"""
ResourceUpdaterThread - Updates game resources in background.
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


class ResourceUpdaterThread(QtCore.QThread):

    major_step_update = QtCore.pyqtSignal(str)
    log_update = QtCore.pyqtSignal(str)

    finished = QtCore.pyqtSignal(str, str)

    def __init__(self, parent_app, pak_path, update_audio, update_loc):
        super().__init__(parent_app)
        self.parent_app = parent_app
        self.tr = self.parent_app.tr
        self.pak_path = pak_path
        self.update_audio = update_audio
        self.update_loc = update_loc
        self.aes_key = "0x613E92E0F3CE880FC652EC86254E2581126AE86D63BA46550FB2CE0EC2EDA439"
        self.temp_extract_path = os.path.join(self.parent_app.base_path, "temp_extracted_resources")
        self._is_cancelled = False
        self.repak_process = None

    def cancel(self):
        self._is_cancelled = True
        if self.repak_process and self.repak_process.poll() is None:
            self.log_update.emit(f"--- {self.tr('update_cancelled_by_user')} ---")
            self.repak_process.terminate()
            DEBUG.log("repak.exe process terminated by user.")

    def run(self):
        status = "failure"
        message = "An unknown error occurred."
        try:
            self._cleanup_previous_session()
            if self._is_cancelled: return

            updated_resources = []

            if self.update_audio:
                if not self._unpack_and_process_audio():
                    if not self._is_cancelled: message = self.tr("unpack_failed")
                    return
                updated_resources.append("Audio (Wems)")
            
            if self._is_cancelled: return

            if self.update_loc:
                if not self._unpack_and_process_loc():
                    if not self._is_cancelled: message = self.tr("unpack_failed")
                    return
                updated_resources.append("Localization")

            if self._is_cancelled: return

            self.major_step_update.emit(self.tr("update_step_finishing"))
            self.log_update.emit(f"\n--- {self.tr('done')} ---")
            status = "success"
            message = self.tr("update_complete_msg").format(updated_resources="\n- ".join(updated_resources))

        except Exception as e:
            status = "failure"
            message = str(e)
        finally:
            if self._is_cancelled:
                status = "cancelled"
                message = self.tr("update_cancelled_by_user")
            
            self._cleanup_previous_session()
            self.finished.emit(status, message)

    def _cleanup_previous_session(self):
        self.log_update.emit("Preparing workspace...")
        if PSUTIL_AVAILABLE:
            for proc in psutil.process_iter(['name', 'exe', 'pid']):
                try:
                    if proc.info['name'].lower() == 'repak.exe' and os.path.normpath(proc.info['exe']) == os.path.normpath(self.parent_app.repak_path):
                        self.log_update.emit(f"Terminating lingering repak.exe (PID: {proc.pid})...")
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        if os.path.exists(self.temp_extract_path):
            self.log_update.emit("Cleaning up temporary directory...")
            try:
                shutil.rmtree(self.temp_extract_path)
                time.sleep(0.1)
            except Exception as e:
                self.log_update.emit(f"Warning: Could not clean temp directory: {e}")

    def _run_repak(self, path_to_unpack):
        self.log_update.emit(self.tr("unpacking_path").format(path_to_unpack=path_to_unpack))
        command = [self.parent_app.repak_path, "-a", self.aes_key, "unpack", self.pak_path, "-i", path_to_unpack, "-o", self.temp_extract_path]
        
        self.log_update.emit(f"\n-> {self.tr('update_unpacking_long_wait')}")

        try:
            self.repak_process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                startupinfo=startupinfo, creationflags=CREATE_NO_WINDOW, encoding='utf-8', errors='ignore'
            )
            stdout, stderr = self.repak_process.communicate()
            
            full_output = (stdout.strip() + "\n" + stderr.strip()).strip()
            if full_output:
                self.log_update.emit(full_output)

            if self._is_cancelled:
                self.log_update.emit("Repak process was cancelled.")
                return False

            if self.repak_process.returncode != 0:
                self.log_update.emit(self.tr("unpack_failed"))
                return False
            
            return True
        finally:
            self.repak_process = None

    def _unpack_and_process_audio(self):
        self.major_step_update.emit(self.tr("update_step_unpacking"))
        source_path_in_pak = "OPP/Content/WwiseAudio/Windows"
        if not self._run_repak(source_path_in_pak): return False
        if self._is_cancelled: return False
        
        extracted_content_path = os.path.join(self.temp_extract_path, "OPP", "Content", "WwiseAudio", "Windows")
        
        self.major_step_update.emit(self.tr("update_step_clearing"))
        if os.path.exists(self.parent_app.wem_root): shutil.rmtree(self.parent_app.wem_root)
        os.makedirs(self.parent_app.wem_root)
        
        self.major_step_update.emit(self.tr("update_step_moving"))

        sfx_path = os.path.join(self.parent_app.wem_root, "SFX")
        os.makedirs(sfx_path, exist_ok=True)

        for root, dirs, files in os.walk(extracted_content_path):
            if self._is_cancelled: return False
            
            rel_path = os.path.relpath(root, extracted_content_path)
            
            for file in files:
                src_file_path = os.path.join(root, file)
                dest_folder = ""

                if rel_path == ".": 
 
                    dest_folder = sfx_path
                elif rel_path == "Media":
             
                    dest_folder = sfx_path
                elif rel_path.startswith("Media"):
               
                    lang_name = os.path.basename(rel_path)
                    dest_folder = os.path.join(self.parent_app.wem_root, lang_name)
                else:
              
                    lang_name = rel_path
                    dest_folder = os.path.join(self.parent_app.wem_root, lang_name)

                os.makedirs(dest_folder, exist_ok=True)
                
                try:
                    shutil.move(src_file_path, os.path.join(dest_folder, file))
                except shutil.Error:
                    pass 
        return True
    def _unpack_and_process_loc(self):
        self.major_step_update.emit(self.tr("update_step_unpacking"))
        if not self._run_repak("OPP/Content/Localization"): return False
        if self._is_cancelled: return False

        extracted_content_path = os.path.join(self.temp_extract_path, "OPP", "Content", "Localization")
        loc_root = os.path.join(self.parent_app.base_path, "Localization")
        
        self.major_step_update.emit(self.tr("update_step_clearing"))
        if os.path.exists(loc_root): shutil.rmtree(loc_root)

        self.major_step_update.emit(self.tr("update_step_moving"))
        shutil.move(extracted_content_path, loc_root)
        return True
