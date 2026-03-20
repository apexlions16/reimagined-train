"""
BNK File Editor - SoundEntry and BNKEditor classes.
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

from editor.core.debug import DEBUG


@dataclass
class SoundEntry:
    offset: int
    sound_id: int
    source_id: int
    file_size: int
    override_fx: bool

class BNKEditor:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"File {file_path} not found")
        self.data = None
        self._sound_map = None
        self.load_file()

    def _build_sound_map(self):

        if self._sound_map is not None:
            return

        DEBUG.log(f"Building sound map for {self.file_path.name}...")
        self._sound_map = {}

        search_pattern = b'\x01\x00\x04\x00\x00'
        offset = 0
        while True:
            try:
                offset = self.data.index(search_pattern, offset)
                
                id_offset = offset + 5
                if id_offset + 4 <= len(self.data):
                    source_id = struct.unpack('<I', self.data[id_offset:id_offset+4])[0]
                    
                    entry_start_offset = offset - 4 
                    
                    if source_id not in self._sound_map:
                        self._sound_map[source_id] = []
                    self._sound_map[source_id].append(entry_start_offset)

                offset += len(search_pattern)
            except ValueError:
                break 
        DEBUG.log(f"Sound map for {self.file_path.name} built. Found {len(self._sound_map)} unique sound IDs.")

    def load_file(self):
        with open(self.file_path, 'rb') as f:
            self.data = bytearray(f.read())

    def save_file(self, output_path: Optional[str] = None):
        if output_path is None:
            output_path = self.file_path
            
        with open(output_path, 'wb') as f:
            f.write(self.data)

    def find_sound_by_source_id(self, source_id: int, expected_size: Optional[int] = None) -> List[SoundEntry]:
        self._build_sound_map() 
        
        offsets = self._sound_map.get(source_id)
        if not offsets:
            return []
        
        found_entries = []
        for offset in offsets:
            entry = self._parse_sound_entry(offset)
            if entry:
                if expected_size is None or entry.file_size == expected_size:
                    found_entries.append(entry)
        return found_entries
        
    def find_all_sounds(self) -> List[SoundEntry]:
     
        self._build_sound_map()
        all_entries = []
        for source_id, offsets in self._sound_map.items():
            for offset in offsets:
                entry = self._parse_sound_entry(offset)
                if entry:
                    all_entries.append(entry)
        return all_entries

    def _parse_sound_entry(self, offset: int) -> Optional[SoundEntry]:
        try:
            if offset + 19 > len(self.data):
                return None
            
            sound_id = struct.unpack('<I', self.data[offset:offset+4])[0]

            source_id_offset = offset + 9
            source_id = struct.unpack('<I', self.data[source_id_offset:source_id_offset+4])[0]
            
            file_size_offset = source_id_offset + 4
            file_size = struct.unpack('<I', self.data[file_size_offset:file_size_offset+4])[0]
            
            fx_flag_offset = file_size_offset + 5 
            override_fx = self.data[fx_flag_offset] == 0x01
            
            return SoundEntry(
                offset=offset,
                sound_id=sound_id,
                source_id=source_id,
                file_size=file_size,
                override_fx=override_fx
            )
        except (struct.error, IndexError):
            return None
            
    def modify_sound(self, source_id: int, override_fx: Optional[bool] = None, 
                     new_size: Optional[int] = None, find_by_size: Optional[int] = None):
        entries = self.find_sound_by_source_id(source_id, find_by_size)
        
        if not entries:
            # DEBUG.log(f"Sound with Source ID {source_id} (and size {find_by_size}) not found in BNK", "WARNING")
            return False
            
        modified = False
        for entry in entries:
            # DEBUG.log(f"Modifying entry in BNK at offset 0x{entry.offset:08X} (ID: {entry.source_id}, current size: {entry.file_size})")

            if override_fx is not None:
                fx_flag_offset = entry.offset + 18
                new_byte = 0x01 if override_fx else 0x00
                self.data[fx_flag_offset] = new_byte
                # DEBUG.log(f"  Override FX changed to: {override_fx}")
                modified = True
                
            if new_size is not None:
                if new_size > 0xFFFFFFFF:
                    # DEBUG.log(f"  Size {new_size} is too large", "ERROR")
                    continue
                    
                file_size_offset = entry.offset + 13
                struct.pack_into('<I', self.data, file_size_offset, new_size)
                # DEBUG.log(f"  File size changed from {entry.file_size} to: {new_size}")
                modified = True
                
        return modified    
