"""
AudioTrimDialog and WaveformWidget - Audio trimming dialog.
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


class AudioTrimDialog(QtWidgets.QDialog):
    
    def __init__(self, parent, entry, lang, is_mod=False):
        super().__init__(parent)
        self.tr = parent.tr if hasattr(parent, 'tr') else lambda key: key
        self.parent = parent
        self.entry = entry
        self.lang = lang
        self.is_mod = is_mod
        self.temp_files = []
        self.source_wav = None
        self.start_ms = 0
        self.end_ms = 0
        
        self.setWindowTitle(self.tr("trim_editor_title").format(shortname=entry.get('ShortName', ''))) 
        self.setMinimumSize(800, 450)
        
        self.ffmpeg_path = AudioToWavConverter().find_ffmpeg()
        if not self.ffmpeg_path or not MATPLOTLIB_AVAILABLE:
            msg = self.tr("trim_deps_missing")
            QtWidgets.QMessageBox.critical(self, self.tr("error"), msg)

        self.wav_converter = WavToWemConverter(parent)
        self.auto_configure_converter()

        self.player = QtMultimedia.QMediaPlayer()
        self.player.setNotifyInterval(10)

        self.create_ui()
        QtCore.QTimer.singleShot(100, self.prepare_audio)

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
        
        header_text = self.tr("trimming_audio_for").format(shortname=self.entry.get('ShortName', ''))
        header_text += self.tr("version_mod") if self.is_mod else self.tr("version_original")
        header = QtWidgets.QLabel(header_text)
        header.setStyleSheet("font-size: 14px; font-weight: bold; padding: 5px;")
        layout.addWidget(header)

        self.waveform_widget = WaveformWidget()
        self.waveform_widget.rangeChanged.connect(self.update_times_from_waveform)
        self.waveform_widget.zoomRequested.connect(self.on_wheel_zoom)
        self.waveform_widget.seekRequested.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.waveform_widget.set_playhead)
        layout.addWidget(self.waveform_widget)

        self.scroll_bar = QtWidgets.QScrollBar(QtCore.Qt.Horizontal)
        self.scroll_bar.valueChanged.connect(self.on_scroll)
        layout.addWidget(self.scroll_bar)

        zoom_widget = QtWidgets.QWidget()
        zoom_layout = QtWidgets.QHBoxLayout(zoom_widget)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom_slider.setRange(0, 100)
        self.zoom_slider.valueChanged.connect(self.on_zoom)
        zoom_layout.addWidget(QtWidgets.QLabel(self.tr("zoom_label")))
        zoom_layout.addWidget(self.zoom_slider)
        layout.addWidget(zoom_widget)

        time_widget = QtWidgets.QWidget()
        time_layout = QtWidgets.QFormLayout(time_widget)
        self.start_time_edit = QtWidgets.QSpinBox()
        self.start_time_edit.setSuffix(" ms")
        self.start_time_edit.editingFinished.connect(self.update_waveform_from_times)
        self.end_time_edit = QtWidgets.QSpinBox()
        self.end_time_edit.setSuffix(" ms")
        self.end_time_edit.editingFinished.connect(self.update_waveform_from_times)
        self.duration_label = QtWidgets.QLabel("New Duration: 0.000 s")
        self.duration_label.setStyleSheet("font-weight: bold;")
        time_layout.addRow(self.tr("start_time_label"), self.start_time_edit)
        time_layout.addRow(self.tr("end_time_label"), self.end_time_edit)
        time_layout.addRow(self.tr("new_duration_label"), self.duration_label)
        layout.addWidget(time_widget)

        playback_layout = QtWidgets.QHBoxLayout()
        self.play_btn = QtWidgets.QPushButton(self.tr("play_pause_btn"))
        self.play_btn.clicked.connect(self.toggle_playback)
        self.preview_btn = QtWidgets.QPushButton(self.tr("preview_trim_btn"))
        self.preview_btn.clicked.connect(self.preview_trim)
        self.stop_btn = QtWidgets.QPushButton(self.tr("stop_btn"))
        self.stop_btn.clicked.connect(self.stop_playback)
        playback_layout.addWidget(self.play_btn)
        playback_layout.addWidget(self.preview_btn)
        playback_layout.addWidget(self.stop_btn)
        layout.addLayout(playback_layout)
        self.progress_widget = QtWidgets.QWidget()
        self.progress_widget.hide()
        progress_layout = QtWidgets.QVBoxLayout(self.progress_widget)
        self.progress_label = QtWidgets.QLabel("Processing...")
        self.progress_bar = QtWidgets.QProgressBar()
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_widget)
        buttons_layout = QtWidgets.QHBoxLayout()
        buttons_layout.addStretch()
        self.process_btn = QtWidgets.QPushButton(self.tr("apply_trim_btn"))
        self.process_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.process_btn.clicked.connect(self.process_trim)
        cancel_btn = QtWidgets.QPushButton(self.tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(self.process_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)

    def prepare_audio(self):
        try:
            file_id = self.entry.get("Id", "")
            wem_path = self.parent.get_mod_path(file_id, self.lang) if self.is_mod else self.parent.get_original_path(file_id, self.lang)
            if not os.path.exists(wem_path): raise FileNotFoundError("Audio file not found!")

            temp_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
            self.temp_files.append(temp_wav)
            
            ok, err = self.parent.wem_to_wav_vgmstream(wem_path, temp_wav)
            if not ok: raise Exception(f"WEM to WAV conversion failed: {err}")

            self.source_wav = temp_wav
            self.waveform_widget.set_waveform(self.source_wav)
            
            url = QtCore.QUrl.fromLocalFile(self.source_wav)
            self.player.setMedia(QtMultimedia.QMediaContent(url))
            self.player.durationChanged.connect(self.on_duration_changed)

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, self.tr("error"), self.tr("preparing_audio_failed").format(e=e))
            self.reject()

    def on_duration_changed(self, duration):
        self.waveform_widget.set_duration(duration)
        self.start_time_edit.setRange(0, duration)
        self.end_time_edit.setRange(0, duration)
        self.end_time_edit.setValue(duration)
        self.update_times_from_waveform(0, duration)
        self.on_zoom(0)
    def on_zoom(self, value):
        if self.waveform_widget.duration_ms <= 0: return

        min_view_ms = 50
        max_view_ms = self.waveform_widget.duration_ms
        
        zoom_factor = value / 100.0
        view_duration = int(max_view_ms / (1 + 99 * zoom_factor))
        view_duration = max(min_view_ms, view_duration)

        self.scroll_bar.setPageStep(view_duration)
        self.scroll_bar.setRange(0, self.waveform_widget.duration_ms - view_duration)
        
        current_center = self.waveform_widget.view_start_ms + (self.waveform_widget.view_end_ms - self.waveform_widget.view_start_ms) / 2
        new_start = int(current_center - view_duration / 2)
        
        self.scroll_bar.setValue(new_start)
        self.on_scroll(new_start)

    def on_scroll(self, value):
        view_duration = self.scroll_bar.pageStep()
        self.waveform_widget.set_view_range(value, value + view_duration)
    def on_wheel_zoom(self, delta, mouse_x):
        """Handles zooming with the mouse wheel with smooth, centered scaling."""
        if self.waveform_widget.duration_ms <= 0: return

        zoom_factor = 1.15 if delta > 0 else 1 / 1.15

        current_view_start = self.waveform_widget.view_start_ms
        current_view_end = self.waveform_widget.view_end_ms
        current_view_duration = current_view_end - current_view_start

        time_at_cursor = self.waveform_widget._x_to_ms(mouse_x, current_view_start, current_view_end)

        new_view_duration = current_view_duration / zoom_factor
        
        min_view_ms = 20
        new_view_duration = max(min_view_ms, min(self.waveform_widget.duration_ms, new_view_duration))

        cursor_ratio = (time_at_cursor - current_view_start) / current_view_duration
        new_view_start = time_at_cursor - (new_view_duration * cursor_ratio)
        new_view_end = new_view_start + new_view_duration

        if new_view_start < 0:
            new_view_start = 0
            new_view_end = new_view_duration
        if new_view_end > self.waveform_widget.duration_ms:
            new_view_end = self.waveform_widget.duration_ms
            new_view_start = new_view_end - new_view_duration
        
        self.scroll_bar.setPageStep(int(new_view_duration))
        self.scroll_bar.setRange(0, self.waveform_widget.duration_ms - int(new_view_duration))
        self.scroll_bar.setValue(int(new_view_start))
        
        if new_view_duration >= self.waveform_widget.duration_ms:
            zoom_slider_value = 0
        elif new_view_duration <= min_view_ms:
            zoom_slider_value = 100
        else:
            max_view_ms = self.waveform_widget.duration_ms
            factor = (max_view_ms / new_view_duration - 1) / 99
            zoom_slider_value = int(factor * 100)
            
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(zoom_slider_value)
        self.zoom_slider.blockSignals(False)
    def update_times_from_waveform(self, start_ms, end_ms):
        self.start_ms, self.end_ms = start_ms, end_ms
        self.start_time_edit.blockSignals(True)
        self.end_time_edit.blockSignals(True)
        self.start_time_edit.setValue(start_ms)
        self.end_time_edit.setValue(end_ms)
        self.start_time_edit.blockSignals(False)
        self.end_time_edit.blockSignals(False)
        self.update_duration_label()

    def update_waveform_from_times(self):
        start_ms = self.start_time_edit.value()
        end_ms = self.end_time_edit.value()
        self.waveform_widget.set_selection_range(start_ms, end_ms)
        self.update_times_from_waveform(start_ms, end_ms)
        
    def update_duration_label(self):
        new_duration = self.end_ms - self.start_ms
        self.duration_label.setText(self.tr("new_duration_format").format(duration_sec=new_duration/1000, duration_ms=new_duration))

    def toggle_playback(self):
        if self.player.state() == QtMultimedia.QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop_playback(self):
        self.player.stop()
        self.waveform_widget.set_playhead(0)

    def preview_trim(self):
        self.player.setPosition(self.start_ms)
        self.player.play()
        
        def check_position(position):
            if position >= self.end_ms:
                self.player.stop()
                try: self.player.positionChanged.disconnect(check_position)
                except TypeError: pass
        
        try: self.player.positionChanged.disconnect()
        except TypeError: pass
        finally:
            self.player.positionChanged.connect(check_position)
            self.player.positionChanged.connect(self.waveform_widget.set_playhead)

    def process_trim(self):
        self.progress_widget.show()
        self.process_btn.setEnabled(False)
        
        thread = threading.Thread(target=self._process_thread)
        thread.daemon = True
        thread.start()

    def _process_thread(self):
        try:
            self.update_progress(10, "Preparing...")

            self.update_progress(20, self.tr("trimming_with_ffmpeg"))
            trimmed_wav = tempfile.NamedTemporaryFile(suffix='_trimmed.wav', delete=False).name
            self.temp_files.append(trimmed_wav)
            
            start_sec = self.start_ms / 1000.0
            duration_sec = (self.end_ms - self.start_ms) / 1000.0
            
            cmd = [
                self.ffmpeg_path, '-i', self.source_wav,
                '-ss', str(start_sec), '-t', str(duration_sec),
                '-acodec', 'pcm_s16le',
                '-ar', str(self.waveform_widget.sample_rate), 
                trimmed_wav, '-y'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo, creationflags=CREATE_NO_WINDOW)
            if result.returncode != 0:
                raise Exception(f"FFmpeg trimming failed: {result.stderr}")
            file_id = self.entry.get("Id", "")
            shortname = self.entry.get("ShortName", "")
            original_filename = os.path.splitext(shortname)[0]
            
            if self.is_mod:
                current_mod_path = self.parent.get_mod_path(file_id, self.lang)
                if not os.path.exists(current_mod_path):
                     raise Exception("Modified audio file not found")
                
                backup_path = self.parent.get_backup_path(file_id, self.lang)
                if not os.path.exists(backup_path):
                    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                    shutil.copy2(current_mod_path, backup_path)
            
            source_wem_path = self.parent.get_original_path(file_id, self.lang)
            target_size = os.path.getsize(source_wem_path)

            file_pair = {
                "wav_file": trimmed_wav, "target_wem": source_wem_path,
                "wav_name": f"{original_filename}.wav", "target_name": f"{original_filename}.wem",
                "target_size": target_size, "language": self.lang, "file_id": file_id
            }

            self.update_progress(60, "Converting to WEM...")
            conversion_result = self.wav_converter.convert_single_file_main(file_pair, 1, 1)

            if not conversion_result.get('success'):
                raise Exception(f"WEM conversion failed: {conversion_result.get('error', 'Unknown error')}")

            self.update_progress(85, "Deploying to MOD_P...")
            target_path = self.parent.get_mod_path(file_id, self.lang)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(conversion_result['output_path'], target_path)

            self.update_progress(100, self.tr("status_complete"))
            QtCore.QMetaObject.invokeMethod(self, "show_success", QtCore.Qt.QueuedConnection)

        except Exception as e:
            QtCore.QMetaObject.invokeMethod(self, "show_error", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, str(e)))
    def cleanup_before_exit(self):
        if hasattr(self, 'player'):
            self.player.stop()
        
        for f in self.temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except PermissionError:
                    DEBUG.log(f"Could not delete temp file (in use?): {f}", "WARNING")
                except Exception as e:
                    DEBUG.log(f"Error deleting temp file {f}: {e}", "ERROR")
        self.temp_files = []
    def update_progress(self, value, text):
        QtCore.QMetaObject.invokeMethod(self.progress_bar, "setValue", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(int, value))
        QtCore.QMetaObject.invokeMethod(self.progress_label, "setText", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, text))

    @QtCore.pyqtSlot()
    def show_success(self):
        self.progress_widget.hide()
        QtWidgets.QMessageBox.information(self, self.tr("success"), self.tr("trim_success_msg"))
        self.parent.populate_tree(self.lang)
        self.accept()
        
    @QtCore.pyqtSlot(str)
    def show_error(self, error):
        self.progress_widget.hide()
        self.process_btn.setEnabled(True)
        QtWidgets.QMessageBox.critical(self, self.tr("error"), f"{self.tr('trim_failed_title')}:\n\n{error}")
    def accept(self):
        self.cleanup_before_exit()
        super().accept()

    def reject(self):
        self.cleanup_before_exit()
        super().reject()
    def closeEvent(self, event):
        self.cleanup_before_exit()
        super().closeEvent(event)
class WaveformWidget(QtWidgets.QWidget):
    rangeChanged = QtCore.pyqtSignal(int, int)
    viewChanged = QtCore.pyqtSignal(int, int)
    zoomRequested = QtCore.pyqtSignal(int, int)
    seekRequested = QtCore.pyqtSignal(int)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        
        self.audio_data = None
        self.resampled_data = None
        self.sample_rate = 0
        
        self.duration_ms = 0
        self.selection_start_ms = 0
        self.selection_end_ms = 0
        self.view_start_ms = 0
        self.view_end_ms = 0
        self.playhead_ms = 0
        
        self.is_selecting = False
        
        self.selection_color = QtGui.QColor(0, 120, 215, 70)
        self.playhead_color = QtGui.QColor(255, 0, 0)
        self.background_color = QtGui.QColor(25, 25, 26)
        self.waveform_color = QtGui.QColor(150, 180, 210)
        
        self.setMouseTracking(True)
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta != 0:
            self.zoomRequested.emit(delta, event.pos().x())
        event.accept()
    def set_duration(self, duration_ms):
        self.duration_ms = duration_ms
        self.selection_end_ms = duration_ms
        self.view_end_ms = duration_ms
        self.viewChanged.emit(0, self.duration_ms)

    def set_waveform(self, wav_path):
        if not MATPLOTLIB_AVAILABLE:
            self.audio_data = None
            self.update()
            return
        
        try:
            import wave
            
            with wave.open(wav_path, 'rb') as wf:
                self.sample_rate = wf.getframerate()
                sampwidth = wf.getsampwidth()
                nframes = wf.getnframes()
                channels = wf.getnchannels()
                
                frames = wf.readframes(nframes)
            
            if sampwidth == 1:
                dtype = np.uint8
                max_val = 2**8 / 2
            elif sampwidth == 2:
                dtype = np.int16
                max_val = 2**15
            elif sampwidth == 3:
                data = np.empty((nframes, channels, 4), dtype=np.uint8)
                data[:, :, :sampwidth] = np.frombuffer(frames, dtype=np.uint8).reshape(-1, channels, sampwidth)
                data[:, :, sampwidth:] = (data[:, :, sampwidth - 1:sampwidth] >> 7) * 255
                data = data.view(np.int32)
                max_val = 2**23
            elif sampwidth == 4:
                try:
                    data = np.frombuffer(frames, dtype=np.float32)
                    max_val = 1.0 
                except (TypeError, ValueError):
                    data = np.frombuffer(frames, dtype=np.int32)
                    max_val = 2**31
            else:
                raise ValueError(f"Unsupported sample width: {sampwidth}")

            if sampwidth != 4 or max_val != 1.0:
                 data = np.frombuffer(frames, dtype=dtype)
            
            if channels > 1:
                data = data.reshape(-1, channels)
                data = data.mean(axis=1)

            self.audio_data = data.astype(np.float32) / max_val
            
            DEBUG.log(f"Waveform data loaded: {len(self.audio_data)} samples, sample rate: {self.sample_rate}, sampwidth: {sampwidth}")
        
        except Exception as e:
            DEBUG.log(f"Error loading waveform data: {e}", "ERROR")
            self.audio_data = None
        
        self.update()

    def set_selection_range(self, start_ms, end_ms):
        self.selection_start_ms = max(0, start_ms)
        self.selection_end_ms = min(self.duration_ms, end_ms)
        self.update()

    def set_view_range(self, start_ms, end_ms):
        self.view_start_ms = max(0, start_ms)
        self.view_end_ms = min(self.duration_ms, end_ms)
        self.update()

    def set_playhead(self, position_ms):
        self.playhead_ms = position_ms
        self.update()
        
    def _ms_to_sample(self, ms):
        return int(ms / 1000.0 * self.sample_rate)

    def _sample_to_ms(self, sample_index):
        return int(sample_index / self.sample_rate * 1000.0)

    def _ms_to_x(self, ms):
        view_duration = self.view_end_ms - self.view_start_ms
        if view_duration <= 0: return 0
        return ((ms - self.view_start_ms) / view_duration) * self.width()

    def _x_to_ms(self, x, view_start=None, view_end=None):
        start = view_start if view_start is not None else self.view_start_ms
        end = view_end if view_end is not None else self.view_end_ms
        
        view_duration = end - start
        if view_duration <= 0: return 0
        return start + int((x / self.width()) * view_duration)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), self.background_color)

        if self.audio_data is not None and self.duration_ms > 0:
            view_duration = self.view_end_ms - self.view_start_ms
            if view_duration <= 0:
                painter.end()
                return

            start_sample = self._ms_to_sample(self.view_start_ms)
            end_sample = self._ms_to_sample(self.view_end_ms)
            visible_data = self.audio_data[start_sample:end_sample]

            if len(visible_data) > 0:
        
                path = QtGui.QPainterPath()
                h = self.height()
                half_h = h / 2
                
                samples_per_pixel = len(visible_data) / self.width()
                
                if samples_per_pixel < 1: 
              
                    path.moveTo(0, half_h - visible_data[0] * half_h)
                    for i, sample in enumerate(visible_data):
                        x = i * (self.width() / len(visible_data))
                        y = half_h - sample * half_h
                        path.lineTo(x, y)
                else:
                    step = int(samples_per_pixel)
                    path.moveTo(0, half_h)
                    for i in range(self.width()):
                        start = i * step
                        end = start + step
                        if start >= len(visible_data): break
                        
                        chunk = visible_data[start:end]
                        min_val = np.min(chunk)
                        max_val = np.max(chunk)
                        
                        y_max = half_h - max_val * half_h
                        y_min = half_h - min_val * half_h
                        
                        painter.setPen(QtGui.QPen(self.waveform_color, 1))
                        painter.drawLine(i, int(y_min), i, int(y_max))
                
                if not path.isEmpty():
                    painter.setPen(QtGui.QPen(self.waveform_color, 1))
                    painter.drawPath(path)

        start_x = self._ms_to_x(self.selection_start_ms)
        end_x = self._ms_to_x(self.selection_end_ms)
        painter.fillRect(QtCore.QRectF(start_x, 0, end_x - start_x, self.height()), self.selection_color)
        
        if self.view_start_ms <= self.playhead_ms <= self.view_end_ms:
            playhead_x = self._ms_to_x(self.playhead_ms)
            painter.setPen(self.playhead_color)
            painter.drawLine(int(playhead_x), 0, int(playhead_x), self.height())
        
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.duration_ms > 0:
            seek_ms = self._x_to_ms(event.pos().x())
            
            self.seekRequested.emit(seek_ms)
            
            self.is_selecting = True
            self.selection_start_ms = seek_ms
            self.selection_end_ms = seek_ms
            
            self.set_playhead(seek_ms) 
            self.rangeChanged.emit(self.selection_start_ms, self.selection_end_ms)
            self.update()

    def mouseMoveEvent(self, event):
        current_ms = self._x_to_ms(event.pos().x())
        self.setToolTip(f"{current_ms / 1000:.3f} s")

        if self.is_selecting and self.duration_ms > 0:
            start = min(self.selection_start_ms, current_ms)
            end = max(self.selection_start_ms, current_ms)
            if self.selection_start_ms != start or self.selection_end_ms != end:
                self.set_selection_range(start, end)
                self.rangeChanged.emit(start, end)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.is_selecting = False     
