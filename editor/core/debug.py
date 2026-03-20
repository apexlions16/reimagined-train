"""
DebugLogger - Debug logging utility.
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


class DebugLogger:
    def __init__(self):
        self.logs_in_memory = []
        self.callbacks = []
        self.log_file_path = None

    def setup_logging(self, base_path):
        try:
            data_path = os.path.join(base_path, "data")
            os.makedirs(data_path, exist_ok=True)
            
            self.log_file_path = os.path.join(data_path, "session_log.txt")
            previous_log_path = os.path.join(data_path, "previous_session_log.txt")
            
            if os.path.exists(self.log_file_path):
                if os.path.exists(previous_log_path):
                    os.remove(previous_log_path)
                os.rename(self.log_file_path, previous_log_path)

            with open(self.log_file_path, 'w', encoding='utf-8') as f:
                f.write(f"=== Session started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

        except Exception as e:
            print(f"FATAL: Could not set up file logging: {e}")
            self.log_file_path = None

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] [{level}] {message}"
        
        self.logs_in_memory.append(log_entry)
        print(log_entry)
        
        if self.log_file_path:
            try:
                with open(self.log_file_path, 'a', encoding='utf-8') as f:
                    f.write(log_entry + '\n')
            except Exception as e:
                print(f"ERROR: Could not write to log file: {e}")
        
        for callback in self.callbacks:
            callback(log_entry)
            
    def add_callback(self, callback):
        self.callbacks.append(callback)
        
    def get_logs(self):
        return "\n".join(self.logs_in_memory)

DEBUG = DebugLogger()
