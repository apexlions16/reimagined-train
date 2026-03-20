"""
WEMAnalyzer - WEM file analysis.
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


class WEMAnalyzer:
    def __init__(self, filename):
        self.filename = filename
        self.sample_rate = 0
        self.channels = 0
        self.cue_points = []
        self.labels = {}
        
    def read_chunk_header(self, file):
  
        chunk_id = file.read(4)
        if len(chunk_id) < 4:
            return None, 0
        chunk_size = struct.unpack('<I', file.read(4))[0]
        return chunk_id.decode('ascii', errors='ignore'), chunk_size
    
    def parse_fmt_chunk(self, file, size):
   
        fmt_data = file.read(size)
        
        if len(fmt_data) < 8:
            return
            
        audio_format = struct.unpack('<H', fmt_data[0:2])[0]
        self.channels = struct.unpack('<H', fmt_data[2:4])[0]
        self.sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
        

        DEBUG.log(f"Audio format: 0x{audio_format:04X}")
        DEBUG.log(f"Channels: {self.channels}")
        DEBUG.log(f"Sample rate: {self.sample_rate} Hz")
    def parse_cue_chunk(self, file, size):
        
        cue_data = file.read(size)
        
        num_cues = struct.unpack('<I', cue_data[0:4])[0]
        offset = 4
        
        for i in range(num_cues):
            if offset + 24 <= len(cue_data):
                cue_id = struct.unpack('<I', cue_data[offset:offset+4])[0]
                position = struct.unpack('<I', cue_data[offset+4:offset+8])[0]
                chunk_id = cue_data[offset+8:offset+12].decode('ascii', errors='ignore').rstrip('\x00')
                chunk_start = struct.unpack('<I', cue_data[offset+12:offset+16])[0]
                block_start = struct.unpack('<I', cue_data[offset+16:offset+20])[0]
                sample_offset = struct.unpack('<I', cue_data[offset+20:offset+24])[0]
                
                cue_point = CuePoint(cue_id, position, chunk_id, chunk_start, block_start, sample_offset)
                self.cue_points.append(cue_point)
                offset += 24
    
    def parse_list_chunk(self, file, size):
     
        list_data = file.read(size)
        
        if len(list_data) < 4:
            return
            
        list_type = list_data[0:4].decode('ascii', errors='ignore')
        
        if list_type == 'adtl':  # Associated Data List
            offset = 4
            while offset < len(list_data):
                if offset + 8 > len(list_data):
                    break
                    
                sub_chunk_id = list_data[offset:offset+4].decode('ascii', errors='ignore')
                sub_chunk_size = struct.unpack('<I', list_data[offset+4:offset+8])[0]
                
                if sub_chunk_id == 'labl' and offset + 8 + sub_chunk_size <= len(list_data):
                   
                    cue_id = struct.unpack('<I', list_data[offset+8:offset+12])[0]
                    
                    label_data = list_data[offset+12:offset+8+sub_chunk_size]
                    
                    
                    try:
                        label_text = label_data.decode('ascii', errors='ignore').rstrip('\x00')
                     
                        label_text = ''.join(char for char in label_text if char.isprintable() or char.isspace())
                        label_text = label_text.strip()
                        
                        if label_text:
                            self.labels[cue_id] = label_text
                            DEBUG.log(f"Found label ID {cue_id}: '{label_text}'")
                            
                    except Exception as e:
                        DEBUG.log(f"Error decoding label for cue {cue_id}: {e}", "ERROR")
                
              
                offset += 8 + sub_chunk_size
                if sub_chunk_size % 2 == 1:
                    offset += 1
    def analyze(self):

        try:
            with open(self.filename, 'rb') as f:
                riff_id = f.read(4)
                if riff_id != b'RIFF':
                    DEBUG.log(f"Not a RIFF file: {self.filename}", "ERROR")
                    return False
                
                file_size = struct.unpack('<I', f.read(4))[0]
                wave_id = f.read(4)
                
                if wave_id != b'WAVE':
                    DEBUG.log(f"Not a WAVE file: {self.filename}", "ERROR")
                    return False
                
                DEBUG.log(f"Analyzing WEM file: {os.path.basename(self.filename)} (size: {file_size + 8} bytes)")
                
                while f.tell() < file_size + 8:
                    chunk_id, chunk_size = self.read_chunk_header(f)
                    if chunk_id is None:
                        break
                    
                    current_pos = f.tell()
                    
                    if chunk_id == 'fmt ':
                        self.parse_fmt_chunk(f, chunk_size)
                    elif chunk_id == 'cue ':
                        self.parse_cue_chunk(f, chunk_size)
                    elif chunk_id == 'LIST':
                        self.parse_list_chunk(f, chunk_size)
                    else:
                        f.seek(current_pos + chunk_size)
                    
                    if chunk_size % 2 == 1:
                        f.read(1)
                
           
                DEBUG.log(f"Final analysis result:")
                DEBUG.log(f"  Sample rate: {self.sample_rate} Hz")
                DEBUG.log(f"  Channels: {self.channels}")
                DEBUG.log(f"  Cue points: {len(self.cue_points)}")
                DEBUG.log(f"  Labels: {len(self.labels)}")
                
          
                for cue in self.cue_points:
                    if self.sample_rate > 0:
                        calc_time = cue.position / self.sample_rate
                        DEBUG.log(f"  Cue {cue.id}: {cue.position} samples = {calc_time:.3f} seconds")
                
                return True
                
        except Exception as e:
            DEBUG.log(f"Error analyzing WEM file {self.filename}: {e}", "ERROR")
            return False
    def get_markers_info(self):
        markers = []
        sorted_cues = sorted(self.cue_points, key=lambda x: x.position)
        
        
        for cue in sorted_cues:
            time_seconds = 0.0
            if self.sample_rate > 0:
                time_seconds = float(cue.position) / float(self.sample_rate)
            
            label = self.labels.get(cue.id, "")
            
            marker_info = {
                'id': cue.id,
                'position': cue.position,
                'time_seconds': time_seconds,
                'label': label
            }
            markers.append(marker_info)
        
        return markers
