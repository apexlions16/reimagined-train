"""
WemScannerThread - Scans for WEM files in background.
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


class WemScannerThread(QtCore.QThread):
    """A thread to scan for orphaned WEM files and return them as a list."""
    scan_finished = QtCore.pyqtSignal(list)

    def __init__(self, wem_root, known_ids, parent=None):
        super().__init__(parent)
        self.wem_root = wem_root
        self.known_ids = known_ids
        self._is_running = True

    def run(self):
        orphaned_entries = []
        if not os.path.exists(self.wem_root):
            self.scan_finished.emit([])
            return

        for root, _, files in os.walk(self.wem_root):
            if not self._is_running:
                break
                
            rel_path = os.path.relpath(root, self.wem_root)
            parts = rel_path.split(os.sep)
            
            lang = "SFX"
            if rel_path == '.' or rel_path == "SFX":
                lang = "SFX"
            elif parts[0] == "Media":
                if len(parts) > 1:
                    lang = parts[1] 
                else:
                    lang = "SFX"
            else:
                lang = rel_path

            for file in files:
                if not self._is_running:
                    break
                if not file.lower().endswith('.wem'):
                    continue

                file_id = os.path.splitext(file)[0]
                if file_id in self.known_ids:
                    continue

                full_path = os.path.join(root, file)
                
                short_name = f"{file_id}.wav"
                try:
                    analyzer = WEMAnalyzer(full_path)
                    if analyzer.analyze():
                        markers = analyzer.get_markers_info()
                        if markers and markers[0]['label']:
                            short_name = f"{markers[0]['label']}.wav"
                except Exception:
                    pass

                new_entry = {
                    "Id": file_id,
                    "Language": lang,
                    "ShortName": short_name, 
                    "Path": file, 
                    "Source": "ScannedFromFileSystem"
                }
                orphaned_entries.append(new_entry)
        
        self.scan_finished.emit(orphaned_entries)

    def stop(self):
        self._is_running = False       
