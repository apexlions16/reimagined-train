"""
VolumeProcessor - Audio volume processing.
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


class VolumeProcessor:
    def __init__(self):
        self.has_numpy = False
        self.has_scipy = False
        self.np = None
        self.wavfile = None
        try:
            DEBUG.log("VolumeProcessor: trying to import numpy...", "INFO")
            import numpy as np
            DEBUG.log("VolumeProcessor: numpy imported successfully", "INFO")
            self.np = np
        except Exception as e:
            import traceback
            DEBUG.log(f"VolumeProcessor: NumPy import error: {e}", "ERROR")
            DEBUG.log(traceback.format_exc(), "ERROR")
            self.np = None

        try:
            DEBUG.log("VolumeProcessor: trying to import scipy.io.wavfile...", "INFO")
            import scipy.io.wavfile
            DEBUG.log("VolumeProcessor: scipy.io.wavfile imported successfully", "INFO")
            self.wavfile = scipy.io.wavfile
        except Exception as e:
            import traceback
            DEBUG.log(f"VolumeProcessor: SciPy import error: {e}", "ERROR")
            DEBUG.log(traceback.format_exc(), "ERROR")
            self.wavfile = None

        if self.np is not None:
            self.has_numpy = True
        if self.wavfile is not None:
            self.has_scipy = True

        DEBUG.log(f"VolumeProcessor: has_numpy={self.has_numpy}, has_scipy={self.has_scipy}", "INFO")

    def is_available(self):
        DEBUG.log(f"VolumeProcessor.is_available: {self.has_numpy=}, {self.has_scipy=}", "INFO")
        return self.has_numpy and self.has_scipy

    def analyze_audio(self, wav_path):
        DEBUG.log(f"VolumeProcessor.analyze_audio: {wav_path=}", "INFO")
        if not self.is_available():
            DEBUG.log("VolumeProcessor.analyze_audio: not available", "WARNING")
            return None
        try:
            sample_rate, data = self.wavfile.read(wav_path)
            DEBUG.log(f"VolumeProcessor.analyze_audio: sample_rate={sample_rate}, dtype={data.dtype}", "INFO")
            if data.dtype == self.np.int16:
                max_amp = 32767
            elif data.dtype == self.np.int32:
                max_amp = 2147483647
            elif data.dtype == self.np.uint8:
                max_amp = 255
            else:
                max_amp = 1.0
            data_float = data.astype(self.np.float64)
            rms = self.np.sqrt(self.np.mean(data_float**2))
            rms_percent = (rms / max_amp) * 100
            peak = self.np.max(self.np.abs(data_float))
            peak_percent = (peak / max_amp) * 100
            max_increase_without_clipping = (max_amp / peak) * 100 if peak > 0 else 100
            DEBUG.log(f"VolumeProcessor.analyze_audio: rms={rms}, peak={peak}", "INFO")
            return {
                'sample_rate': sample_rate,
                'duration_seconds': len(data) / sample_rate,
                'rms': rms,
                'rms_percent': rms_percent,
                'peak': peak,
                'peak_percent': peak_percent,
                'max_amp': max_amp,
                'dtype': data.dtype,
                'max_increase': max_increase_without_clipping
            }
        except Exception as e:
            import traceback
            DEBUG.log(f"Error analyzing audio: {e}", "ERROR")
            DEBUG.log(traceback.format_exc(), "ERROR")
            return None

    def change_volume(self, input_wav, output_wav, volume_percent):
        DEBUG.log(f"VolumeProcessor.change_volume: {input_wav=}, {output_wav=}, {volume_percent=}", "INFO")
        if not self.is_available():
            DEBUG.log("VolumeProcessor.change_volume: not available", "WARNING")
            return False, "NumPy/SciPy not installed"
        try:
            sample_rate, data = self.wavfile.read(input_wav)
            original_dtype = data.dtype
            if data.dtype == self.np.int16:
                max_amp = 32767
            elif data.dtype == self.np.int32:
                max_amp = 2147483647
            elif data.dtype == self.np.uint8:
                max_amp = 255
            else:
                max_amp = 1.0
            data_float = data.astype(self.np.float64)
            current_rms = self.np.sqrt(self.np.mean(data_float**2))
            current_peak = self.np.max(self.np.abs(data_float))
            scale = volume_percent / 100.0
            new_data = data_float * scale
            clipped_samples = self.np.sum(self.np.abs(new_data) > max_amp)
            clipped_percent = 0
            if clipped_samples > 0:
                clipped_percent = (clipped_samples / len(new_data)) * 100
            new_data = self.np.clip(new_data, -max_amp, max_amp - 1)
            final_rms = self.np.sqrt(self.np.mean(new_data**2))
            actual_change = (final_rms / current_rms) * 100 if current_rms > 0 else 100
            new_data = new_data.astype(original_dtype)
            self.wavfile.write(output_wav, sample_rate, new_data)
            result_info = {
                'actual_change': actual_change,
                'clipped_percent': clipped_percent,
                'final_rms': final_rms,
                'final_peak': self.np.max(self.np.abs(new_data))
            }
            DEBUG.log(f"VolumeProcessor.change_volume: result_info={result_info}", "INFO")
            return True, result_info
        except Exception as e:
            import traceback
            DEBUG.log(f"Error changing volume: {e}", "ERROR")
            DEBUG.log(traceback.format_exc(), "ERROR")
            return False, str(e)
