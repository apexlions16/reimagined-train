"""
BatchVolumeEditDialog - Batch volume editing dialog.
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


class BatchVolumeEditDialog(QtWidgets.QDialog):
    """Dialog for batch editing volume of multiple files"""
    
    def __init__(self, parent, entries_and_lang, is_mod=False):
        super().__init__(parent)
        self.tr = parent.tr if hasattr(parent, 'tr') else lambda key: key
        self.parent = parent
        self.entries_and_lang = entries_and_lang
        self.is_mod = is_mod
        self.volume_processor = VolumeProcessor()
        self.temp_files = []
        
        self.setWindowTitle(self.tr("batch_volume_editor_title").format(count=len(entries_and_lang)))
        self.setMinimumSize(800, 700)
        
        self.wav_converter = WavToWemConverter(parent)
        self.auto_configure_converter()
        
        self.create_ui()
        QtCore.QTimer.singleShot(100, self.analyze_files)
    
    def auto_configure_converter(self):
        try:
            if hasattr(self.parent, 'wwise_path_edit') and hasattr(self.parent, 'converter_project_path_edit'):
                wwise_path = self.parent.wwise_path_edit.text()
                project_path = self.parent.converter_project_path_edit.text()
                if wwise_path and project_path and os.path.exists(wwise_path):
                    self.wav_converter.set_paths(wwise_path, project_path, tempfile.gettempdir())
                    return True
            wwise_path = self.parent.settings.data.get("wav_wwise_path", "")
            project_path = self.parent.settings.data.get("wav_project_path", "")
            if wwise_path and project_path and os.path.exists(wwise_path):
                self.wav_converter.set_paths(wwise_path, project_path, tempfile.gettempdir())
                return True
            return False
        except:
            return False
    
    def create_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        
        header_text = f"Batch Volume Editor - {len(self.entries_and_lang)} files"
        if self.is_mod:
            header_text += " (MOD versions)"
        else:
            header_text += " (Original versions)"
            
        header = QtWidgets.QLabel(header_text)
        header.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px;")
        layout.addWidget(header)
        
        if not self.volume_processor.is_available():
            error_label = QtWidgets.QLabel(self.tr("volume_deps_missing"))
            error_label.setStyleSheet("color: red; padding: 20px; font-size: 14px;")
            layout.addWidget(error_label)
            close_btn = QtWidgets.QPushButton(self.tr("close"))
            close_btn.clicked.connect(self.reject)
            layout.addWidget(close_btn)
            return
        
        if self.wav_converter.wwise_path and self.wav_converter.project_path:
            config_status = QtWidgets.QLabel(self.tr("wwise_configured_auto"))
            config_status.setStyleSheet("color: green; font-weight: bold; padding: 5px;")
        else:
            config_status = QtWidgets.QLabel(self.tr("wwise_not_configured_warning"))
            config_status.setStyleSheet("color: red; font-weight: bold; padding: 5px;")
        layout.addWidget(config_status)
        backup_info_widget = QtWidgets.QWidget()
        backup_info_layout = QtWidgets.QHBoxLayout(backup_info_widget)

        backup_icon = QtWidgets.QLabel("💾")
        backup_text = QtWidgets.QLabel(self.tr("backups_stored_in").format(path=os.path.join(self.parent.base_path, '.backups', 'audio')))
        backup_text.setStyleSheet("color: #666; font-size: 11px;")

        backup_info_layout.addWidget(backup_icon)
        backup_info_layout.addWidget(backup_text)
        backup_info_layout.addStretch()

        layout.addWidget(backup_info_widget)

        control_group = QtWidgets.QGroupBox(self.tr("volume_control_all_files_group"))

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
        self.volume_label.setStyleSheet("font-weight: bold; font-size: 16px;")
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
            ("25%", 25), ("50%", 50), ("75%", 75), ("100%", 100),
            ("150%", 150), ("200%", 200), ("300%", 300), ("500%", 500)
        ]
        
        for text, value in preset_buttons:
            btn = QtWidgets.QPushButton(text)
            btn.setMaximumWidth(60)
            btn.clicked.connect(lambda checked, v=value: self.set_volume(v))
            presets_layout.addWidget(btn)
        
        presets_layout.addStretch()
        control_layout.addWidget(presets_widget)
        
        layout.addWidget(control_group)
        
        files_group = QtWidgets.QGroupBox(self.tr("files_to_process_group"))
        files_layout = QtWidgets.QVBoxLayout(files_group)
        
        self.files_table = QtWidgets.QTableWidget()
        self.files_table.setColumnCount(6)
        self.files_table.setHorizontalHeaderLabels([
            self.tr("file_header"), self.tr("language_header"), self.tr("current_rms_header"), 
            self.tr("current_peak_header"), self.tr("new_preview_header"), self.tr("status_header")
        ])

        header = self.files_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for i in range(1, 6):
            header.setSectionResizeMode(i, QtWidgets.QHeaderView.ResizeToContents)
        
        self.files_table.setAlternatingRowColors(True)
        files_layout.addWidget(self.files_table)
        
        layout.addWidget(files_group)
        
        self.progress_widget = QtWidgets.QWidget()
        self.progress_widget.hide()
        progress_layout = QtWidgets.QVBoxLayout(self.progress_widget)
        
        self.progress_label = QtWidgets.QLabel("Processing...")
        progress_layout.addWidget(self.progress_label)
        
        self.progress_bar = QtWidgets.QProgressBar()
        progress_layout.addWidget(self.progress_bar)
        
        self.current_file_label = QtWidgets.QLabel("")
        progress_layout.addWidget(self.current_file_label)
        
        layout.addWidget(self.progress_widget)
        
        buttons_widget = QtWidgets.QWidget()
        buttons_layout = QtWidgets.QHBoxLayout(buttons_widget)
        
        buttons_layout.addStretch()
        
        self.process_btn = QtWidgets.QPushButton(self.tr("apply_to_all_btn"))
        self.process_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 10px 20px;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.process_btn.clicked.connect(self.process_all_files)
        
        cancel_btn = QtWidgets.QPushButton(self.tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        
        buttons_layout.addWidget(self.process_btn)
        buttons_layout.addWidget(cancel_btn)
        
        layout.addWidget(buttons_widget)
        
        self.file_analyses = []
    
    def analyze_files(self):
        """Analyze all files"""
        self.files_table.setRowCount(len(self.entries_and_lang))
        self.file_analyses = []
        
        for i, (entry, lang) in enumerate(self.entries_and_lang):
            self.files_table.setItem(i, 0, QtWidgets.QTableWidgetItem(entry.get('ShortName', '')))
            self.files_table.setItem(i, 1, QtWidgets.QTableWidgetItem(lang))
            self.files_table.setItem(i, 5, QtWidgets.QTableWidgetItem("Analyzing..."))
            
            try:
                file_id = entry.get("Id", "")
                if self.is_mod:
                   
                    wem_path = self.parent.get_mod_path(file_id, lang)
                    if not wem_path or not os.path.exists(wem_path):
                       
                        if lang != "SFX":
                            wem_path = os.path.join(
                                self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", 
                                "Windows", "Media", lang, f"{file_id}.wem"
                            )
                        else:
                            wem_path = os.path.join(
                                self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", 
                                "Windows", "Media", f"{file_id}.wem"
                            )
                else:
                    wem_path = self.parent.get_original_path(file_id, lang)
                
                if wem_path and os.path.exists(wem_path):
                    temp_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
                    self.temp_files.append(temp_wav)
                    
                    ok, err = self.parent.wem_to_wav_vgmstream(wem_path, temp_wav)
                    if ok:
                        analysis = self.volume_processor.analyze_audio(temp_wav)
                        if analysis:
                            self.file_analyses.append(analysis)
                            self.files_table.setItem(i, 2, QtWidgets.QTableWidgetItem(f"{analysis['rms_percent']:.1f}%"))
                            self.files_table.setItem(i, 3, QtWidgets.QTableWidgetItem(f"{analysis['peak_percent']:.1f}%"))
                            self.files_table.setItem(i, 5, QtWidgets.QTableWidgetItem("Ready"))
                            continue
                
                self.file_analyses.append(None)
                self.files_table.setItem(i, 2, QtWidgets.QTableWidgetItem("N/A"))
                self.files_table.setItem(i, 3, QtWidgets.QTableWidgetItem("N/A"))
                self.files_table.setItem(i, 5, QtWidgets.QTableWidgetItem("Error"))
                
            except Exception as e:
                self.file_analyses.append(None)
                self.files_table.setItem(i, 5, QtWidgets.QTableWidgetItem("Error"))
        
        self.update_preview_all()
    
    def on_volume_changed(self, value):
        self.volume_label.setText(f"{value}%")
        self.volume_spin.blockSignals(True)
        self.volume_spin.setValue(value)
        self.volume_spin.blockSignals(False)
        self.update_preview_all()
    
    def on_spin_changed(self, value):
        self.volume_slider.blockSignals(True)
        if value > self.volume_slider.maximum():
            self.volume_slider.setMaximum(value + 100)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)
        self.volume_label.setText(f"{value}%")
        self.update_preview_all()
    
    def set_volume(self, value):
        if value > self.volume_slider.maximum():
            self.volume_slider.setMaximum(value + 100)
        self.volume_slider.setValue(value)
    
    def update_preview_all(self):
        volume = self.volume_slider.value()
        
        for i, analysis in enumerate(self.file_analyses):
            if analysis:
                new_rms = analysis['rms_percent'] * (volume / 100)
                new_peak = analysis['peak_percent'] * (volume / 100)
                
                preview_text = f"RMS {new_rms:.1f}%, Peak {new_peak:.1f}%"
                if new_peak > 100:
                    preview_text += " ⚠️"
                
                preview_item = QtWidgets.QTableWidgetItem(preview_text)
                if new_peak > 100:
                    preview_item.setBackground(QtGui.QColor(255, 200, 200))
                elif new_peak > 95:
                    preview_item.setBackground(QtGui.QColor(255, 240, 200))
                else:
                    preview_item.setBackground(QtGui.QColor(200, 255, 200))
                
                self.files_table.setItem(i, 4, preview_item)
            else:
                self.files_table.setItem(i, 4, QtWidgets.QTableWidgetItem("N/A"))
    
    def process_all_files(self):
        volume = self.volume_slider.value()
        
        if volume == 100:
            QtWidgets.QMessageBox.information(self, "No Change", "Volume is set to 100% (no change).")
            return
        
        if not self.wav_converter.wwise_path or not self.wav_converter.project_path:
            QtWidgets.QMessageBox.warning(self, "Configuration Required", self.tr("wwise_config_required_msg"))
            return
        
        self.progress_widget.show()
        self.process_btn.setEnabled(False)
        
        thread = threading.Thread(target=self._process_all_thread, args=(volume,))
        thread.daemon = True
        thread.start()
    
    def _process_all_thread(self, volume):
        """Process all files in thread"""
        try:
            total_files = len(self.entries_and_lang)
            successful = 0
            failed = 0
            
            for i, (entry, lang) in enumerate(self.entries_and_lang):
                if self.file_analyses[i] is None:
                    failed += 1
                    QtCore.QMetaObject.invokeMethod(
                        self, "update_file_status",
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(int, i),
                        QtCore.Q_ARG(str, self.tr("status_skipped_no_analysis"))
                    )
                    continue
                
                progress = int((i / total_files) * 100)
                file_name = entry.get('ShortName', f'File {i+1}')
                shortname = entry.get("ShortName", "")
                original_filename = os.path.splitext(shortname)[0]
                file_id = entry.get("Id", "")
                
                QtCore.QMetaObject.invokeMethod(
                    self, "update_progress",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(int, progress),
                    QtCore.Q_ARG(str, f"Processing {i+1}/{total_files}"),
                    QtCore.Q_ARG(str, file_name)
                )
                
                try:
                    if self.is_mod:
                        
                        current_mod_path = self.parent.get_mod_path(file_id, lang)
                        
                        if not current_mod_path or not os.path.exists(current_mod_path):
                            raise Exception(f"Modified audio file not found for {file_name}")
                        
                        backup_path = self.parent.get_backup_path(file_id, lang)
                        
                        if os.path.exists(backup_path):
                            source_wem_path = backup_path
                        else:
                            backup_dir = os.path.dirname(backup_path)
                            os.makedirs(backup_dir, exist_ok=True)
                            shutil.copy2(current_mod_path, backup_path)
                            source_wem_path = backup_path
                    else:
                        source_wem_path = self.parent.get_original_path(file_id, lang)
                        
                        if not os.path.exists(source_wem_path):
                            raise Exception(f"Original WEM file not found: {source_wem_path}")
                    
                    temp_wav_original = tempfile.NamedTemporaryFile(suffix=f'_{original_filename}_original.wav', delete=False).name
                    self.temp_files.append(temp_wav_original)
                    
                    ok, err = self.parent.wem_to_wav_vgmstream(source_wem_path, temp_wav_original)
                    if not ok:
                        raise Exception(f"WEM to WAV conversion failed: {err}")
                    
                    temp_wav_adjusted = tempfile.NamedTemporaryFile(suffix=f'_{original_filename}_adjusted.wav', delete=False).name
                    self.temp_files.append(temp_wav_adjusted)
                    
                    success, result = self.volume_processor.change_volume(
                        temp_wav_original,
                        temp_wav_adjusted,
                        volume
                    )
                    
                    if not success:
                        raise Exception(f"Volume adjustment failed: {result}")
                    
                    temp_dir = tempfile.mkdtemp(prefix=f"batch_volume_{i}_")
                    self.temp_files.append(temp_dir)
                    
                    final_wav_for_wwise = os.path.join(temp_dir, f"{original_filename}.wav")
                    shutil.copy2(temp_wav_adjusted, final_wav_for_wwise)
                    
                    original_wem_size = os.path.getsize(source_wem_path)
                    
                    file_pair = {
                        "wav_file": final_wav_for_wwise,
                        "target_wem": source_wem_path,
                        "wav_name": f"{original_filename}.wav",
                        "target_name": f"{original_filename}.wem",
                        "target_size": original_wem_size,
                        "language": lang,
                        "file_id": file_id
                    }
                    
                    temp_output = os.path.join(temp_dir, "output")
                    os.makedirs(temp_output, exist_ok=True)
                    self.wav_converter.output_folder = temp_output
                    
                    conversion_result = self.wav_converter.convert_single_file_main(file_pair, i+1, total_files)
                    
                    if not conversion_result.get('success'):
                        raise Exception(f"WEM conversion failed: {conversion_result.get('error', 'Unknown error')}")
                    
                    output_wem = conversion_result['output_path']
                    
                    if lang != "SFX":
                        target_dir = os.path.join(
                            self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", 
                            "Windows", "Media", lang
                        )
                    else:
                        target_dir = os.path.join(
                            self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", 
                            "Windows", "Media"
                        )
                    
                    os.makedirs(target_dir, exist_ok=True)
                    target_path = os.path.join(target_dir, f"{file_id}.wem")
                    
                    shutil.copy2(output_wem, target_path)
                    successful += 1
                    
                    QtCore.QMetaObject.invokeMethod(
                        self, "update_file_status",
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(int, i),
                        QtCore.Q_ARG(str, f"✓ {volume}%")
                    )
                    
                except Exception as e:
                    failed += 1
                    error_msg = str(e)
                    if len(error_msg) > 50:
                        error_msg = error_msg[:47] + "..."
                    
                    QtCore.QMetaObject.invokeMethod(
                        self, "update_file_status",
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(int, i),
                        QtCore.Q_ARG(str, f"✗ {error_msg}")
                    )
                    DEBUG.log(f"Error processing {file_name}: {str(e)}", "ERROR")
            
            QtCore.QMetaObject.invokeMethod(
                self, "processing_complete",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(int, successful),
                QtCore.Q_ARG(int, failed),
                QtCore.Q_ARG(int, volume)
            )
            
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(
                self, "show_error",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, str(e))
            )

    @QtCore.pyqtSlot(int, str, str)
    def update_progress(self, progress, main_text, current_file):
        self.progress_bar.setValue(progress)
        self.progress_label.setText(main_text)
        self.current_file_label.setText(current_file)

    @QtCore.pyqtSlot(int, str)
    def update_file_status(self, row, status):
        if row < self.files_table.rowCount():
            self.files_table.setItem(row, 5, QtWidgets.QTableWidgetItem(status))

    @QtCore.pyqtSlot(int, int, int)
    def processing_complete(self, successful, failed, volume):
        self.progress_widget.hide()
        self.process_btn.setEnabled(True)
        
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    if os.path.isdir(temp_file):
                        shutil.rmtree(temp_file)
                    else:
                        os.remove(temp_file)
            except Exception as e:
                DEBUG.log(f"Failed to clean up temp file {temp_file}: {e}", "WARNING")
        
        message = self.tr("batch_process_complete_msg").format(volume=volume, successful=successful, failed=failed)
        QtWidgets.QMessageBox.information(self, self.tr("batch_process_complete_title"), message)
        
        for lang in set(lang for _, lang in self.entries_and_lang):
            if hasattr(self.parent, 'populate_tree'):
                self.parent.populate_tree(lang)
        
        self.accept()

    @QtCore.pyqtSlot(str)
    def show_error(self, error):
        self.progress_widget.hide()
        self.process_btn.setEnabled(True)
        QtWidgets.QMessageBox.critical(self, self.tr("batch_process_error_title"), error)
