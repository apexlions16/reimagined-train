"""
SaveSubtitlesThread - Saves subtitles in background.
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


class SaveSubtitlesThread(QtCore.QThread):
    progress_updated = QtCore.pyqtSignal(int, str)
    finished = QtCore.pyqtSignal(int, list) # count, errors_list

    def __init__(self, parent_app):
        super().__init__(parent_app)
        self.parent_app = parent_app
        self.tr = parent_app.tr
        
        self.mod_p_path = self.parent_app.mod_p_path
        self.subtitles = self.parent_app.subtitles.copy()
        self.original_subtitles = self.parent_app.original_subtitles.copy()
        self.all_subtitle_files = self.parent_app.all_subtitle_files.copy()
        self.dirty_files = list(self.parent_app.dirty_subtitle_files)
        self.locres_manager = self.parent_app.locres_manager

    def run(self):
        saved_files_count = 0
        errors = []
        
        try:
            total_files = len(self.dirty_files)
            if total_files == 0:
                self.finished.emit(0, [])
                return

            for i, original_path in enumerate(self.dirty_files):
                QtCore.QThread.msleep(1)
                
                file_info = self.find_file_info_by_path(original_path)
                if not file_info:
                    errors.append(f"Could not find file info for path: {original_path}")
                    continue

                progress = int(((i + 1) / total_files) * 100)
                self.progress_updated.emit(progress, self.tr("Saving") + f" {file_info['filename']}...")
                
                target_dir = os.path.join(self.mod_p_path, "OPP", "Content", "Localization", file_info['category'], file_info['language'])
                os.makedirs(target_dir, exist_ok=True)
                target_path = os.path.join(target_dir, file_info['filename'])

                try:
                    subtitles_to_write = self.locres_manager.export_locres(original_path)
                    
                    for key in subtitles_to_write.keys():
                        if key in self.subtitles:
                            subtitles_to_write[key] = self.subtitles[key]
                    
                    shutil.copy2(original_path, target_path)

                    if not self.locres_manager.import_locres(target_path, subtitles_to_write):
                        raise Exception("UnrealLocresManager failed to import data.")
                    
                    saved_files_count += 1
                except Exception as e:
                    msg = f"Failed to save {file_info['filename']}: {e}"
                    errors.append(msg)
                    DEBUG.log(msg, "ERROR")

            self.finished.emit(saved_files_count, errors)

        except Exception as e:
            errors.append(f"A critical error occurred during saving: {e}")
            self.finished.emit(saved_files_count, errors)

    def find_file_info_by_path(self, path_to_find):
        for info in self.all_subtitle_files.values():
            if info['path'] == path_to_find:
                return info
        return None
