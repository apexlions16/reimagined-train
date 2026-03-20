"""
ImportModThread - Imports mod files in background.
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


class ImportModThread(QtCore.QThread):
    finished = QtCore.pyqtSignal(bool, str) # success, message

    def __init__(self, parent_app, pak_path, profile_name):
        super().__init__(parent_app)
        self.parent_app = parent_app
        self.tr = parent_app.tr
        self.pak_path = pak_path
        self.profile_name = profile_name
        self.temp_extract_path = os.path.join(tempfile.gettempdir(), f"mod_import_{profile_name}")

    def run(self):
        try:
           
            if os.path.exists(self.temp_extract_path):
                shutil.rmtree(self.temp_extract_path)
            os.makedirs(self.temp_extract_path, exist_ok=True)
            
            command = [self.parent_app.repak_path, "unpack", self.pak_path, "-o", self.temp_extract_path]
            result = subprocess.run(
                command, capture_output=True, text=True, startupinfo=startupinfo,
                creationflags=CREATE_NO_WINDOW, encoding='utf-8', errors='ignore'
            )
            if result.returncode != 0:
                raise Exception(f"Repak failed to unpack: {result.stderr}")

            profiles_root = os.path.join(self.parent_app.base_path, "Profiles")
            profile_path = os.path.join(profiles_root, self.profile_name)
            mod_p_path = os.path.join(profile_path, f"{self.profile_name}_P")
            os.makedirs(mod_p_path, exist_ok=True)

            unpacked_opp_path = os.path.join(self.temp_extract_path, "OPP")
            if not os.path.exists(unpacked_opp_path):
                raise Exception("Unpacked mod does not contain an 'OPP' folder.")
            
            windows_audio_path = os.path.join(unpacked_opp_path, "Content", "WwiseAudio", "Windows")
            
            if os.path.exists(windows_audio_path):
                needs_conversion = False
                
                for item in os.listdir(windows_audio_path):
                    item_path = os.path.join(windows_audio_path, item)
                    if os.path.isfile(item_path) and item.lower().endswith(".wem"):
                        needs_conversion = True
                        break
                
                if not needs_conversion:
                    for item in os.listdir(windows_audio_path):
                        item_path = os.path.join(windows_audio_path, item)
                        if os.path.isdir(item_path) and item != "Media":
                            for sub_item in os.listdir(item_path):
                                if sub_item.lower().endswith(".wem"):
                                    needs_conversion = True
                                    break
                        if needs_conversion: break

                if needs_conversion:
                   
                    should_convert = QtCore.QMetaObject.invokeMethod(
                        self.parent_app, 
                        "_ask_convert_old_mod_structure", 
                        QtCore.Qt.BlockingQueuedConnection,
                        QtCore.Q_RETURN_ARG(bool)
                    )
                    
                    if should_convert:
                        self.convert_structure_to_media(windows_audio_path)
                    else:
                        DEBUG.log("User declined structure conversion.")

            destination_opp_path = os.path.join(mod_p_path, "OPP")
            if os.path.exists(destination_opp_path):
                shutil.rmtree(destination_opp_path)
            shutil.copytree(unpacked_opp_path, destination_opp_path)

            bnk_deleted_count = 0
            for root, dirs, files in os.walk(destination_opp_path):
                for file in files:
                    if file.lower().endswith(".bnk"):
                        os.remove(os.path.join(root, file))
                        bnk_deleted_count += 1
            if bnk_deleted_count > 0:
                DEBUG.log(f"Removed {bnk_deleted_count} outdated BNK files from imported mod to prevent conflicts.")

            watermark_path = os.path.join(destination_opp_path, "CreatedByAudioEditor.txt")
            if os.path.exists(watermark_path):
                os.remove(watermark_path)

            profile_info = {
                "author": "Imported",
                "version": "1.0",
                "description": f"This profile was imported from '{os.path.basename(self.pak_path)}'."
            }
            with open(os.path.join(profile_path, "profile.json"), 'w', encoding='utf-8') as f:
                json.dump(profile_info, f, indent=2)
            
            self.parent_app.settings.data["mod_profiles"][self.profile_name] = profile_path
            self.parent_app.settings.save()
            
            self.finished.emit(True, self.tr("import_successful_message").format(
                pak_name=os.path.basename(self.pak_path),
                profile_name=self.profile_name
            ))

        except Exception as e:
            self.finished.emit(False, str(e))
        finally:
            if os.path.exists(self.temp_extract_path):
                shutil.rmtree(self.temp_extract_path)

    def convert_structure_to_media(self, windows_path):
        """Moves .wem files into a 'Media' subfolder structure."""
        DEBUG.log("Converting old mod structure to new 'Media' format...")
        
        media_root = os.path.join(windows_path, "Media")
        os.makedirs(media_root, exist_ok=True)
        
        items = list(os.listdir(windows_path))
        
        for item in items:
            item_path = os.path.join(windows_path, item)
            
            if item == "Media":
                continue
                
            if os.path.isfile(item_path) and item.lower().endswith(".wem"):
                dest_path = os.path.join(media_root, item)
                try:
                    if os.path.exists(dest_path):
                        os.remove(dest_path)
                    shutil.move(item_path, dest_path)
                    DEBUG.log(f"Moved {item} to Media root")
                except Exception as e:
                    DEBUG.log(f"Failed to move {item}: {e}", "ERROR")
                
            elif os.path.isdir(item_path):
                lang_folder_name = item
                lang_source_path = item_path
                
                has_wems = any(f.lower().endswith(".wem") for f in os.listdir(lang_source_path))
                
                if has_wems:
                    lang_media_dest = os.path.join(media_root, lang_folder_name)
                    os.makedirs(lang_media_dest, exist_ok=True)
                    
                    for sub_item in os.listdir(lang_source_path):
                        sub_item_path = os.path.join(lang_source_path, sub_item)
                        if os.path.isfile(sub_item_path) and sub_item.lower().endswith(".wem"):
                            dest_sub_path = os.path.join(lang_media_dest, sub_item)
                            try:
                                if os.path.exists(dest_sub_path):
                                    os.remove(dest_sub_path)
                                shutil.move(sub_item_path, dest_sub_path)
                                DEBUG.log(f"Moved {sub_item} to Media/{lang_folder_name}")
                            except Exception as e:
                                DEBUG.log(f"Failed to move {sub_item}: {e}", "ERROR")
                    
                    if not os.listdir(lang_source_path):
                        try:
                            os.rmdir(lang_source_path)
                        except OSError:
                            pass 
                    
        DEBUG.log("Structure conversion complete.")  
