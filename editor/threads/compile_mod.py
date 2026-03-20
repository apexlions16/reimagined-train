"""
CompileModThread - Compiles mod in background.
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


class CompileModThread(QtCore.QThread):

    finished = QtCore.pyqtSignal(bool, str) 

    def __init__(self, repak_path, mod_p_path, parent=None):
        super().__init__(parent)
        self.repak_path = repak_path
        self.mod_p_path = mod_p_path

    def run(self):
        command = [self.repak_path, "pack", "--version", "V11", "--compression", "Zlib", self.mod_p_path]
        try:
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = 0x08000000
            else:
                startupinfo = None
                creationflags = 0

            result = subprocess.run(
                command, 
                capture_output=True, 
                text=True,
                startupinfo=startupinfo,
                creationflags=creationflags,
                encoding='utf-8',
                errors='ignore'
            )

            if result.returncode == 0:
                output = result.stderr if result.stderr else result.stdout
                self.finished.emit(True, output)
            else:
                self.finished.emit(False, result.stderr)
        except Exception as e:
            self.finished.emit(False, str(e))
