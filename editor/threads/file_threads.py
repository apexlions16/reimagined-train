"""
File operation threads - Adding and dropping files.
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


class AddFilesThread(QtCore.QThread):
    progress_updated = QtCore.pyqtSignal(int, str) 
    details_updated = QtCore.pyqtSignal(str) 
    finished = QtCore.pyqtSignal(int, int, int, int)  
    error_occurred = QtCore.pyqtSignal(str)       
    
    def __init__(self, parent, audio_folder):
        super().__init__(parent)
        self.audio_folder = audio_folder
        self.parent = parent
        self.should_stop = False
        self.replace_all = False
        self.skip_all = False
    
    def run(self):
        try:
            audio_extensions = ['.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.wma', '.opus', '.webm']
            
            audio_files = []
            for file in os.listdir(self.audio_folder):
                if any(file.lower().endswith(ext) for ext in audio_extensions):
                    audio_files.append(file)
            
            if not audio_files:
                self.finished.emit(0, 0, 0, 0)
                return
            
            self.details_updated.emit(f"Found {len(audio_files)} audio files")
            
            added_count = 0
            replaced_count = 0
            skipped_count = 0
            not_found = 0
            
            for i, audio_file in enumerate(audio_files):
                if self.should_stop:
                    break
                
                audio_path = os.path.join(self.audio_folder, audio_file)
                
                percent = int((i / len(audio_files)) * 100)
                self.progress_updated.emit(percent, f"Processing {audio_file} ({i+1}/{len(audio_files)})...")
                
                result = self.parent.find_matching_wem_for_audio(
                    audio_path, 
                    auto_mode=True, 
                    replace_all=self.replace_all, 
                    skip_all=self.skip_all
                )
                
                if result == 'replace_all':
                    self.replace_all = True
           
                    result = self.parent.find_matching_wem_for_audio(
                        audio_path, 
                        auto_mode=True, 
                        replace_all=True, 
                        skip_all=False
                    )
                elif result == 'skip_all':
                    self.skip_all = True
            
                    result = self.parent.find_matching_wem_for_audio(
                        audio_path, 
                        auto_mode=True, 
                        replace_all=False, 
                        skip_all=True
                    )
                
                if result is True:
                    if self.replace_all:
                        replaced_count += 1
                    else:
                        added_count += 1
                elif result is False:
                    skipped_count += 1
                elif result is None:
                    not_found += 1
            
            self.progress_updated.emit(100, "Complete!")
            self.finished.emit(added_count, replaced_count, skipped_count, not_found)
            
        except Exception as e:
            self.error_occurred.emit(str(e))
class AddSingleFileThread(QtCore.QThread):
    progress_updated = QtCore.pyqtSignal(int, str) 
    details_updated = QtCore.pyqtSignal(str)   
    finished = QtCore.pyqtSignal(bool)           
    error_occurred = QtCore.pyqtSignal(str)   
    
    def __init__(self, parent, file_path):
        super().__init__(parent)
        self.file_path = file_path
        self.parent = parent
        self.should_stop = False
    
    def run(self):
        try:
            self.progress_updated.emit(0, "Processing file...")
            
            result = self.parent.find_matching_wem_for_audio(self.file_path, auto_mode=False)
            if result is True:
        
                self.details_updated.emit("File processed successfully.")
                self.progress_updated.emit(100, "Complete!")
                self.finished.emit(True)
            elif result is False:
    
                self.details_updated.emit("File was skipped (already in list or user choice).")
                self.progress_updated.emit(100, "Complete!")
                self.finished.emit(False)
            elif result is None:
      
                self.details_updated.emit("No matching WEM file found in the database.")
                self.progress_updated.emit(100, "Complete!")
                self.finished.emit(False)
            else:
        
                self.details_updated.emit(f"File processed with result: {result}")
                self.progress_updated.emit(100, "Complete!")
                self.finished.emit(True) 
            
        except Exception as e:
            import traceback
            error_details = f"{str(e)}\n\n{traceback.format_exc()}"
            self.error_occurred.emit(error_details)
class DropFilesThread(QtCore.QThread):
    progress_updated = QtCore.pyqtSignal(int, str)
    details_updated = QtCore.pyqtSignal(str)     
    finished = QtCore.pyqtSignal(int, int, int, int) 
    error_occurred = QtCore.pyqtSignal(str)    
    
    def __init__(self, parent, file_paths):
        super().__init__(parent)
        self.file_paths = file_paths
        self.parent = parent
        self.should_stop = False
        self.replace_all = False
        self.skip_all = False
    
    def run(self):
        try:
            self.details_updated.emit(f"Processing {len(self.file_paths)} dropped files...")
            
            added_count = 0
            replaced_count = 0
            skipped_count = 0
            not_found = 0
            
            for i, file_path in enumerate(self.file_paths):
                if self.should_stop:
                    break
                
                percent = int((i / len(self.file_paths)) * 100)
                self.progress_updated.emit(percent, f"Processing {os.path.basename(file_path)} ({i+1}/{len(self.file_paths)})...")
                
                file_ext = os.path.splitext(file_path)[1].lower()
                supported_formats = ['.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.wma', '.opus', '.webm']
                
                if file_ext not in supported_formats:
                    self.details_updated.emit(f"✗ {os.path.basename(file_path)} - unsupported format")
                    skipped_count += 1
                    continue
                
                auto_mode = len(self.file_paths) > 1  
                
                result = self.parent.find_matching_wem_for_audio(
                    file_path, 
                    auto_mode=auto_mode, 
                    replace_all=self.replace_all, 
                    skip_all=self.skip_all
                )
                
                if result == 'replace_all':
                    self.replace_all = True
                    result = self.parent.find_matching_wem_for_audio(
                        file_path, 
                        auto_mode=auto_mode, 
                        replace_all=True, 
                        skip_all=False
                    )
                elif result == 'skip_all':
                    self.skip_all = True
                    result = self.parent.find_matching_wem_for_audio(
                        file_path, 
                        auto_mode=auto_mode, 
                        replace_all=False, 
                        skip_all=True
                    )
                
                if result is True:
                    if self.replace_all:
                        replaced_count += 1
                    else:
                        added_count += 1
                elif result is False:
                    skipped_count += 1
                elif result is None:
                    not_found += 1
            
            self.progress_updated.emit(100, "Complete!")
            self.finished.emit(added_count, replaced_count, skipped_count, not_found)
            
        except Exception as e:
            self.error_occurred.emit(str(e))
if __name__ == "__main__":
    from PyQt5.QtCore import QSharedMemory
    from PyQt5.QtWidgets import QMessageBox

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    shared_memory_key = "DAA73E5A-A93B-4264-8263-6901E788C946-OutlastTrialsAudioEditor"
    shared_memory = QSharedMemory(shared_memory_key)
    
    temp_settings = AppSettings()
    lang = temp_settings.data.get("ui_language", "en")
    temp_tr = lambda key: TRANSLATIONS.get(lang, {}).get(key, key)
    
    if not shared_memory.create(1):
        QMessageBox.warning(
            None, 
            temp_tr("app_already_running_title"), 
            temp_tr("app_already_running_msg")
        )
        sys.exit(0)

    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    splash_path = os.path.join(base_path, "data", "splash.png")
    splash = None

    if os.path.exists(splash_path):
        original_pixmap = QtGui.QPixmap(splash_path)
        splash = QtWidgets.QSplashScreen(original_pixmap, QtCore.Qt.WindowStaysOnTopHint)
        splash.setMask(original_pixmap.mask())

        def show_splash_message(message_key):
            pixmap_with_text = original_pixmap.copy()
            painter = QtGui.QPainter(pixmap_with_text)
            
            font = QtGui.QFont()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QtGui.QColor(220, 220, 220))

            rect = pixmap_with_text.rect()
            text_rect = QtCore.QRect(rect.x(), rect.y() + rect.height() - 40, rect.width(), 30)

            painter.drawText(text_rect, QtCore.Qt.AlignCenter, temp_tr(message_key))
            painter.end()
            
            splash.setPixmap(pixmap_with_text)
            app.processEvents()

        show_splash_message("splash_loading_app")
        splash.show()
        app.processEvents()
    
    try:
        if splash: show_splash_message("splash_init_ui")
        window = WemSubtitleApp()

        if splash: show_splash_message("splash_loading_profiles")
        if not window.initialize_profiles_and_ui():
            sys.exit(0)
        
        if splash:
            splash.finish(window)
        
        window.show()
        
        QtCore.QTimer.singleShot(100, window.load_orphans_from_cache_or_scan)
        
        sys.exit(app.exec_())
    except Exception as e:
        error_msg = f"An unexpected error occurred:\n\n{str(e)}\n\n"
        error_msg += "Traceback:\n" + traceback.format_exc()
        
        log_filename = f"crash_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        log_path = os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__)), log_filename)
        
        try:
            with open(log_path, 'w', encoding='utf-8') as log_file:
                log_file.write("=== CRASH LOG ===\n")
                log_file.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_file.write(f"Version: {current_version}\n")
                log_file.write(f"OS: {sys.platform}\n")
                log_file.write(f"Python: {sys.version}\n")
                log_file.write(f"PyQt5: {QtCore.PYQT_VERSION_STR}\n\n")
                
                log_file.write("Debug Logs:\n")
                log_file.write(DEBUG.get_logs() + "\n\n")
                
                log_file.write("Error Details:\n")
                log_file.write(error_msg)

            error_msg += f"\n\nCrash log saved to: {log_path}"
        except Exception as save_error:
            error_msg += f"\n\nFailed to save crash log: {str(save_error)}"
        
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Critical)
        msg.setWindowTitle("Application Error")
        msg.setText("The application has encountered an error and will close.")
        msg.setInformativeText("Please report this bug with the details below.")
        msg.setDetailedText(error_msg)
        
        copy_btn = msg.addButton("Copy Error to Clipboard", QtWidgets.QMessageBox.ActionRole)
        msg.addButton("Close", QtWidgets.QMessageBox.RejectRole)
        
        msg.exec_()
        
        if msg.clickedButton() == copy_btn:
            QtWidgets.QApplication.clipboard().setText(error_msg)
            print("Error copied to clipboard")
        
        if 'DEBUG' in globals():
            DEBUG.log(f"Critical error: {str(e)}\n{traceback.format_exc()}", "ERROR")
        
        sys.exit(1) 
