"""
WemVolumeEditDialog - WEM volume editing dialog.
"""
import sys
import os
import json
import subprocess
import tempfile
import shutil
import threading
import csv
import traceback
import time
from functools import partial
from datetime import datetime
from PyQt5 import QtWidgets, QtCore, QtGui, QtMultimedia
from PyQt5.QtCore import QObject, pyqtSignal, QThread
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
import struct
from collections import namedtuple
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from editor.constants import (
    CuePoint, Label, startupinfo, CREATE_NO_WINDOW, current_version,
    MATPLOTLIB_AVAILABLE, PSUTIL_AVAILABLE
)
from editor.translations import TRANSLATIONS, tr


class WemVolumeEditDialog(QtWidgets.QDialog):
    """Dialog for editing WEM file volume"""
    
    def __init__(self, parent, entry, lang, is_mod=False):
        super().__init__(parent)
        self.tr = parent.tr if hasattr(parent, 'tr') else lambda key: key
        self.parent = parent
        self.entry = entry
        self.lang = lang
        self.is_mod = is_mod
        self.volume_processor = VolumeProcessor()
        self.temp_files = []
        self.current_analysis = None
        
        self.setWindowTitle(self.tr("volume_editor_title").format(shortname=entry.get('ShortName', '')))
        self.setMinimumSize(600, 500)
        
        self.wav_converter = WavToWemConverter(parent)
        self.auto_configure_converter()
        
        self.create_ui()
        QtCore.QTimer.singleShot(100, self.analyze_wem_file)

    def auto_configure_converter(self):
        """Automatically configure converter from parent settings"""
        try:
            if hasattr(self.parent, 'wwise_path_edit') and hasattr(self.parent, 'converter_project_path_edit'):
                wwise_path = self.parent.wwise_path_edit.text()
                project_path = self.parent.converter_project_path_edit.text()
                
                if wwise_path and project_path and os.path.exists(wwise_path):
                    self.wav_converter.set_paths(wwise_path, project_path, tempfile.gettempdir())
                    DEBUG.log(f"Auto-configured Wwise: {wwise_path}")
                    return True
            
            wwise_path = self.parent.settings.data.get("wav_wwise_path", "")
            project_path = self.parent.settings.data.get("wav_project_path", "")
            
            if wwise_path and project_path and os.path.exists(wwise_path):
                self.wav_converter.set_paths(wwise_path, project_path, tempfile.gettempdir())
                DEBUG.log(f"Auto-configured Wwise from settings: {wwise_path}")
                return True
                
            DEBUG.log("Could not auto-configure Wwise - no valid paths found", "WARNING")
            return False
            
        except Exception as e:
            DEBUG.log(f"Error auto-configuring Wwise: {e}", "ERROR")
            return False    

    def create_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        
        header_text = f"Adjusting volume for: {self.entry.get('ShortName', '')}"
        if self.is_mod:
            header_text += " (MOD version)"
        else:
            header_text += " (Original version)"
            
        header = QtWidgets.QLabel(header_text)
        header.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(header)
        
        if not self.volume_processor.is_available():
            error_widget = QtWidgets.QWidget()
            error_layout = QtWidgets.QVBoxLayout(error_widget)
            error_layout.setContentsMargins(20, 20, 20, 20)
            
            error_label = QtWidgets.QLabel(self.tr("volume_deps_missing"))
            error_label.setStyleSheet("color: red; font-size: 14px;")
            error_layout.addWidget(error_label)
            
            close_btn = QtWidgets.QPushButton("Close")
            close_btn.clicked.connect(self.reject)
            error_layout.addWidget(close_btn)
            
            layout.addWidget(error_widget)
            return
        
        analysis_group = QtWidgets.QGroupBox(self.tr("audio_analysis_group"))
        analysis_layout = QtWidgets.QFormLayout(analysis_group)
        
        self.current_rms_label = QtWidgets.QLabel(self.tr("analyzing"))
        self.current_peak_label = QtWidgets.QLabel(self.tr("analyzing"))
        self.duration_label = QtWidgets.QLabel(self.tr("analyzing"))

        self.max_safe_label = QtWidgets.QLabel(self.tr("no_limit"))
        
        analysis_layout.addRow(self.tr("current_rms"), self.current_rms_label)
        analysis_layout.addRow(self.tr("current_peak"), self.current_peak_label)
        analysis_layout.addRow(self.tr("duration_label"), self.duration_label)
        analysis_layout.addRow(self.tr("recommended_max"), self.max_safe_label)
        
        layout.addWidget(analysis_group)
        
        control_group = QtWidgets.QGroupBox(self.tr("volume_control_group"))
        control_layout = QtWidgets.QVBoxLayout(control_group)
        
        slider_widget = QtWidgets.QWidget()
        slider_layout = QtWidgets.QHBoxLayout(slider_widget)
        
        slider_layout.addWidget(QtWidgets.QLabel(self.tr("volume_label_simple")))
        
        self.volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(1000) 
        self.volume_slider.setValue(100)
        self.volume_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.volume_slider.setTickInterval(100)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        slider_layout.addWidget(self.volume_slider, 1)
        
        self.volume_label = QtWidgets.QLabel("100%")
        self.volume_label.setMinimumWidth(80)
        self.volume_label.setAlignment(QtCore.Qt.AlignCenter)
        self.volume_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        slider_layout.addWidget(self.volume_label)
        
        self.volume_spin = QtWidgets.QSpinBox()
        self.volume_spin.setMinimum(0)
        self.volume_spin.setMaximum(9999)  
        self.volume_spin.setValue(100)
        self.volume_spin.setSuffix("%")
        self.volume_spin.valueChanged.connect(self.on_spin_changed)
        slider_layout.addWidget(self.volume_spin)
        
        control_layout.addWidget(slider_widget)
        
        presets_widget = QtWidgets.QWidget()
        presets_layout = QtWidgets.QHBoxLayout(presets_widget)
        presets_layout.addWidget(QtWidgets.QLabel(self.tr("quick_presets")))
        
        preset_buttons = [
            ("25%", 25),
            ("50%", 50),
            ("75%", 75),
            ("100%", 100),
            ("150%", 150),
            ("200%", 200),
            ("300%", 300),
            ("500%", 500),
            ("1000%", 1000)
        ]
        
        for text, value in preset_buttons:
            btn = QtWidgets.QPushButton(text)
            btn.setMaximumWidth(60)
            btn.clicked.connect(lambda checked, v=value: self.set_volume(v))
            presets_layout.addWidget(btn)
        
        presets_layout.addStretch()
        control_layout.addWidget(presets_widget)
        
        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setStyleSheet("padding: 10px; border: 1px solid #5a5d5f; border-radius: 5px;")
        control_layout.addWidget(self.preview_label)
        self.update_preview()
        
        layout.addWidget(control_group)
        
        self.progress_widget = QtWidgets.QWidget()
        self.progress_widget.hide()
        progress_layout = QtWidgets.QVBoxLayout(self.progress_widget)
        
        self.progress_label = QtWidgets.QLabel("Processing...")
        progress_layout.addWidget(self.progress_label)
        
        self.progress_bar = QtWidgets.QProgressBar()
        progress_layout.addWidget(self.progress_bar)
        
        self.status_text = QtWidgets.QTextEdit()
        self.status_text.setMaximumHeight(100)
        self.status_text.setReadOnly(True)
        progress_layout.addWidget(self.status_text)
        
        layout.addWidget(self.progress_widget)
        
        buttons_widget = QtWidgets.QWidget()
        buttons_layout = QtWidgets.QHBoxLayout(buttons_widget)
        
        buttons_layout.addStretch()
        
        self.process_btn = QtWidgets.QPushButton(self.tr("apply_volume_change_btn"))
        self.process_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.process_btn.clicked.connect(self.process_volume_change)
        
        cancel_btn = QtWidgets.QPushButton(self.tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        
        buttons_layout.addWidget(self.process_btn)
        buttons_layout.addWidget(cancel_btn)
        
        layout.addWidget(buttons_widget)
    
    def analyze_wem_file(self):
        """Analyze the WEM file"""
        if not self.volume_processor.is_available():
            return 
        
        try:
            file_id = self.entry.get("Id", "")
            if self.is_mod:
  
                wem_path = self.parent.get_mod_path(file_id, self.lang)
                if not wem_path or not os.path.exists(wem_path):
                 
                    if self.lang != "SFX":
                        wem_path = os.path.join(
                            self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", 
                            "Windows", "Media", self.lang, f"{file_id}.wem"
                        )
                    else:
                        wem_path = os.path.join(
                            self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", 
                            "Windows", "Media", f"{file_id}.wem"
                        )
            else:
                wem_path = self.parent.get_original_path(file_id, self.lang)
            
            if not wem_path or not os.path.exists(wem_path):
                self.current_rms_label.setText("File not found")
                DEBUG.log(f"WemVolumeEditDialog: File not found at {wem_path}", "WARNING")
                return
            
            temp_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
            self.temp_files.append(temp_wav)
            
            ok, err = self.parent.wem_to_wav_vgmstream(wem_path, temp_wav)
            if not ok:
                self.current_rms_label.setText("Conversion error")
                return
            
            self.current_analysis = self.volume_processor.analyze_audio(temp_wav)
            if self.current_analysis:
                self.current_rms_label.setText(f"{self.current_analysis['rms_percent']:.1f}%")
                self.current_peak_label.setText(f"{self.current_analysis['peak_percent']:.1f}%")
                self.duration_label.setText(f"{self.current_analysis['duration_seconds']:.2f} seconds")
                
                safe_max = int(self.current_analysis['max_increase'])
                self.max_safe_label.setText(f"{safe_max}% (for no clipping)")
                
            else:
                self.current_rms_label.setText("Analysis failed")
                
        except Exception as e:
            DEBUG.log(f"Error analyzing WEM: {e}", "ERROR")
            self.current_rms_label.setText("Error")
    
    def on_volume_changed(self, value):
        self.volume_label.setText(f"{value}%")
        self.volume_spin.blockSignals(True)
        self.volume_spin.setValue(value)
        self.volume_spin.blockSignals(False)
        self.update_preview()
    
    def on_spin_changed(self, value):
        self.volume_slider.blockSignals(True)
        if value > self.volume_slider.maximum():
            self.volume_slider.setMaximum(value + 100)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)
        self.volume_label.setText(f"{value}%")
        self.update_preview()
    
    def set_volume(self, value):
        if value > self.volume_slider.maximum():
            self.volume_slider.setMaximum(value + 100)
        self.volume_slider.setValue(value)
    
    def update_preview(self):
        if not self.current_analysis:
            self.preview_label.setText(self.tr("waiting_for_analysis"))
            return
            
        volume = self.volume_slider.value()
        
        new_rms = self.current_analysis['rms_percent'] * (volume / 100)
        new_peak = self.current_analysis['peak_percent'] * (volume / 100)
        
        preview_text = self.tr("preview_rms_peak").format(new_rms=new_rms, new_peak=new_peak)
        is_dark_theme = self.parent.settings.data.get("theme", "light") == "dark"

        base_style = "padding: 10px; border-radius: 5px;"
        if new_peak > 100:
            preview_text += self.tr("preview_clipping").format(over=new_peak - 100)
            bg_color = "#5a1d1d" if is_dark_theme else "#ffcccc"
            text_color = "#ff8a80" if is_dark_theme else "red"
            self.preview_label.setStyleSheet(f"{base_style} background-color: {bg_color}; color: {text_color};")
        elif new_peak > 95:
            preview_text += self.tr("preview_near_clipping")
            bg_color = "#6b4f1b" if is_dark_theme else "#fff0cc"
            text_color = "#ffd54f" if is_dark_theme else "orange"
            self.preview_label.setStyleSheet(f"{base_style} background-color: {bg_color}; color: {text_color};")
        else:
            bg_color = "#1e4e24" if is_dark_theme else "#ccffcc"
            text_color = "#a5d6a7" if is_dark_theme else "green"
            self.preview_label.setStyleSheet(f"{base_style} background-color: {bg_color}; color: {text_color};")

        self.preview_label.setText(preview_text)
    
    def process_volume_change(self):
        volume = self.volume_slider.value()
        
        if volume == 100:
            QtWidgets.QMessageBox.information(
                self, self.tr("no_change"),
                self.tr("volume_no_change_msg")
            )
            return
        
        if not self.wav_converter.wwise_path or not self.wav_converter.project_path:
            QtWidgets.QMessageBox.warning(
                self, self.tr("config_required"),
                self.tr("wwise_config_required_msg")
            )
            return
        
        self.progress_widget.show()
        self.process_btn.setEnabled(False)
        
        thread = threading.Thread(target=self._process_thread, args=(volume,))
        thread.daemon = True
        thread.start()

    def _process_thread(self, volume):
        """Process volume change in thread"""
        try:
            self.update_progress(10, self.tr("status_preparing"))
            
            file_id = self.entry.get("Id", "")
            shortname = self.entry.get("ShortName", "")
            original_filename = os.path.splitext(shortname)[0]
            
            if self.is_mod:
                # FIX: Use get_mod_path helper to correctly locate the source file
                current_mod_path = self.parent.get_mod_path(file_id, self.lang)
                
                if not current_mod_path or not os.path.exists(current_mod_path):
                    raise Exception("Modified audio file not found. Try reverting to original first.")
                
                backup_path = self.parent.get_backup_path(file_id, self.lang)
                
                if os.path.exists(backup_path):
                    source_wem_path = backup_path
                    self.update_progress(15, self.tr("status_using_backup"))
                    DEBUG.log(f"Using backup as source: {backup_path}")
                else:
                    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                    shutil.copy2(current_mod_path, backup_path)
                    source_wem_path = backup_path
                    self.update_progress(15, self.tr("status_backup_created"))
                    DEBUG.log(f"Created backup from current mod: {backup_path}")
            else:
                source_wem_path = self.parent.get_original_path(file_id, self.lang)
                if not os.path.exists(source_wem_path):
                    raise Exception(f"Original WEM file not found: {source_wem_path}")
                self.update_progress(15, self.tr("status_using_original"))
            
            self.update_progress(20, self.tr("status_converting_to_wav"))
            
            temp_wav_original = tempfile.NamedTemporaryFile(
                suffix=f'_{original_filename}_source.wav', 
                delete=False
            ).name
            self.temp_files.append(temp_wav_original)
            
            ok, err = self.parent.wem_to_wav_vgmstream(source_wem_path, temp_wav_original)
            if not ok:
                raise Exception(f"WEM to WAV conversion failed: {err}")
            
            self.update_progress(40, self.tr("status_adjusting_volume"))
            
            temp_wav_adjusted = tempfile.NamedTemporaryFile(
                suffix=f'_{original_filename}_vol{volume}.wav', 
                delete=False
            ).name
            self.temp_files.append(temp_wav_adjusted)
            
            success, result = self.volume_processor.change_volume(
                temp_wav_original,
                temp_wav_adjusted,
                volume
            )
            
            if not success:
                raise Exception(f"Volume adjustment failed: {result}")
            
            self.update_progress(60, self.tr("status_preparing_for_wem"))
            
            temp_dir = tempfile.mkdtemp(prefix="volume_adjust_")
            self.temp_files.append(temp_dir)
            
            final_wav_for_wwise = os.path.join(temp_dir, f"{original_filename}.wav")
            shutil.copy2(temp_wav_adjusted, final_wav_for_wwise)
            
            # Use source size as target if we are editing original, or if preserving mod size
            target_size = os.path.getsize(source_wem_path)
            
            file_pair = {
                "wav_file": final_wav_for_wwise,
                "target_wem": source_wem_path,
                "wav_name": f"{original_filename}.wav",
                "target_name": f"{original_filename}.wem",
                "target_size": target_size,
                "language": self.lang,
                "file_id": file_id
            }
            
            if not self.wav_converter.wwise_path:
                raise Exception("Wwise not configured. Please check configuration.")
            
            temp_output = os.path.join(temp_dir, "output")
            os.makedirs(temp_output, exist_ok=True)
            self.wav_converter.output_folder = temp_output
            
            self.update_progress(70, self.tr("status_converting_to_wem"))
            
            conversion_result = self.wav_converter.convert_single_file_main(file_pair, 1, 1)
            
            if not conversion_result.get('success'):
                error_msg = conversion_result.get('error', 'Unknown error')
                raise Exception(f"WEM conversion failed: {error_msg}")
            
            self.update_progress(85, self.tr("status_deploying_to_mod"))
            
            # FIX: Use correct deployment path with Media folder
            if self.lang != "SFX":
                target_dir = os.path.join(
                    self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", 
                    "Windows", "Media", self.lang
                )
            else:
                target_dir = os.path.join(
                    self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", 
                    "Windows", "Media"
                )
            
            os.makedirs(target_dir, exist_ok=True)
            target_path = os.path.join(target_dir, f"{file_id}.wem")
            
            output_wem = conversion_result['output_path']
            shutil.copy2(output_wem, target_path)
            
            try:
                if os.path.exists(temp_output):
                    shutil.rmtree(temp_output)
            except Exception as e:
                DEBUG.log(f"Warning: Failed to cleanup temp output: {e}", "WARNING")
            
            self.update_progress(100, self.tr("status_complete"))
            
            clipping_info = ""
            if result.get('clipped_percent', 0) > 0:
                clipping_info = self.tr("clipping_info_text").format(percent=result['clipped_percent'])

            backup_info = ""
            if self.parent.has_backup(file_id, self.lang):
                backup_info = self.tr("backup_available_info")

            source_info = ""
            if self.is_mod:
                if os.path.exists(self.parent.get_backup_path(file_id, self.lang)):
                    source_info = self.tr("source_info_backup")
                else:
                    source_info = self.tr("source_info_mod_backup_created")
            else:
                source_info = self.tr("source_info_original")

            success_message = self.tr("volume_change_success_msg").format(
                volume=volume,
                actual_change=result.get('actual_change', volume),
                clipping_info=clipping_info,
                source_info=source_info,
                backup_info=backup_info
            )
            
            QtCore.QMetaObject.invokeMethod(
                self, "show_success",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, success_message)
            )
            
        except Exception as e:
            error_message = str(e)
            
            if "Failed to create WEM file" in error_message or "No acceptable result found" in error_message:
                error_message = self.tr("wem_conversion_failed_msg").format(error_message=error_message)
            elif "Wwise not configured" in error_message:
                error_message = self.tr("wwise_not_configured_msg")
            elif "not found" in error_message.lower():
                error_message = self.tr("required_file_not_found_msg").format(error_message=error_message)
            
            QtCore.QMetaObject.invokeMethod(
                self, "show_error",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, error_message)
            )
        
        finally:
            for temp_file in self.temp_files:
                try:
                    if os.path.exists(temp_file):
                        if os.path.isdir(temp_file):
                            shutil.rmtree(temp_file)
                        else:
                            os.remove(temp_file)
                except Exception as e:
                    DEBUG.log(f"Warning: Failed to cleanup temp file {temp_file}: {e}", "WARNING")  

    def update_progress(self, value, text):
        QtCore.QMetaObject.invokeMethod(
            self.progress_bar, "setValue",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(int, value)
        )
        QtCore.QMetaObject.invokeMethod(
            self.progress_label, "setText",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, text)
        )
        QtCore.QMetaObject.invokeMethod(
            self.status_text, "append",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, f"[{value}%] {text}")
        )
    
    @QtCore.pyqtSlot(str)
    def show_success(self, message):
        self.progress_widget.hide()
        self.process_btn.setEnabled(True)
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
        QtWidgets.QMessageBox.information(self, "Success", message)
        if hasattr(self.parent, 'populate_tree'):
            self.parent.populate_tree(self.lang)
        self.accept()
    
    @QtCore.pyqtSlot(str)
    def show_error(self, error):
        self.progress_widget.hide()
        self.process_btn.setEnabled(True)
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
        QtWidgets.QMessageBox.critical(self, self.tr("error"), f"{self.tr('volume_change_failed_title')}:\n\n{error}")
