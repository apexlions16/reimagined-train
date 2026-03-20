"""
AudioToWavConverter - Audio to WAV conversion.
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


class AudioToWavConverter:
    
    SUPPORTED_FORMATS = ['.mp3', '.ogg', '.flac', '.m4a', '.aac', '.wma', '.opus', '.webm']
    
    def __init__(self, ffmpeg_path=None):
        self.ffmpeg_path = ffmpeg_path or self.find_ffmpeg()
        
    def find_ffmpeg(self):
       
        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            
        ffmpeg_paths = [
            os.path.join(base_path, "data", "ffmpeg.exe"),
            os.path.join(base_path, "libs", "ffmpeg.exe"),
            "ffmpeg.exe",  
            "ffmpeg"
        ]
        
        for path in ffmpeg_paths:
            if os.path.exists(path) or shutil.which(path):
                return path
                
        return None
        
    def is_available(self):
        return self.ffmpeg_path is not None
        
    def is_supported_format(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self.SUPPORTED_FORMATS
        
    def convert_to_wav(self, input_file, output_wav=None, sample_rate=48000):
        if not self.is_available():
            return False, "FFmpeg not found"
            
        if output_wav is None:
            output_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
            
        try:
            cmd = [
                self.ffmpeg_path,
                '-i', input_file,
                '-acodec', 'pcm_s16le',
                '-ar', str(sample_rate),
                '-ac', '2',  # Stereo
                '-y',  # Overwrite
                output_wav
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                creationflags=CREATE_NO_WINDOW,
                encoding='utf-8',
                errors='ignore'
            )
            
            if result.returncode == 0:
                return True, output_wav
            else:
                return False, result.stderr
                
        except Exception as e:
            return False, str(e)
