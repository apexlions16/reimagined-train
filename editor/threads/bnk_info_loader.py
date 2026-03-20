"""
BnkInfoLoader - Loads BNK info in background.
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


class BnkInfoLoader(QtCore.QThread):
    info_loaded = QtCore.pyqtSignal(int, object, object)  # source_id, original_info, modified_info

    def __init__(self, parent, source_id, bnk_files_info, mod_p_path, wems_base_path):
        super().__init__(parent)
        self.source_id = source_id
        self.bnk_files_info = bnk_files_info 
        self.mod_p_path = mod_p_path
        self.wems_base_path = wems_base_path
        self.parent_app = parent
        
    def run(self):
        original_bnk_info, original_bnk_path = self.find_info_in_bnks(self.bnk_files_info, self.source_id, is_mod=False)
        if original_bnk_info:
            DEBUG.log(f"Original information for ID {self.source_id} found in BNK: {os.path.basename(original_bnk_path)}")
        else:
            DEBUG.log(f"Original information for ID {self.source_id} not found in any BNK.")

        mod_bnk_paths_info = []
        for bnk_path, bnk_type in self.bnk_files_info:
            if bnk_type == 'sfx':
                base_for_relpath = os.path.join(self.wems_base_path, "SFX")
                rel_path = os.path.relpath(bnk_path, base_for_relpath)
                mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
            else:
                base_for_relpath = self.wems_base_path
                rel_path = os.path.relpath(bnk_path, base_for_relpath)
                mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
            
            if os.path.exists(mod_bnk_path):
                mod_bnk_paths_info.append((mod_bnk_path, bnk_type))
        
        modified_bnk_info, modified_bnk_path = self.find_info_in_bnks(mod_bnk_paths_info, self.source_id, is_mod=True)
        if modified_bnk_info:
            DEBUG.log(f"Modified information for ID {self.source_id} found in BNK: {os.path.basename(modified_bnk_path)}")
        else:
            if mod_bnk_paths_info:
                 DEBUG.log(f"Modified information for ID {self.source_id} not found.")

        self.info_loaded.emit(self.source_id, original_bnk_info, modified_bnk_info)

    def find_info_in_bnks(self, bnk_paths_info, source_id, is_mod=False):
        cache_name = 'bnk_cache_mod' if is_mod else 'bnk_cache_orig'
        cache = getattr(self.parent_app, cache_name, {})
        
        for bnk_path, bnk_type in bnk_paths_info:
            if bnk_path in cache and source_id in cache[bnk_path]:
                return cache[bnk_path][source_id], bnk_path

            try:
                editor = BNKEditor(bnk_path)
                entries = editor.find_sound_by_source_id(source_id)
                if entries:
                    entry = entries[0]
                    
                    if bnk_path not in cache:
                        cache[bnk_path] = {}
                    cache[bnk_path][source_id] = entry
                    setattr(self.parent_app, cache_name, cache)
                    
                    return entry, bnk_path
            except Exception as e:
                DEBUG.log(f"Error reading BNK {bnk_path}: {e}", "WARNING")
                continue
        
        return None, None    
