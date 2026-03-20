"""
WemSubtitleApp - Main application window.
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

import requests
from packaging import version

from editor.constants import (
    CuePoint, Label, startupinfo, CREATE_NO_WINDOW, current_version,
    MATPLOTLIB_AVAILABLE, PSUTIL_AVAILABLE
)
from editor.translations import TRANSLATIONS, tr
from editor.core.settings import AppSettings
from editor.core.debug import DebugLogger
from editor.core.audio_player import AudioPlayer
from editor.core.wem_analyzer import WEMAnalyzer
from editor.core.bnk_editor import SoundEntry, BNKEditor
from editor.core.locres_manager import UnrealLocresManager
from editor.core.audio_converter import AudioToWavConverter
from editor.core.volume_processor import VolumeProcessor
from editor.core.wav_to_wem import WavToWemConverter
from editor.threads.subtitle_loader import SubtitleLoaderThread
from editor.threads.bnk_info_loader import BnkInfoLoader
from editor.threads.wem_scanner import WemScannerThread
from editor.threads.resource_updater import ResourceUpdaterThread
from editor.threads.save_subtitles import SaveSubtitlesThread
from editor.threads.import_mod import ImportModThread
from editor.threads.compile_mod import CompileModThread
from editor.threads.file_threads import AddFilesThread, AddSingleFileThread, DropFilesThread
from editor.dialogs.subtitle_editor import SubtitleEditor
from editor.dialogs.volume_editor import WemVolumeEditDialog
from editor.dialogs.batch_volume import BatchVolumeEditDialog
from editor.dialogs.audio_trim import AudioTrimDialog, WaveformWidget
from editor.dialogs.debug_window import DebugWindow
from editor.dialogs.statistics import StatisticsDialog
from editor.dialogs.profile_dialog import ProfileDialog
from editor.dialogs.profile_manager import ProfileManagerDialog
from editor.dialogs.progress import ProgressDialog
from editor.widgets.modern_button import ModernButton
from editor.widgets.audio_tree import AudioTreeWidget
from editor.widgets.search_bar import SearchBar
from editor.widgets.clickable_widgets import ClickableProgressBar, ClickableLabel
from editor.widgets.easter_egg import EasterEggLoader

class WemSubtitleApp(QtWidgets.QMainWindow):
    log_signal = QtCore.pyqtSignal(str, str)
    def __init__(self):
        super().__init__()
        DEBUG.log("=== OutlastTrials AudioEditor Starting ===")
        if getattr(sys, 'frozen', False):

            self.base_path = os.path.dirname(sys.executable)
        else:

            self.base_path = os.path.dirname(os.path.abspath(__file__))
        DEBUG.setup_logging(self.base_path)
        self.wem_index = None
        self.settings = AppSettings()
        self.translations = TRANSLATIONS
        self.current_lang = self.settings.data["ui_language"]
        
        self.setWindowTitle(self.tr("app_title"))
        icon_path = os.path.join(self.base_path, "data", "app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))
        else:
            self.setWindowIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay))
            DEBUG.log(f"Application icon not found at {icon_path}, using default.", "WARNING")
        
        
        
        DEBUG.log(f"Base path: {self.base_path}")
        
        self.data_path = os.path.join(self.base_path, "data")
        self.libs_path = os.path.join(self.base_path, "libs")   
        
        self.unreal_locres_path = os.path.join(self.data_path, "UnrealLocres.exe")
        self.repak_path = os.path.join(self.data_path, "repak.exe")
        self.vgmstream_path = os.path.join(self.data_path, "vgmstream", "vgmstream-cli.exe")
        
        
        self.wem_root = os.path.join(self.base_path, "Wems")
        json_path = os.path.join(self.wem_root, "SFX", "SoundbanksInfo.json")
        xml_path = os.path.join(self.wem_root, "SFX", "SoundbanksInfo.xml")
        if os.path.exists(json_path):
            self.soundbanks_path = json_path
        elif os.path.exists(xml_path):
            self.soundbanks_path = xml_path
        else:
            self.soundbanks_path = json_path 
        self.active_profile_name = None
        self.mod_p_path = None
        self.orphaned_cache_path = os.path.join(self.base_path, "orphaned_files_cache.json")
        self.check_required_files()
        self.orphaned_files_cache = []
        DEBUG.log(f"Paths configured:")
        DEBUG.log(f"  data_path: {self.data_path}")
        DEBUG.log(f"  unreal_locres_path: {self.unreal_locres_path}")
        DEBUG.log(f"  repak_path: {self.repak_path}")
        DEBUG.log(f"  vgmstream_path: {self.vgmstream_path}")

        self.locres_manager = UnrealLocresManager(self.unreal_locres_path)
        self.subtitles = {}
        self.original_subtitles = {}
        self.all_subtitle_files = {}
        self.key_to_file_map = {}
        self.all_files = self.load_all_soundbank_files(self.soundbanks_path)
        self.entries_by_lang = self.group_by_language()
        self.show_orphans_checkbox = QtWidgets.QCheckBox("Show Scanned Files")
        self.show_orphans_checkbox.setToolTip("Show/hide audio files found by scanning the 'Wems' folder that are not in the main database.")
        self.show_orphans_checkbox.setChecked(self.settings.data.get("show_orphaned_files", False))
        self.show_orphans_checkbox.stateChanged.connect(self.on_show_orphans_toggled)
        self.audio_player = AudioPlayer()
        self.temp_wav = None
        self.currently_playing_item = None
        self.is_playing_mod = False
        self.original_duration = 0
        self.mod_duration = 0
        self.original_size = 0
        self.mod_size = 0
        self.populated_tabs = set()
        self.modified_subtitles = set()
        self.dirty_subtitle_files = set()
        self.marked_items = {}
        if "marked_items" in self.settings.data:
            for key, data in self.settings.data["marked_items"].items():
                self.marked_items[key] = {
                    'color': QtGui.QColor(data['color']) if 'color' in data else None,
                    'tag': data.get('tag', '')
                }
        self.current_file_duration = 0

        self.debug_window = None
        self.updater_thread = None
        self.first_show_check_done = False
        self.auto_save_timer = QtCore.QTimer()
        self.auto_save_timer.timeout.connect(self.auto_save_subtitles)
        self.auto_save_enabled = False  
        self.bnk_cache_orig = {}
        self.bnk_cache_mod = {}
        self.bnk_loader_thread = None
        self.first_show_check_done = False
        self.current_bnk_request_id = 0
        self.search_timer = QtCore.QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(400) 
        self.search_timer.timeout.connect(self.perform_delayed_search)
        self.tree_loader_timer = QtCore.QTimer()
        self.tree_loader_timer.setInterval(0) 
        self.tree_loader_timer.timeout.connect(self._process_tree_batch)
        self.tree_loader_generator = None
        self.current_loading_lang = None
        self.create_ui()
        # QtCore.QTimer.singleShot(100, self.load_orphans_from_cache_or_scan) 
        self.apply_settings()
        self.restore_window_state()


        self.update_auto_save_timer()
        
        self.log_signal.connect(self.append_to_log_widget)
        DEBUG.log("=== OutlastTrials AudioEditor Started Successfully ===")
    def check_soundbanks_info(self):
        sfx_folder = os.path.join(self.wem_root, "SFX")
        
        json_path = os.path.join(sfx_folder, "SoundbanksInfo.json")
        xml_path = os.path.join(sfx_folder, "SoundbanksInfo.xml")

        if os.path.exists(json_path) or os.path.exists(xml_path):
            return 
        DEBUG.log("Neither SoundbanksInfo.json nor .xml found. Prompting user.", "WARNING")
        
        updater_tab_index = -1
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == self.tr("resource_updater_tab"):
                updater_tab_index = i
                break

        if updater_tab_index == -1:
            QtWidgets.QMessageBox.critical(self,
                                        self.tr("critical_file_missing_title"),
                                        self.tr("critical_file_missing_message"))
            return

        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle(self.tr("soundbanksinfo_missing_title"))
        msg_box.setText(self.tr("soundbanksinfo_missing_message")) 
        msg_box.setInformativeText(self.tr("soundbanksinfo_missing_details"))
        msg_box.setIcon(QtWidgets.QMessageBox.Warning)
        
        go_btn = msg_box.addButton(self.tr("go_to_updater_btn"), QtWidgets.QMessageBox.AcceptRole)
        later_btn = msg_box.addButton(self.tr("later_btn"), QtWidgets.QMessageBox.RejectRole)
        
        msg_box.exec_()
        
        if msg_box.clickedButton() == go_btn:
            self.tabs.setCurrentIndex(updater_tab_index)
    def check_for_loose_wems(self):
        if not os.path.isdir(self.wem_root):
            return False

        loose_files = []
        for item in os.listdir(self.wem_root):
            item_path = os.path.join(self.wem_root, item)
            if os.path.isfile(item_path):
                loose_files.append(item)

        if not loose_files:
            return False

        DEBUG.log(f"Found {len(loose_files)} loose files in the Wems root directory.", "WARNING")

        sfx_path = os.path.join(self.wem_root, "SFX")
        os.makedirs(sfx_path, exist_ok=True)

        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle(self.tr("wems_folder_loose_files_title"))

        msg_box.setText(self.tr("wems_folder_loose_files_message").format(count=len(loose_files)).replace(" (.wem/.bnk)", ""))
        msg_box.setInformativeText(self.tr("wems_folder_loose_files_details"))
        msg_box.setIcon(QtWidgets.QMessageBox.Question)
        
        move_btn = msg_box.addButton(self.tr("move_all_files_btn"), QtWidgets.QMessageBox.AcceptRole)
        ignore_btn = msg_box.addButton(self.tr("ignore_btn"), QtWidgets.QMessageBox.RejectRole)
        
        msg_box.exec_()

        if msg_box.clickedButton() == move_btn:
            moved_count = 0
            errors = []
            for filename in loose_files:
                source_path = os.path.join(self.wem_root, filename)
                dest_path = os.path.join(sfx_path, filename)
                try:

                    if os.path.exists(dest_path):
                        errors.append(f"{filename}: File already exists in SFX folder.")
                        DEBUG.log(f"Skipped moving '{filename}', it already exists in SFX.", "WARNING")
                        continue
                    shutil.move(source_path, dest_path)
                    moved_count += 1
                    DEBUG.log(f"Moved '{filename}' to SFX folder.")
                except Exception as e:
                    error_text = str(e)
                    errors.append(f"{filename}: {error_text}")
                    DEBUG.log(f"Error moving '{filename}': {error_text}", "ERROR")
            
            result_message = self.tr("move_complete_message").format(count=moved_count)
            if errors:
                result_message += "\n\n" + self.tr("move_complete_errors").format(count=len(errors), errors="\n".join(errors))
            
            result_message += self.tr("move_complete_restart_note")
            
            QtWidgets.QMessageBox.information(self, self.tr("move_complete_title"), result_message)

        return True
    def check_initial_resources(self):
        updater_tab_index = -1
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == self.tr("resource_updater_tab"):
                updater_tab_index = i
                break
        
        if updater_tab_index == -1:
            return False

        wems_path = os.path.join(self.base_path, "Wems")
        wems_exist = self._wems_folder_is_valid(wems_path)
        
        if not wems_exist:
            DEBUG.log("Wems folder is missing or invalid on startup.", "INFO")
            msg_box = QtWidgets.QMessageBox(self)
            msg_box.setWindowTitle(self.tr("initial_setup_title"))
            msg_box.setText(self.tr("wems_folder_missing_message"))
            msg_box.setIcon(QtWidgets.QMessageBox.Information)
            go_btn = msg_box.addButton(self.tr("go_to_updater_button"), QtWidgets.QMessageBox.AcceptRole)
            msg_box.addButton(self.tr("cancel"), QtWidgets.QMessageBox.RejectRole)
            msg_box.exec_()
            if msg_box.clickedButton() == go_btn:
                self.tabs.setCurrentIndex(updater_tab_index)
            return True 
        loc_path = os.path.join(self.base_path, "Localization")
        if not os.path.isdir(loc_path) or not any(f.endswith('.locres') for f in os.listdir(loc_path) if os.path.isdir(os.path.join(loc_path, f)) for f in os.listdir(os.path.join(loc_path, f))):
            loc_files_exist = False
            if os.path.exists(loc_path):
                for root, _, files in os.walk(loc_path):
                    if any(f.endswith('.locres') for f in files):
                        loc_files_exist = True
                        break
            
            if not loc_files_exist:
                DEBUG.log("Localization folder has no .locres files on startup.", "INFO")
                msg_box = QtWidgets.QMessageBox(self)
                msg_box.setWindowTitle(self.tr("initial_setup_title"))
                msg_box.setText(self.tr("localization_folder_missing_message"))
                msg_box.setIcon(QtWidgets.QMessageBox.Information)
                go_btn = msg_box.addButton(self.tr("go_to_updater_button"), QtWidgets.QMessageBox.AcceptRole)
                msg_box.addButton(self.tr("cancel"), QtWidgets.QMessageBox.RejectRole)
                msg_box.exec_()
                if msg_box.clickedButton() == go_btn:
                    self.tabs.setCurrentIndex(updater_tab_index)
                return True

        return False
    def _wems_folder_is_valid(self, directory):

        if not os.path.isdir(directory):
            return False
            
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.lower().endswith('.wem'):

                    return True
                    
        return False
    def showEvent(self, event):
        super().showEvent(event)
        
        if not self.first_show_check_done:
            self.first_show_check_done = True
            DEBUG.log("Application window shown for the first time. Scheduling initial checks.")
            
            def run_all_startup_checks():
    
                if self.check_initial_resources():
                    return
                
                loose_files_found = self.check_for_loose_wems()
            
                if not loose_files_found:
                    self.check_soundbanks_info()

                QtCore.QTimer.singleShot(1500, self.check_updates_on_startup)

            QtCore.QTimer.singleShot(100, run_all_startup_checks)
    def verify_bnk_sizes(self):
        if not self.ensure_active_profile():
            return

        progress = ProgressDialog(self, "Verifying Mod Integrity...")
        progress.show()
        
        self.verification_thread = threading.Thread(target=self._verify_mod_integrity_thread, args=(progress,))
        self.verification_thread.daemon = True
        self.verification_thread.start()

    
    def _verify_batch(self, wem_files, id_to_entry_map, bnk_files_info):
        mismatches = []
        bnk_editor_cache = {} 
        
        for wem_path in wem_files:
            wem_name = os.path.basename(wem_path)
            
            try:
                file_id = os.path.splitext(wem_name)[0]
                source_id = int(file_id)
            except ValueError:
                continue

            entry = id_to_entry_map.get(file_id)
            if not entry:
                continue
            
            real_wem_size = os.path.getsize(wem_path)
            
            bnk_info, mod_bnk_path = self._find_bnk_for_entry_with_cache(
                entry, bnk_files_info, bnk_editor_cache
            )

            if bnk_info:
                if bnk_info.file_size != real_wem_size:
                    mismatches.append({
                        "type": "Size Mismatch",
                        "bnk_path": mod_bnk_path,
                        "source_id": source_id,
                        "short_name": entry.get("ShortName", wem_name),
                        "bnk_size": bnk_info.file_size,
                        "wem_size": real_wem_size
                    })
            else:
                source_type = entry.get("Source", "")
                if source_type not in ["StreamedFiles", "MediaFilesNotInAnyBank"]:
                    mismatches.append({
                        "type": "BNK Entry Missing",
                        "bnk_path": "N/A",
                        "source_id": source_id,
                        "short_name": entry.get("ShortName", wem_name),
                        "bnk_size": "N/A",
                        "wem_size": real_wem_size
                    })
        
        return mismatches, len(wem_files)

    def _find_bnk_for_entry_with_cache(self, entry, bnk_files_info, cache):
        source_id = int(entry.get("Id"))
        
        for bnk_path, bnk_type in bnk_files_info:
            if bnk_path not in cache:
                try:
                    cache[bnk_path] = BNKEditor(bnk_path)
                except Exception:
                    continue
            
            original_bnk = cache[bnk_path]
            if not original_bnk.find_sound_by_source_id(source_id):
                continue
            
            if bnk_type == 'sfx':
                rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems", "SFX"))
                mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
            else:
                rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems"))
                mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)

            if os.path.exists(mod_bnk_path):
                if mod_bnk_path not in cache:
                    try:
                        cache[mod_bnk_path] = BNKEditor(mod_bnk_path)
                    except Exception:
                        continue
                
                mod_editor = cache[mod_bnk_path]
                entries = mod_editor.find_sound_by_source_id(source_id)
                if entries:
                    return entries[0], mod_bnk_path
        
        return None, None

    def _find_bnk_for_entry_optimized(self, entry, modified_bnks, bnk_editor_cache):
        source_id = int(entry.get("Id"))
        
        for bnk_path, (mod_bnk_path, bnk_type) in modified_bnks.items():

            if bnk_path not in bnk_editor_cache:
                try:
                    bnk_editor_cache[bnk_path] = BNKEditor(bnk_path)
                except Exception:
                    continue
            
            original_bnk = bnk_editor_cache[bnk_path]
            if not original_bnk.find_sound_by_source_id(source_id):
                continue
            
            if mod_bnk_path not in bnk_editor_cache:
                try:
                    bnk_editor_cache[mod_bnk_path] = BNKEditor(mod_bnk_path)
                except Exception:
                    continue
            
            mod_editor = bnk_editor_cache[mod_bnk_path]
            entries = mod_editor.find_sound_by_source_id(source_id)
            if entries:
                return entries[0], mod_bnk_path
        
        return None, None

    def _find_bnk_for_entry(self, entry):
        source_id = int(entry.get("Id"))
        
        bnk_files_info = self.find_relevant_bnk_files()

        for bnk_path, bnk_type in bnk_files_info:
            original_bnk = BNKEditor(bnk_path)
            if not original_bnk.find_sound_by_source_id(source_id):
                continue
            
            if bnk_type == 'sfx':
                rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems", "SFX"))
                mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
            else:
                rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems"))
                mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)

            if os.path.exists(mod_bnk_path):
                mod_editor = BNKEditor(mod_bnk_path)
                entries = mod_editor.find_sound_by_source_id(source_id)
                if entries:
                    return entries[0], mod_bnk_path
        
        return None, None
    def rebuild_bnk_index(self, confirm=True):
        if not self.ensure_active_profile():
            return

        if confirm:
            reply = QtWidgets.QMessageBox.question(
                self, 
                self.tr("rebuild_bnk_confirm_title"), 
                self.tr("rebuild_bnk_confirm_text"), 
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply == QtWidgets.QMessageBox.No:
                return

        progress = ProgressDialog(self, self.tr("rebuilding_mod_bnk"))
        progress.show()
        
        self.rebuild_thread = threading.Thread(target=self._rebuild_bnk_thread, args=(progress,))
        self.rebuild_thread.daemon = True
        self.rebuild_thread.start()
    def find_all_original_bnks(self):
        all_bnks = []
        wems_root = os.path.join(self.base_path, "Wems")
        if not os.path.exists(wems_root):
            return []
        for root, _, files in os.walk(wems_root):
            for file in files:
                if file.lower().endswith('.bnk'):
                    bnk_type = 'sfx' if os.path.basename(root) == "SFX" else 'lang'
                    all_bnks.append((os.path.join(root, file), bnk_type))
        return all_bnks
    def _rebuild_bnk_thread(self, progress):
        try:
            DEBUG.log("--- Starting BNK Rebuild (Robust Mode) ---")
            QtCore.QMetaObject.invokeMethod(progress, "set_progress", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(int, 5), QtCore.Q_ARG(str, "Scanning modified audio files..."))

            mod_audio_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows")
            modified_wem_files = {}
            
            if os.path.exists(mod_audio_path):
                for root, _, files in os.walk(mod_audio_path):
                    for file in files:
                        if file.lower().endswith('.wem'):
                            file_id = os.path.splitext(file)[0]
                           
                            if file_id.isdigit():
                                full_path = os.path.join(root, file)
                                modified_wem_files[file_id] = os.path.getsize(full_path)

            if not modified_wem_files:
                raise FileNotFoundError("No modified audio files (IDs) found in MOD_P to rebuild.")

            total_wems = len(modified_wem_files)
            progress.details_updated.emit(f"Found {total_wems} modified WEM files.")
            
            all_original_bnks = self.find_all_original_bnks()
            
            bnk_update_map = {}
            
            bnk_editor_cache = {}

            for i, (file_id, new_size) in enumerate(modified_wem_files.items()):
                progress_percent = 10 + int((i / total_wems) * 30)
                if i % 10 == 0:
                    QtCore.QMetaObject.invokeMethod(progress, "set_progress", QtCore.Qt.QueuedConnection,
                                                    QtCore.Q_ARG(int, progress_percent),
                                                    QtCore.Q_ARG(str, f"Mapping ID {file_id}..."))
                
                found_parent = False
                source_id_int = int(file_id)

                for original_bnk_path, bnk_type in all_original_bnks:
                    try:
                        if original_bnk_path not in bnk_editor_cache:
                           bnk_editor_cache[original_bnk_path] = BNKEditor(original_bnk_path)
                        
                        editor = bnk_editor_cache[original_bnk_path]
                        
                        if editor.find_sound_by_source_id(source_id_int):
                            if original_bnk_path not in bnk_update_map:
                                bnk_update_map[original_bnk_path] = {'type': bnk_type, 'wems': {}}
                            
                            bnk_update_map[original_bnk_path]['wems'][file_id] = new_size
                            found_parent = True
                       
                            break 
                    except Exception as e:
                        DEBUG.log(f"Error reading BNK {os.path.basename(original_bnk_path)}: {e}", "WARNING")
                
                if not found_parent:
                    DEBUG.log(f"Warning: ID {file_id} not found in any known SoundBank.", "WARNING")

            updated_count = 0
            created_count = 0
            total_bnks = len(bnk_update_map)
            
            for i, (original_bnk_path, data) in enumerate(bnk_update_map.items()):
                bnk_type = data['type']
                wems_to_update = data['wems'] # {id_str: size}
                
                progress_percent = 40 + int((i / total_bnks) * 60)
                bnk_name = os.path.basename(original_bnk_path)
                QtCore.QMetaObject.invokeMethod(progress, "set_progress", QtCore.Qt.QueuedConnection,
                                                QtCore.Q_ARG(int, progress_percent),
                                                QtCore.Q_ARG(str, f"Updating {bnk_name}..."))

                if bnk_type == 'sfx':
                    rel_path = os.path.relpath(original_bnk_path, os.path.join(self.wem_root, "SFX"))
               
                    if rel_path.startswith(".."): rel_path = os.path.basename(original_bnk_path)
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                else:
                    rel_path = os.path.relpath(original_bnk_path, self.wem_root)
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)

                old_fx_flags = {}
                if os.path.exists(mod_bnk_path):
                    try:
                        old_mod_editor = BNKEditor(mod_bnk_path)
                        for entry in old_mod_editor.find_all_sounds():
                            old_fx_flags[str(entry.source_id)] = entry.override_fx
                        os.remove(mod_bnk_path) 
                    except Exception: 
                        pass
                
                os.makedirs(os.path.dirname(mod_bnk_path), exist_ok=True)
                shutil.copy2(original_bnk_path, mod_bnk_path)
                created_count += 1

                new_mod_editor = BNKEditor(mod_bnk_path)
                
                file_modified = False
                
                for file_id_str, new_size in wems_to_update.items():
                    source_id = int(file_id_str)
                    
                    fx_flag = old_fx_flags.get(file_id_str) 
                    
                    if new_mod_editor.modify_sound(source_id, new_size=new_size, override_fx=fx_flag):
                        updated_count += 1
                        file_modified = True
                        DEBUG.log(f"Updated {bnk_name}: ID {source_id} -> {new_size} bytes")
                    else:
                        DEBUG.log(f"FAILED to update {bnk_name}: ID {source_id} not found in binary scan!", "ERROR")

                if file_modified:
                    new_mod_editor.save_file()
                    
                    for file_id_str in wems_to_update.keys():
                        self.invalidate_bnk_cache(int(file_id_str))
                else:
                    DEBUG.log(f"No changes made to {bnk_name}, keeping original copy.", "WARNING")

            self.bnk_cache_mod.clear()
            
            QtCore.QMetaObject.invokeMethod(progress, "close", QtCore.Qt.QueuedConnection)
            
            final_message = (f"Rebuild Complete!\n\n"
                             f"Processed {len(modified_wem_files)} modified audio files.\n"
                             f"Re-created {created_count} BNK files.\n"
                             f"Updated {updated_count} size entries.")

            QtCore.QMetaObject.invokeMethod(self, "show_message_box", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(int, QtWidgets.QMessageBox.Information),
                                            QtCore.Q_ARG(str, self.tr("rebuild_complete_title")),
                                            QtCore.Q_ARG(str, final_message))
            
            current_lang = self.get_current_language()
            if current_lang:
                QtCore.QMetaObject.invokeMethod(self, "populate_tree", QtCore.Qt.QueuedConnection,
                                                QtCore.Q_ARG(str, current_lang))

        except Exception as e:
            import traceback
            DEBUG.log(f"BNK Rebuild Critical Error: {e}\n{traceback.format_exc()}", "ERROR")
            QtCore.QMetaObject.invokeMethod(progress, "close", QtCore.Qt.QueuedConnection)
            QtCore.QMetaObject.invokeMethod(self, "_show_bnk_verification_error", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, str(e)))
    @QtCore.pyqtSlot(list)
    def _show_bnk_verification_report(self, mismatches):

        if not mismatches:
            QtWidgets.QMessageBox.information(self, "Verification Complete", "All modified audio files are consistent with their BNK entries. No issues found!")
            return

        report_text = f"Found {len(mismatches)} issues in your mod.\n\n"
        report_text += "These problems can cause sounds to not play correctly in the game.\n\n"
        report_text += "Do you want to automatically fix these entries?"

        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Mod Integrity Issues Found")
        dialog.setText(report_text)
        
        detailed_report = ""
        for item in mismatches:
            if item['type'] == 'Size Mismatch':
                bnk_name = os.path.basename(item['bnk_path'])
                detailed_report += (
                    f"Type: {item['type']} in {bnk_name}\n"
                    f"  Sound: {item['short_name']} (ID: {item['source_id']})\n"
                    f"  - BNK Size: {item['bnk_size']} bytes\n"
                    f"  - WEM Size: {item['wem_size']} bytes\n\n"
                )
            elif item['type'] == 'BNK Entry Missing':
                 detailed_report += (
                    f"Type: {item['type']}\n"
                    f"  Sound: {item['short_name']} (ID: {item['source_id']})\n"
                    f"  - A .wem file exists, but no corresponding entry was found in any modified .bnk file.\n\n"
                )
        dialog.setDetailedText(detailed_report)
        
        fix_btn = dialog.addButton("Fix All", QtWidgets.QMessageBox.AcceptRole)
        cancel_btn = dialog.addButton(QtWidgets.QMessageBox.Cancel)
        dialog.setDefaultButton(fix_btn)
        
        self.show_dialog(dialog)
        
        if dialog.clickedButton() == fix_btn:
            self.fix_bnk_mismatches(mismatches)

    @QtCore.pyqtSlot(str)
    def _show_bnk_verification_error(self, error_message):

        QtWidgets.QMessageBox.critical(self, "Verification Error", f"An error occurred during verification:\n\n{error_message}")

    def fix_bnk_mismatches(self, mismatches):

        progress = ProgressDialog(self, "Fixing Mod Issues...")
        progress.show()
        
        fixable_mismatches = [item for item in mismatches if item['type'] == 'Size Mismatch']

        if not fixable_mismatches:
            progress.close()
            QtWidgets.QMessageBox.information(self, "Fix Complete", "No automatically fixable issues were found (e.g., 'BNK Entry Missing').")
            return
        
        fixed_count = 0
        error_count = 0
        
        fixes_by_bnk = {}
        for item in fixable_mismatches:
            bnk_path = item['bnk_path']
            if bnk_path not in fixes_by_bnk:
                fixes_by_bnk[bnk_path] = []
            fixes_by_bnk[bnk_path].append(item)
            
        total_bnks_to_fix = len(fixes_by_bnk)
        
        for i, (bnk_path, items_to_fix) in enumerate(fixes_by_bnk.items()):
            bnk_name = os.path.basename(bnk_path)
            progress_percent = int((i / total_bnks_to_fix) * 100)
            QtCore.QMetaObject.invokeMethod(progress, "set_progress", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(int, progress_percent), QtCore.Q_ARG(str, f"Fixing {bnk_name}..."))
            
            try:
                editor = BNKEditor(bnk_path)
                modified = False
                for item in items_to_fix:
                    if editor.modify_sound(item['source_id'], new_size=item['wem_size']):
                        fixed_count += 1
                        modified = True
                
                if modified:
                    editor.save_file()
   
                    for item in items_to_fix:
                        self.invalidate_bnk_cache(item['source_id'])

            except Exception as e:
                error_count += len(items_to_fix)
                DEBUG.log(f"Error fixing {bnk_name}: {e}", "ERROR")

        progress.close()
        
        message = f"Fixed {fixed_count} size mismatch issues."
        if error_count > 0:
            message += f"\nFailed to fix {error_count} entries. See debug console for details."
            
        QtWidgets.QMessageBox.information(self, "Fix Complete", message)    
    @QtCore.pyqtSlot(int, str, str)    
    def show_message_box(self, icon, title, text, informative_text="", detailed_text="", buttons=QtWidgets.QMessageBox.Ok):
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(icon)
        msg.setWindowTitle(title)
        msg.setText(text)
        if informative_text:
            msg.setInformativeText(informative_text)
        if detailed_text:
            msg.setDetailedText(detailed_text)
        msg.setStandardButtons(buttons)
        msg.setWindowFlags(msg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint) 
        msg.show() 
        msg.raise_() 
        msg.activateWindow() 
        return msg.exec_() 

    def show_dialog(self, dialog):
        dialog.setWindowFlags(dialog.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog.exec_()
    def get_mod_path(self, file_id, lang):
        if not self.mod_p_path:
            return None
            
        if lang != "SFX":
            new_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", lang, f"{file_id}.wem")
        else:
            new_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", f"{file_id}.wem")
            
        if os.path.exists(new_path):
            return new_path
       
        if lang != "SFX":
            old_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", lang, f"{file_id}.wem")
        else:
            old_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", f"{file_id}.wem")
            
        if os.path.exists(old_path):
            return old_path

        return new_path

    @QtCore.pyqtSlot(dict)
    def _add_orphaned_entry(self, entry):

        self.all_files.append(entry)
        lang = entry.get("Language", "SFX")
        self.entries_by_lang.setdefault(lang, []).append(entry)

        if lang in self.tab_widgets:
            widgets = self.tab_widgets[lang]
            tree = widgets["tree"]
            
            scanned_group_name = "Scanned From Filesystem"
            items = tree.findItems(scanned_group_name, QtCore.Qt.MatchStartsWith, 0)
            group_item = items[0] if items else None
            
            if not group_item:
                group_item = QtWidgets.QTreeWidgetItem(tree, [scanned_group_name, "", "", "", ""])
                group_item.setExpanded(True)
            
            self.add_tree_item(group_item, entry, lang)
            group_item.setText(0, f"{scanned_group_name} ({group_item.childCount()})")
            
            current_tab_index = self.tabs.indexOf(widgets["tree"].parent().parent())
            if current_tab_index != -1:
                total_count = len(self.entries_by_lang.get(lang, []))
                self.tabs.setTabText(current_tab_index, f"{lang} ({total_count})")

    def initialize_profiles_and_ui(self):

        profiles_root = os.path.join(self.base_path, "Profiles")
        legacy_mod_p_path = os.path.join(self.base_path, "MOD_P")
        
        if not os.path.isdir(profiles_root):
            DEBUG.log("Root 'Profiles' folder not found. Running first-time setup or migration.")
            
            if os.path.isdir(legacy_mod_p_path):
                self.handle_legacy_mod_p_migration(legacy_mod_p_path, profiles_root)
            else: 
                self.handle_new_user_setup(profiles_root)
        
        self.load_profiles_from_settings()
        return True

    def handle_new_user_setup(self, profiles_root):
        DEBUG.log("Performing automatic new user setup.")
        try:

            os.makedirs(profiles_root, exist_ok=True)
            
            default_profile_name = "Default"
            profile_path = os.path.join(profiles_root, default_profile_name)
            new_mod_p_path = os.path.join(profile_path, f"{default_profile_name}_P")
            
            os.makedirs(new_mod_p_path, exist_ok=True)
            
            profile_json_path = os.path.join(profile_path, "profile.json")
            profile_info = {
                "author": "New User", "version": "1.0",
                "description": "Default profile created on first launch."
            }
            with open(profile_json_path, 'w', encoding='utf-8') as f:
                json.dump(profile_info, f, indent=2)

            self.settings.data["mod_profiles"] = {default_profile_name: profile_path}
            self.settings.data["active_profile"] = default_profile_name
            self.settings.save()

            self.show_message_box(
                QtWidgets.QMessageBox.Information,
                self.tr("setup_complete_title"),
                self.tr("setup_complete_msg").format(mods_root=profiles_root)
            )
            return True

        except Exception as e:
            self.show_message_box(
                QtWidgets.QMessageBox.Critical,
                self.tr("setup_failed_title"),
                self.tr("setup_failed_msg").format(e=e)
            )
            return False
    def handle_legacy_mod_p_migration(self, legacy_mod_p_path, profiles_root):
        DEBUG.log(f"Performing automatic migration of '{legacy_mod_p_path}'")
        try:
            os.makedirs(profiles_root, exist_ok=True)
            
            default_profile_name = "Default"
            profile_path = os.path.join(profiles_root, default_profile_name)
            new_mod_p_path = os.path.join(profile_path, f"{default_profile_name}_P")
            
            if not os.path.exists(profile_path):
                os.makedirs(profile_path)
            
            shutil.move(legacy_mod_p_path, new_mod_p_path)
            
            profile_json_path = os.path.join(profile_path, "profile.json")
            profile_info = {
                "author": "Migrated", "version": "1.0",
                "description": "This profile was automatically migrated from the legacy MOD_P folder."
            }
            with open(profile_json_path, 'w', encoding='utf-8') as f:
                json.dump(profile_info, f, indent=2)

            self.settings.data["mod_profiles"] = {default_profile_name: profile_path}
            self.settings.data["active_profile"] = default_profile_name
            self.settings.save()

            self.show_message_box(
                QtWidgets.QMessageBox.Information,
                self.tr("migration_complete_title"),
                self.tr("migration_complete_msg").format(mods_root=profiles_root)
            )

        except Exception as e:
            self.show_message_box(
                QtWidgets.QMessageBox.Critical,
                self.tr("migration_failed_title"),
                self.tr("migration_failed_msg").format(e=e)
            )
            if os.path.exists(new_mod_p_path):
                 shutil.move(new_mod_p_path, legacy_mod_p_path)

    def ensure_active_profile(self):
        if self.active_profile_name and self.mod_p_path:
            return True

        reply = self.show_message_box(
            QtWidgets.QMessageBox.Information,
            "No Active Profile",
            "No mod profile is currently active. Please create or activate a profile first.",
            buttons=QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)

        if reply == QtWidgets.QMessageBox.Ok:
            self.show_profile_manager()
        
        return self.active_profile_name and self.mod_p_path is not None
    @QtCore.pyqtSlot(int)
    def _on_scan_finished(self, count):

        DEBUG.log(f"Orphan scan finished. Found {count} additional files.")
        self.status_bar.showMessage(f"Scan complete. Found {count} additional audio files.", 5000)
    def get_original_path(self, file_id, lang):
        standard_path = os.path.join(self.wem_root, lang, f"{file_id}.wem")
        if os.path.exists(standard_path):
            return standard_path
            
        if lang == "SFX":
            media_path = os.path.join(self.wem_root, "Media", f"{file_id}.wem")
        else:
            media_path = os.path.join(self.wem_root, "Media", lang, f"{file_id}.wem")
            
        if os.path.exists(media_path):
            return media_path
            
        if lang == "SFX":
            sfx_path = os.path.join(self.wem_root, "SFX", f"{file_id}.wem")
            if os.path.exists(sfx_path):
                return sfx_path 
                
        return standard_path
    def find_relevant_bnk_files(self, force_all=False):

        bnk_files_info = []
        bnk_paths_set = set()
        wems_root = os.path.join(self.base_path, "Wems")
        if not os.path.exists(wems_root):
            return []

        scan_folders = []
        
        if force_all:
            DEBUG.log("Force all BNKs: Scanning all subdirectories in Wems folder.")
            for item in os.listdir(wems_root):
                path = os.path.join(wems_root, item)
                if os.path.isdir(path):
                    scan_folders.append(path)

        else:
            sfx_path = os.path.join(wems_root, "SFX")
            if os.path.exists(sfx_path):
                scan_folders.append(sfx_path)

            lang_setting = self.settings.data.get("wem_process_language", "english")
            lang_folder_name = "English(US)" if lang_setting == "english" else "Francais"
            lang_path = os.path.join(wems_root, lang_folder_name)
            if os.path.exists(lang_path):
                scan_folders.append(lang_path)
            DEBUG.log(f"Standard scan: looking for BNKs for language '{lang_setting}'.")

        for folder_path in scan_folders:
            bnk_type = 'sfx' if os.path.basename(folder_path) == "SFX" else 'lang'
            try:
                for file in os.listdir(folder_path):
                    if file.lower().endswith('.bnk'):
                        full_path = os.path.join(folder_path, file)
                        if full_path not in bnk_paths_set:
                            bnk_files_info.append((full_path, bnk_type))
                            bnk_paths_set.add(full_path)
            except OSError as e:
                DEBUG.log(f"Can't read folder {folder_path}: {e}", "WARNING")

        mode_str = "FORCE ALL" if force_all else "STANDARD"
        DEBUG.log(f"Found {len(bnk_files_info)} relevant BNK files (Mode: {mode_str}).")
        return bnk_files_info
    def _build_wem_index(self):
        if self.wem_index is not None:
            return 

        DEBUG.log("Building WEM file index (scanning Wems folder)...")
        self.wem_index = {}

        wems_folder = os.path.join(self.base_path, "Wems")
        if not os.path.exists(wems_folder):
            DEBUG.log("Wems folder not found")
            return

        for root, dirs, files in os.walk(wems_folder):
       
            
            for file in files:
                if file.lower().endswith('.wem'):
                    file_id = os.path.splitext(file)[0]
                    file_path = os.path.join(root, file)

                    rel_path = os.path.relpath(root, wems_folder)
                    parts = rel_path.split(os.sep)
                   
                    folder_name = "SFX"
                    
                    if rel_path == ".":
                        folder_name = "SFX"
                    elif parts[0] == "Media":
                        if len(parts) > 1:
                            folder_name = parts[1] # Media/English(US) -> English(US)
                        else:
                            folder_name = "SFX" # Media -> SFX
                    elif parts[0] == "SFX":
                        folder_name = "SFX"
                    else:
                        folder_name = parts[0] # English(US) -> English(US)

                    if file_id not in self.wem_index:
                        self.wem_index[file_id] = {}

                    self.wem_index[file_id][folder_name] = {
                        'path': file_path,
                        'size': os.path.getsize(file_path)
                    }

        DEBUG.log(f"WEM index built: {len(self.wem_index)} unique IDs found.")
    def update_auto_save_timer(self):
        auto_save_setting = self.settings.data.get("auto_save", True)
        
        if self.auto_save_timer.isActive():
            self.auto_save_timer.stop()
            DEBUG.log("Auto-save timer stopped")
        

        if auto_save_setting:
            self.auto_save_timer.start(300000) 
            self.auto_save_enabled = True
            DEBUG.log("Auto-save timer started (5 minutes)")
        else:
            self.auto_save_enabled = False
            DEBUG.log("Auto-save disabled")

    def auto_save_subtitles(self):
        if not self.auto_save_enabled or not self.settings.data.get("auto_save", True):
            DEBUG.log("Auto-save skipped - disabled")
            return
        
        if not self.modified_subtitles:
            DEBUG.log("Auto-save skipped - no changes")
            return
        
        DEBUG.log(f"Auto-saving {len(self.modified_subtitles)} modified subtitles...")
        
        try:

            self.status_bar.showMessage("Auto-saving...", 2000)
            
            QtCore.QTimer.singleShot(100, self.perform_auto_save)
            
        except Exception as e:
            DEBUG.log(f"Auto-save error: {e}", "ERROR")

    def perform_auto_save(self):
        try:
            self.save_subtitles_to_file()
            DEBUG.log(f"Auto-save completed successfully")
            self.status_bar.showMessage("Auto-saved", 1000)
        except Exception as e:
            DEBUG.log(f"Auto-save failed: {e}", "ERROR")
            self.status_bar.showMessage("Auto-save failed", 2000)

    def delete_mod_audio(self, entry, lang):
        """Delete modified audio file(s) and revert BNK changes"""
        widgets = self.tab_widgets.get(lang) 
        if not widgets:
            DEBUG.log(f"No widgets found for language: {lang}", "WARNING")
            return
        
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        if len(items) > 1:
            file_list = []
            for item in items:
                if item.childCount() == 0:
                    entry_data = item.data(0, QtCore.Qt.UserRole)
                    if entry_data:
                        file_id = entry_data.get("Id", "")
                        mod_path = self.get_mod_path(file_id, lang) 
                        if mod_path and os.path.exists(mod_path):
                            file_list.append(entry_data)
            
            if not file_list:
                return
                
            reply = QtWidgets.QMessageBox.question(
                self, "Delete Multiple Mod Audio",
                f"Delete modified audio for {len(file_list)} files?\nThis will also revert changes in BNK files.\n\nThis action cannot be undone.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            
            if reply == QtWidgets.QMessageBox.Yes:
                deleted_count = 0
                for entry_to_delete in file_list:
                    self._perform_single_delete(entry_to_delete, lang)
                    deleted_count += 1
                
                QtCore.QTimer.singleShot(0, lambda: self.populate_tree(lang))
                self.status_bar.showMessage(f"Deleted {deleted_count} mod audio files", 3000)
            return

        if not items or items[0].childCount() > 0:
            return
            
        entry_to_delete = items[0].data(0, QtCore.Qt.UserRole)
        if not entry_to_delete:
            return
        
        file_id = entry_to_delete.get("Id", "")
        shortname = entry_to_delete.get("ShortName", "")
        
        mod_wem_path = self.get_mod_path(file_id, lang)
        
        if not mod_wem_path or not os.path.exists(mod_wem_path):
            QtWidgets.QMessageBox.information(self, "Info", f"No modified audio found for {shortname}")
            return
            
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Mod Audio",
            f"Delete modified audio for:\n{shortname}\nThis will also revert changes in BNK files.\n\nThis action cannot be undone.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            self._perform_single_delete(entry_to_delete, lang)
            QtCore.QTimer.singleShot(0, lambda: self.populate_tree(lang))
    def _perform_single_delete(self, entry, lang):
        file_id = entry.get("Id", "")
        shortname = entry.get("ShortName", "")
        source_id = int(file_id)

        mod_wem_path = self.get_mod_path(file_id, lang)

        try:
          
            if mod_wem_path and os.path.exists(mod_wem_path):
                os.remove(mod_wem_path)
                DEBUG.log(f"Deleted wem audio: {mod_wem_path}")
            
            old_paths = [
                os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", lang, f"{file_id}.wem"),
                os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", f"{file_id}.wem")
            ]
            for p in old_paths:
                if os.path.exists(p):
                    os.remove(p)
                    DEBUG.log(f"Deleted legacy wem audio: {p}")

            bnk_reverted_count = 0
            bnk_files_info = self.find_relevant_bnk_files()

            for bnk_path, bnk_type in bnk_files_info:
                if bnk_type == 'sfx':
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems", "SFX"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                else:
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                
                if not os.path.exists(mod_bnk_path):
                    continue

                original_bnk = BNKEditor(bnk_path)
                original_entries = original_bnk.find_sound_by_source_id(source_id)
                
                if not original_entries:
                    continue
                
                original_entry = original_entries[0]

                mod_bnk_editor = BNKEditor(mod_bnk_path)
               
                if mod_bnk_editor.modify_sound(source_id, 
                                            new_size=original_entry.file_size, 
                                            override_fx=original_entry.override_fx,
                                            find_by_size=None):
                    mod_bnk_editor.save_file()
                    self.invalidate_bnk_cache(source_id)
                    DEBUG.log(f"BNK {os.path.basename(mod_bnk_path)} restored to original values.")
                    bnk_reverted_count += 1
         
            
            if bnk_reverted_count > 0:
                self.status_bar.showMessage(f"Deleted mod audio and restored {bnk_reverted_count} BNK entries for {shortname}", 3000)
            else:
                self.status_bar.showMessage(f"Deleted mod audio for {shortname} (No BNK changes found)", 3000)

        except Exception as e:
            DEBUG.log(f"Error deleting {shortname}: {e}", "ERROR")
            QtWidgets.QMessageBox.warning(self, "Error", f"Failed to process deletion for {shortname}: {str(e)}")
    def invalidate_bnk_cache(self, source_id: int):
        source_id_to_invalidate = int(source_id)
        DEBUG.log(f"Invalidating BNK cache for Source ID: {source_id_to_invalidate}")

        for bnk_path in list(self.bnk_cache_mod.keys()):
            if source_id_to_invalidate in self.bnk_cache_mod[bnk_path]:
                del self.bnk_cache_mod[bnk_path][source_id_to_invalidate]
                DEBUG.log(f"  > Removed ID {source_id_to_invalidate} from mod cache for {os.path.basename(bnk_path)}")

        for bnk_path in list(self.bnk_cache_orig.keys()):
            if source_id_to_invalidate in self.bnk_cache_orig[bnk_path]:
                del self.bnk_cache_orig[bnk_path][source_id_to_invalidate]
                DEBUG.log(f"  > Removed ID {source_id_to_invalidate} from original cache for {os.path.basename(bnk_path)}")        
    def tr(self, key):
        """Translate key to current language"""
        return self.translations.get(self.current_lang, {}).get(key, key)
        
    def check_required_files(self):
        """Check if all required files exist"""
        missing_files = []
        
        required_files = [
            (self.unreal_locres_path, "UnrealLocres.exe"),
            (self.repak_path, "repak.exe"),
            (self.vgmstream_path, "vgmstream-cli.exe")
        ]
        
        for file_path, file_name in required_files:
            if not os.path.exists(file_path):
                missing_files.append(file_name)
                DEBUG.log(f"Missing required file: {file_path}", "WARNING")
        
        if missing_files:
            msg = f"Missing required files in data folder:\n" + "\n".join(f"• {f}" for f in missing_files)
            msg += "\n\nPlease ensure all files are in the correct location."
            QtWidgets.QMessageBox.warning(None, "Missing Files", msg)
            
    def load_subtitles(self):
        DEBUG.log("=== Loading Subtitles (Profile-aware) ===")
        self.subtitles = {}
        self.original_subtitles = {}
        self.all_subtitle_files = {}

        self.scan_localization_folder()

        subtitle_lang = self.settings.data["subtitle_lang"]
        self.load_subtitles_for_language(subtitle_lang)

        self.modified_subtitles.clear()
        for key, value in self.subtitles.items():
            if key in self.original_subtitles and self.original_subtitles[key] != value:
                self.modified_subtitles.add(key)

            elif key not in self.original_subtitles:
                self.modified_subtitles.add(key)
        
        DEBUG.log(f"Found {len(self.modified_subtitles)} modified subtitles after comparing with originals.")
        DEBUG.log("=== Subtitle Loading Complete ===")

    def scan_localization_folder(self):
        """Scan Localization folder for all subtitle files"""
        localization_path = os.path.join(self.base_path, "Localization")
        DEBUG.log(f"Scanning localization folder: {localization_path}")
        
        self.all_subtitle_files = {}
        
        if not os.path.exists(localization_path):
            DEBUG.log("Localization folder not found, creating structure", "WARNING")

            os.makedirs(localization_path, exist_ok=True)

            default_langs = ["en", "ru-RU", "fr-FR", "de-DE", "es-ES"]
            for lang in default_langs:
                lang_path = os.path.join(localization_path, "OPP_Subtitles", lang)
                os.makedirs(lang_path, exist_ok=True)

                locres_path = os.path.join(lang_path, "OPP_Subtitles.locres")
                if not os.path.exists(locres_path):

                    empty_subtitles = {}
                    self.create_empty_locres_file(locres_path, empty_subtitles)

            return self.scan_localization_folder()

        try:
            for item in os.listdir(localization_path):
                item_path = os.path.join(localization_path, item)
                
                if not os.path.isdir(item_path):
                    continue
                    
                DEBUG.log(f"Found subtitle category: {item}")

                try:
                    for lang_item in os.listdir(item_path):
                        lang_path = os.path.join(item_path, lang_item)
                        
                        if not os.path.isdir(lang_path):
                            continue
                            
                        DEBUG.log(f"Found language folder: {lang_item} in {item}")
   
                        try:
                            for file_item in os.listdir(lang_path):
                                if file_item.endswith('.locres') and not file_item.endswith('_working.locres'):
                                    file_path = os.path.join(lang_path, file_item)
                                    
                                    key = f"{item}/{lang_item}/{file_item}"
                                    self.all_subtitle_files[key] = {
                                        'path': file_path,
                                        'category': item,
                                        'language': lang_item,
                                        'filename': file_item,
                                        'relative_path': f"Localization/{item}/{lang_item}/{file_item}"
                                    }
                                    
                                    DEBUG.log(f"Found subtitle file: {key}")
                                    
                        except PermissionError:
                            DEBUG.log(f"Permission denied accessing {lang_path}", "WARNING")
                            continue
                            
                except PermissionError:
                    DEBUG.log(f"Permission denied accessing {item_path}", "WARNING")
                    continue
                    
        except Exception as e:
            DEBUG.log(f"Error scanning localization folder: {e}", "ERROR")
        
        DEBUG.log(f"Total subtitle files found: {len(self.all_subtitle_files)}")

    def create_empty_locres_file(self, path, subtitles):
        """Create an empty locres file using a two-step process."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                pass 
            DEBUG.log(f"Created empty placeholder locres file at: {path}")

            csv_path = path.replace('.locres', '.csv')
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)

                writer.writerow(["Key", "Source", "Translation"])
            
            if os.path.exists(self.unreal_locres_path):
                result = subprocess.run(
                    [self.unreal_locres_path, "import", path, csv_path],
                    capture_output=True,
                    text=True,
                    cwd=os.path.dirname(self.unreal_locres_path) or ".",
                    startupinfo=startupinfo,
                    creationflags=CREATE_NO_WINDOW,
                    encoding='utf-8',
                    errors='ignore'
                )
                
                if result.returncode != 0:

                    DEBUG.log(f"UnrealLocres.exe failed during import for {path}: {result.stderr}", "WARNING")

            if os.path.exists(csv_path):
                os.remove(csv_path)
                
        except Exception as e:
            DEBUG.log(f"Error creating empty locres file at {path}: {e}", "ERROR")

    def load_subtitles_for_language(self, language):
        DEBUG.log(f"Loading subtitles for language: {language}")
        
        self.subtitles = {}
        self.original_subtitles = {}
        self.key_to_file_map = {}

        DEBUG.log("--- Loading original subtitles and building key map ---")
        for key, file_info in self.all_subtitle_files.items():
            if file_info['language'] == language:
                try:
                    original_data = self.locres_manager.export_locres(file_info['path'])
                    self.original_subtitles.update(original_data)

                    for sub_key in original_data:
                        self.key_to_file_map[sub_key] = file_info
                except Exception as e:
                    DEBUG.log(f"Failed to load original subtitles from {file_info['path']}: {e}", "ERROR")

        self.subtitles = self.original_subtitles.copy()
        DEBUG.log(f"Loaded {len(self.original_subtitles)} original subtitle entries and mapped them to files.")

        if self.mod_p_path and os.path.exists(self.mod_p_path):
            DEBUG.log(f"--- Loading modded subtitles from profile: {self.active_profile_name} ---")
            mod_loc_path = os.path.join(self.mod_p_path, "OPP", "Content", "Localization")
            
            if os.path.exists(mod_loc_path):
                for key, file_info in self.all_subtitle_files.items():
                    if file_info['language'] == language:
                        mod_file_path = os.path.join(mod_loc_path, file_info['category'], file_info['language'], file_info['filename'])
                        
                        if os.path.exists(mod_file_path):
                            DEBUG.log(f"Found modded subtitle file: {mod_file_path}")
                            try:
                                mod_data = self.locres_manager.export_locres(mod_file_path)
                                self.subtitles.update(mod_data)
                                DEBUG.log(f"Applied {len(mod_data)} subtitle entries from mod file.")
                            except Exception as e:
                                DEBUG.log(f"Failed to load mod subtitles from {mod_file_path}: {e}", "ERROR")
            else:
                DEBUG.log("No Localization folder in active mod profile.")
        else:
            DEBUG.log("No active mod profile to load modded subtitles from.")
    def create_resource_updater_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setSpacing(15)
        layout.setContentsMargins(10, 10, 10, 10)

        header_layout = QtWidgets.QVBoxLayout()
        header_layout.setSpacing(5)
        header_layout.addWidget(QtWidgets.QLabel(f"<h2>{self.tr('updater_header')}</h2>"))
        desc_label = QtWidgets.QLabel(self.tr("updater_description"))
        desc_label.setWordWrap(True)
        header_layout.addWidget(desc_label)
        layout.addLayout(header_layout)

        pak_group_layout = QtWidgets.QFormLayout()
        pak_group_layout.setSpacing(10)
        pak_group_layout.setContentsMargins(0, 10, 0, 0)
        
        self.pak_path_edit = QtWidgets.QLineEdit()
        self.pak_path_edit.setPlaceholderText(self.tr("pak_file_path_placeholder"))
        if self.settings.data.get("game_path"):
            potential_pak = os.path.join(self.settings.data.get("game_path"), "OPP", "Content", "Paks", "OPP-WindowsClient.pak")
            if os.path.exists(potential_pak):
                self.pak_path_edit.setText(potential_pak)
        
        pak_browse_btn = QtWidgets.QPushButton(self.tr("browse"))
        pak_browse_btn.clicked.connect(self.browse_for_pak)
        
        pak_widget = QtWidgets.QWidget()
        pak_widget_layout = QtWidgets.QHBoxLayout(pak_widget)
        pak_widget_layout.setContentsMargins(0,0,0,0)
        pak_widget_layout.addWidget(self.pak_path_edit)
        pak_widget_layout.addWidget(pak_browse_btn)

        pak_group_layout.addRow(f"<b>1. {self.tr('pak_file_path_label')}</b>", pak_widget)
        layout.addLayout(pak_group_layout)
        
        res_group_layout = QtWidgets.QFormLayout()
        res_group_layout.setSpacing(10)

        res_widget = QtWidgets.QWidget()
        res_layout = QtWidgets.QHBoxLayout(res_widget)
        res_layout.setContentsMargins(0,0,0,0)
        self.update_audio_check = QtWidgets.QCheckBox(self.tr("update_audio_check"))
        self.update_audio_check.setChecked(True)
        self.update_loc_check = QtWidgets.QCheckBox(self.tr("update_localization_check"))
        self.update_loc_check.setChecked(True)
        res_layout.addWidget(self.update_audio_check)
        res_layout.addWidget(self.update_loc_check)
        res_layout.addStretch()
        
        res_group_layout.addRow(f"<b>2. {self.tr('select_resources_group')}:</b>", res_widget)
        layout.addLayout(res_group_layout)
        
        button_layout = QtWidgets.QHBoxLayout()
        self.start_update_btn = QtWidgets.QPushButton(self.tr("start_update_btn"))
        self.start_update_btn.setMinimumHeight(20)
        self.start_update_btn.clicked.connect(self.start_update_process)
        
        self.cancel_update_btn = QtWidgets.QPushButton(self.tr("cancel"))
        self.cancel_update_btn.setMinimumHeight(20)
        self.cancel_update_btn.clicked.connect(self.cancel_update_process)
        self.cancel_update_btn.hide() 

        button_layout.addWidget(self.start_update_btn)
        button_layout.addWidget(self.cancel_update_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        self.update_progress_group = QtWidgets.QGroupBox(f"3. {self.tr('update_process_group')}")
        progress_layout = QtWidgets.QVBoxLayout(self.update_progress_group)

        self.update_progress_bar = QtWidgets.QProgressBar()
        self.update_status_label = QtWidgets.QLabel(self.tr("update_log_ready"))
        self.update_status_label.setStyleSheet("font-weight: bold;")
        self.update_fun_status_label = QtWidgets.QLabel("") 
        self.update_fun_status_label.setStyleSheet("color: #888; font-style: italic;")
        self.update_log_widget = QtWidgets.QTextEdit()
        self.update_log_widget.setReadOnly(True)
        self.update_log_widget.setFont(QtGui.QFont("Consolas", 9))
        self.update_log_widget.setMaximumHeight(250)

        progress_layout.addWidget(self.update_status_label)
        progress_layout.addWidget(self.update_fun_status_label)
        progress_layout.addWidget(self.update_progress_bar)
        progress_layout.addWidget(self.update_log_widget)
        
        layout.addWidget(self.update_progress_group)
        layout.addStretch()

        self.tabs.addTab(tab, self.tr("resource_updater_tab"))

    def browse_for_pak(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Game Pak file", self.settings.data.get("game_path", ""), "Pak files (*.pak)")
        if path:
            self.pak_path_edit.setText(path)


    def on_major_step_update(self, message, progress):
        self.update_status_label.setText(message)
        self.update_progress_bar.setValue(progress)

    def update_animation_text(self):

        if hasattr(self, 'animation_texts') and self.animation_texts:
            text = self.animation_texts[self.animation_index]
            self.update_fun_status_label.setText(f"-> {text}")
            self.animation_index = (self.animation_index + 1) % len(self.animation_texts)

    
    def start_update_process(self):
        pak_path = self.pak_path_edit.text()
        update_audio = self.update_audio_check.isChecked()
        update_loc = self.update_loc_check.isChecked()

        if not pak_path or not os.path.exists(pak_path):
            QtWidgets.QMessageBox.warning(self, self.tr("error"), self.tr("pak_file_not_selected"))
            return

        if not update_audio and not update_loc:
            QtWidgets.QMessageBox.warning(self, self.tr("error"), self.tr("no_resources_selected"))
            return

        folders_to_replace = []
        if update_audio: folders_to_replace.append("Wems")
        if update_loc: folders_to_replace.append("Localization")

        reply = QtWidgets.QMessageBox.question(self, self.tr("update_confirm_title"),
                                    self.tr("update_confirm_msg").format(resource_folder=", ".join(folders_to_replace)),
                                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if reply == QtWidgets.QMessageBox.No:
            return

        self.start_update_btn.hide()
        self.cancel_update_btn.show()
        self.pak_path_edit.setEnabled(False)
        self.update_audio_check.setEnabled(False)
        self.update_loc_check.setEnabled(False)
        
        self.update_log_widget.clear()
        self.update_status_label.setText(self.tr("update_process_started"))
        self.update_fun_status_label.show()
        self.update_progress_bar.setRange(0, 0)
        self.update_start_time = time.time()
        self.update_timer = QtCore.QTimer(self)
        self.update_timer.timeout.connect(self.update_elapsed_time)
        self.update_timer.start(1000) 
        self.update_elapsed_time()

        self.animation_timer = QtCore.QTimer(self)
        self.animation_texts = [
            self.tr("update_fun_status_1"), self.tr("update_fun_status_2"),
            self.tr("update_fun_status_3"), self.tr("update_fun_status_4"),
            self.tr("update_fun_status_5"), self.tr("update_fun_status_6"),
            self.tr("update_fun_status_7"),
        ]
        import random
        random.shuffle(self.animation_texts)
        self.animation_index = 0
        self.animation_timer.timeout.connect(self.update_animation_text)
        self.animation_timer.start(3000)
        self.update_animation_text()

        self.updater_thread = ResourceUpdaterThread(self, pak_path, update_audio, update_loc)
        self.updater_thread.major_step_update.connect(self.update_status_label.setText)
        self.updater_thread.log_update.connect(self.update_log_widget.append)
        self.updater_thread.finished.connect(self.on_update_finished)
        self.updater_thread.start()

    def cancel_update_process(self):
        if hasattr(self, 'updater_thread') and self.updater_thread.isRunning():
            self.updater_thread.cancel()

    def update_elapsed_time(self):
        if not hasattr(self, 'update_start_time'):
            return

        elapsed_seconds = int(time.time() - self.update_start_time)
        minutes = elapsed_seconds // 60
        seconds = elapsed_seconds % 60
        time_str = f"({minutes:02d}:{seconds:02d})"
        
      
        current_status = self.update_status_label.text().split(" (")[0]
        self.update_status_label.setText(f"{current_status} {time_str}")
    def on_update_finished(self, status, message):
        if hasattr(self, 'animation_timer'):
            self.animation_timer.stop()

        self.start_update_btn.show()
        self.cancel_update_btn.hide()
        self.pak_path_edit.setEnabled(True)
        self.update_audio_check.setEnabled(True)
        self.update_loc_check.setEnabled(True)
        
        self.update_fun_status_label.hide()
        
        self.update_progress_bar.setRange(0, 100)
        
        if status == "success":
            self.update_status_label.setText(self.tr('done'))
            self.update_progress_bar.setValue(100)
            
            audio_was_updated = self.update_audio_check.isChecked()
            if audio_was_updated:

                self.update_log_widget.append(f"\n--- {self.tr('update_rescanning_orphans')} ---")
                self.status_bar.showMessage(self.tr("update_rescanning_orphans"), 0)
                QtWidgets.QApplication.processEvents() 
                
                self.perform_blocking_orphan_scan()
            QtWidgets.QMessageBox.information(self, self.tr("update_complete_title"), f"{message}\n\n{self.tr('restart_recommended')}")

        elif status == "failure":
            self.update_status_label.setText(self.tr('error_status'))
            self.update_progress_bar.setValue(0)
            QtWidgets.QMessageBox.critical(self, self.tr("update_failed_title"), f"{self.tr('update_failed_msg')}\n\n{message}")
        
        elif status == "cancelled":
            self.update_status_label.setText(self.tr('update_cancelled_by_user'))
            self.update_progress_bar.setValue(0)
    def create_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.create_menu_bar()
        self.create_toolbar()

        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        self.update_status()

        content_widget = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_widget)

        self.global_search = SearchBar(placeholder_text=self.tr("search_placeholder"))
        self.global_search.searchChanged.connect(self.on_global_search)
        content_layout.addWidget(self.global_search)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tab_widgets = {}

        languages = list(self.entries_by_lang.keys())

        if "French(France)" not in languages and any("French" in lang for lang in languages):
            french_variants = [lang for lang in languages if "French" in lang]
            if french_variants:
                languages = languages
                
        if "SFX" not in languages:
            self.entries_by_lang["SFX"] = []
            languages.append("SFX")
            
        for lang in sorted(languages):
            self.create_language_tab(lang)

        self.create_converter_tab()
        self.load_converter_file_list()
        self.create_subtitle_editor_tab()
        self.create_resource_updater_tab()
        self.tabs.currentChanged.connect(self.on_tab_changed)
        
        content_layout.addWidget(self.tabs)
        main_layout.addWidget(content_widget)

        # if self.entries_by_lang:
        #     first_lang = sorted(self.entries_by_lang.keys())[0]
        #     self.populate_tree(first_lang)
        #     self.populated_tabs.add(first_lang)
            
        def delayed_init():
            if hasattr(self, 'subtitle_lang_combo'):
                self.populate_subtitle_editor_controls()
            
            for lang in self.tab_widgets.keys():
                self.update_filter_combo(lang)

        QtCore.QTimer.singleShot(500, delayed_init)

    def refresh_subtitle_editor(self):
        """Refresh subtitle editor data"""
        DEBUG.log("Refreshing subtitle editor")
        self.scan_localization_folder()
        self.populate_subtitle_editor_controls()
        self.status_bar.showMessage("Localization editor refreshed", 2000)

    def on_global_search_changed_for_subtitles(self, text):
        if hasattr(self, 'subtitle_editor_tab_widget') and self.tabs.currentWidget() == self.subtitle_editor_tab_widget:
            self.on_subtitle_filter_changed()

    def get_global_search_text(self):
        """Get text from global search bar"""
        return self.global_search.text() if hasattr(self, 'global_search') else ""

    def create_subtitle_editor_tab(self):
        """Create tab for editing subtitles without audio files"""
        tab = QtWidgets.QWidget()
        self.subtitle_editor_tab_widget = tab
        layout = QtWidgets.QVBoxLayout(tab)
        
        header = QtWidgets.QLabel(f"""
        <h3>{self.tr("localization_editor")}</h3>
        <p>{self.tr("localization_editor_desc")}</p>
        """)
        layout.addWidget(header)
        
        status_widget = QtWidgets.QWidget()
        status_layout = QtWidgets.QHBoxLayout(status_widget)
        
        self.subtitle_status_label = QtWidgets.QLabel("Ready")
        self.subtitle_status_label.setStyleSheet("color: #666; font-style: italic;")
        
        self.subtitle_progress = QtWidgets.QProgressBar()
        self.subtitle_progress.setVisible(False)
        self.subtitle_progress.setMaximumHeight(20)
        
        self.subtitle_cancel_btn = QtWidgets.QPushButton(self.tr("cancel"))
        self.subtitle_cancel_btn.setVisible(False)
        self.subtitle_cancel_btn.setMaximumWidth(80)
        self.subtitle_cancel_btn.clicked.connect(self.cancel_subtitle_loading)
        
        status_layout.addWidget(self.subtitle_status_label)
        status_layout.addStretch()
        status_layout.addWidget(self.subtitle_progress)
        status_layout.addWidget(self.subtitle_cancel_btn)
        
        layout.addWidget(status_widget)
        
        controls = QtWidgets.QWidget()
        controls_layout = QtWidgets.QHBoxLayout(controls)
        
        category_label = QtWidgets.QLabel("Category:")
        self.subtitle_category_combo = QtWidgets.QComboBox()
        self.subtitle_category_combo.setMinimumWidth(150)
        
        self.orphaned_only_checkbox = QtWidgets.QCheckBox(self.tr("without_audio_filter"))
        self.orphaned_only_checkbox.setToolTip(self.tr("without_audio_filter_tooltip"))
        
        self.modified_only_checkbox = QtWidgets.QCheckBox(self.tr("modified_only_filter"))
        self.modified_only_checkbox.setToolTip(self.tr("modified_only_filter_tooltip"))
        
        self.with_audio_only_checkbox = QtWidgets.QCheckBox(self.tr("with_audio_only_filter"))
        self.with_audio_only_checkbox.setToolTip(self.tr("with_audio_only_filter_tooltip"))
        
        refresh_btn = QtWidgets.QPushButton(self.tr("refresh_btn"))
        refresh_btn.setToolTip(self.tr("refresh_btn_tooltip"))
        refresh_btn.clicked.connect(self.refresh_subtitle_editor)
        
        controls_layout.addWidget(category_label)
        controls_layout.addWidget(self.subtitle_category_combo)
        controls_layout.addWidget(self.orphaned_only_checkbox)
        controls_layout.addWidget(self.modified_only_checkbox)
        controls_layout.addWidget(self.with_audio_only_checkbox)
        controls_layout.addStretch()
        controls_layout.addWidget(refresh_btn)
        
        layout.addWidget(controls)
        
        self.subtitle_category_combo.currentTextChanged.connect(self.on_subtitle_filter_changed)
        self.orphaned_only_checkbox.toggled.connect(self.on_subtitle_filter_changed)
        self.modified_only_checkbox.toggled.connect(self.on_subtitle_filter_changed)
        self.with_audio_only_checkbox.toggled.connect(self.on_subtitle_filter_changed)
        
        self.subtitle_table = QtWidgets.QTableWidget()
        self.subtitle_table.setColumnCount(4)
        self.subtitle_table.setHorizontalHeaderLabels([self.tr("key_header"), self.tr("original_header"), self.tr("current_header"), self.tr("audio_header")])
        
        header = self.subtitle_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        
        self.subtitle_table.setAlternatingRowColors(True)
        self.subtitle_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.subtitle_table.itemDoubleClicked.connect(self.edit_subtitle_from_table)
        
        self.subtitle_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.subtitle_table.customContextMenuRequested.connect(self.show_subtitle_table_context_menu)
        
        layout.addWidget(self.subtitle_table)
        
        btn_widget = QtWidgets.QWidget()
        btn_layout = QtWidgets.QHBoxLayout(btn_widget)
        
        edit_btn = QtWidgets.QPushButton(self.tr("edit_selected_btn"))
        edit_btn.clicked.connect(self.edit_selected_subtitle)
        
        btn_layout.addWidget(edit_btn)
        btn_layout.addStretch()
        
        save_all_btn = QtWidgets.QPushButton(self.tr("save_all_changes_btn"))
        save_all_btn.clicked.connect(self.save_all_subtitle_changes)
        btn_layout.addWidget(save_all_btn)
        
        layout.addWidget(btn_widget)
        
        self.subtitle_editor_loaded = False
        self.audio_keys_cache = None
        self.subtitle_loader_thread = None
        
        self.tabs.addTab(tab, self.tr("localization_editor"))
        self.global_search.searchChanged.connect(self.on_global_search_changed_for_subtitles)

    def cancel_subtitle_loading(self):
        """Cancel current subtitle loading operation"""
        if self.subtitle_loader_thread and self.subtitle_loader_thread.isRunning():
            self.subtitle_loader_thread.stop()
            self.subtitle_loader_thread.wait(2000)
        
        self.hide_subtitle_loading_ui()
        self.subtitle_status_label.setText("Loading cancelled")

    def show_subtitle_loading_ui(self):
        """Show loading UI elements"""
        self.subtitle_progress.setVisible(True)
        self.subtitle_cancel_btn.setVisible(True)
        
        self.subtitle_category_combo.setEnabled(False)
        self.orphaned_only_checkbox.setEnabled(False)

    def hide_subtitle_loading_ui(self):
        """Hide loading UI elements"""
        self.subtitle_progress.setVisible(False)
        self.subtitle_cancel_btn.setVisible(False)
        
        self.subtitle_category_combo.setEnabled(True)
        self.orphaned_only_checkbox.setEnabled(True)

    def populate_subtitle_editor_controls(self):
        """Populate category controls"""
        DEBUG.log("Populating subtitle editor controls")
        
        self.subtitle_category_combo.currentTextChanged.disconnect()
        
        try:
            categories = set()
            
            for file_info in self.all_subtitle_files.values():
                categories.add(file_info['category'])
            
            DEBUG.log(f"Found categories: {categories}")
            
            current_category = self.subtitle_category_combo.currentText()
            
            self.subtitle_category_combo.clear()
            self.subtitle_category_combo.addItem("All Categories")
            if categories:
                sorted_categories = sorted(categories)
                self.subtitle_category_combo.addItems(sorted_categories)
                
                if current_category and current_category != "All Categories":
                    if current_category in categories:
                        self.subtitle_category_combo.setCurrentText(current_category)
            
            DEBUG.log(f"Category combo: {self.subtitle_category_combo.count()} items")
            
        finally:
            self.subtitle_category_combo.currentTextChanged.connect(self.on_subtitle_filter_changed)
        
        self.load_subtitle_editor_data()

    
    def on_subtitle_filter_changed(self):
        """Handle filter changes with debouncing"""
        if hasattr(self, 'filter_timer'):
            self.filter_timer.stop()
        
        self.filter_timer = QtCore.QTimer()
        self.filter_timer.setSingleShot(True)
        self.filter_timer.timeout.connect(self.load_subtitle_editor_data)
        self.filter_timer.start(500)

    def build_audio_keys_cache(self):
        """Build cache of audio keys for orphaned subtitle detection"""
        if self.audio_keys_cache is not None:
            return self.audio_keys_cache
        
        DEBUG.log("Building audio keys cache...")
        self.audio_keys_cache = set()
        
        for entry in self.all_files:
            shortname = entry.get("ShortName", "")
            if shortname:
                audio_key = os.path.splitext(shortname)[0]
                self.audio_keys_cache.add(audio_key)
        
        DEBUG.log(f"Built cache with {len(self.audio_keys_cache)} audio keys")
    
        sample_keys = list(self.audio_keys_cache)[:5]
        DEBUG.log(f"Sample audio keys: {sample_keys}")
        
        return self.audio_keys_cache

    def load_subtitle_editor_data(self):
        """Load subtitle data for editor asynchronously"""
        selected_category = self.subtitle_category_combo.currentText()
        orphaned_only = self.orphaned_only_checkbox.isChecked()
        modified_only = self.modified_only_checkbox.isChecked()
        with_audio_only = self.with_audio_only_checkbox.isChecked()
        search_text = self.get_global_search_text()
        
        DEBUG.log(f"Loading subtitle editor data: category={selected_category}, language={self.settings.data['subtitle_lang']}, orphaned={orphaned_only}, modified={modified_only}, with_audio={with_audio_only}")
        
 
        if orphaned_only and with_audio_only:
            self.with_audio_only_checkbox.setChecked(False)
            with_audio_only = False
            DEBUG.log("Disabled 'with_audio_only' because 'orphaned_only' is active")
        
        if self.subtitle_loader_thread and self.subtitle_loader_thread.isRunning():
            self.subtitle_loader_thread.stop()
            self.subtitle_loader_thread.wait(1000)

        if (orphaned_only or with_audio_only):
            if self.audio_keys_cache is None:
                self.build_audio_keys_cache()
            DEBUG.log(f"Audio cache has {len(self.audio_keys_cache) if self.audio_keys_cache else 0} keys")
        
        self.show_subtitle_loading_ui()
        self.subtitle_status_label.setText("Loading subtitles...")
        self.subtitle_progress.setValue(0)
        
        self.subtitle_table.setRowCount(0)

        self.subtitle_loader_thread = SubtitleLoaderThread(
            self, self.all_subtitle_files, self.locres_manager, 
            self.subtitles, self.original_subtitles,
            self.settings.data["subtitle_lang"], selected_category, orphaned_only, modified_only, with_audio_only,
            search_text, self.audio_keys_cache, self.modified_subtitles
        )
        
        self.subtitle_loader_thread.dataLoaded.connect(self.on_subtitle_data_loaded)
        self.subtitle_loader_thread.statusUpdate.connect(self.subtitle_status_label.setText)
        self.subtitle_loader_thread.progressUpdate.connect(self.subtitle_progress.setValue)
        
        self.subtitle_loader_thread.start()
    def on_subtitle_data_loaded(self, subtitles_to_show):
        """Handle loaded subtitle data"""
        self.hide_subtitle_loading_ui()
        
        self.populate_subtitle_table(subtitles_to_show)
        
        status_parts = [f"{len(subtitles_to_show)} subtitles"]
        
        filters_active = []
        if self.orphaned_only_checkbox.isChecked():
            filters_active.append("without audio")
        
        if self.modified_only_checkbox.isChecked():
            filters_active.append("modified only")
            
        if self.with_audio_only_checkbox.isChecked():
            filters_active.append("with audio only")
        
        search_text = self.get_global_search_text().strip()
        if search_text:
            filters_active.append(f"search: '{search_text}'")
        
        selected_category = self.subtitle_category_combo.currentText()
        if selected_category and selected_category != "All Categories":
            filters_active.append(f"category: {selected_category}")
        
        if filters_active:
            status_parts.append(f"({', '.join(filters_active)})")
        
        self.subtitle_status_label.setText(" ".join(status_parts))

    def populate_subtitle_table(self, subtitles_to_show):
        """Populate the subtitle table with data"""
        self.subtitle_table.setRowCount(len(subtitles_to_show))
        
        if len(subtitles_to_show) == 0:
            return
        
        sorted_items = sorted(subtitles_to_show.items())
        
        for row, (key, data) in enumerate(sorted_items):
            key_item = QtWidgets.QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.subtitle_table.setItem(row, 0, key_item)
            
            original_text = data['original']
            original_display = self.truncate_text(original_text, 150)
            original_item = QtWidgets.QTableWidgetItem(original_display)
            original_item.setFlags(original_item.flags() & ~QtCore.Qt.ItemIsEditable)
            original_item.setToolTip(original_text)
            self.subtitle_table.setItem(row, 1, original_item)
            
            current_text = data['current']
            current_display = self.truncate_text(current_text, 150)
            current_item = QtWidgets.QTableWidgetItem(current_display)
            current_item.setToolTip(current_text)
            self.subtitle_table.setItem(row, 2, current_item)
            
            has_audio = data.get('has_audio', False)
            audio_item = QtWidgets.QTableWidgetItem("🔊" if has_audio else "")
            audio_item.setFlags(audio_item.flags() & ~QtCore.Qt.ItemIsEditable)
            audio_item.setToolTip(self.tr("has_audio_file") if has_audio else self.tr("no_audio_file"))
            audio_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.subtitle_table.setItem(row, 3, audio_item)
            
            is_modified = data.get('is_modified', False)
            if is_modified:
                highlight_color = QtGui.QColor(85, 72, 35) if self.settings.data.get("theme", "light") == "dark" else QtGui.QColor(255, 255, 200)
                for col in range(4):
                    item = self.subtitle_table.item(row, col)
                    if item:
                        item.setBackground(highlight_color)
            
            search_text = self.get_global_search_text().lower().strip()
            if search_text:
                if (search_text in key.lower() or 
                    search_text in original_text.lower() or 
                    search_text in current_text.lower()):
                    for col in range(4):
                        item = self.subtitle_table.item(row, col)
                        if item:
                            font = item.font()
                            font.setBold(True)
                            item.setFont(font)

    def truncate_text(self, text, max_length):
        """Truncate text for display"""
        if len(text) <= max_length:
            return text
        return text[:max_length-3] + "..."

    def edit_subtitle_from_table(self, item):
        """Edit subtitle from table double-click"""
        if not item:
            return
            
        try:
            row = item.row()
            key = self.subtitle_table.item(row, 0).text()
            current_text = self.subtitle_table.item(row, 2).toolTip() or self.subtitle_table.item(row, 2).text()
            original_text = self.subtitle_table.item(row, 1).toolTip() or self.subtitle_table.item(row, 1).text()
            
            stored_key = key
            stored_row = row
            
            editor = SubtitleEditor(self, key, current_text, original_text)
            if editor.exec_() == QtWidgets.QDialog.Accepted:
                new_text = editor.get_text()
                self.subtitles[key] = new_text
                if key in self.key_to_file_map:
                    file_info = self.key_to_file_map[key]
                    self.dirty_subtitle_files.add(file_info['path'])
                    DEBUG.log(f"Marked file as dirty due to edit: {file_info['path']}")
                if new_text != original_text:
                    self.modified_subtitles.add(key)
                else:
                    self.modified_subtitles.discard(key)
                
                target_row = self.find_table_row_by_key(stored_key)
                if target_row >= 0:
                    try:
                        current_item = self.subtitle_table.item(target_row, 2)
                        if current_item:
                            display_text = self.truncate_text(new_text, 150)
                            current_item.setText(display_text)
                            current_item.setToolTip(new_text)
                            
                            if new_text != original_text:
                 
                                highlight_color = QtGui.QColor(85, 72, 35) if self.settings.data.get("theme", "light") == "dark" else QtGui.QColor(255, 255, 200)
                                for col in range(4):
                                    cell_item = self.subtitle_table.item(target_row, col)
                                    if cell_item:
                                        cell_item.setBackground(highlight_color)
                    
                            else:
      
                                base_color = self.palette().color(QtGui.QPalette.Base)
                                for col in range(4):
                                    cell_item = self.subtitle_table.item(target_row, col)
                                    if cell_item:
                                        cell_item.setBackground(base_color)
                                        
                    except RuntimeError as e:
                        DEBUG.log(f"Table item was deleted during update: {e}", "WARNING")
                        self.load_subtitle_editor_data()
                else:
                    DEBUG.log("Table row not found after edit, refreshing")
                    self.load_subtitle_editor_data()
                
                self.update_status()
                
        except RuntimeError as e:
            DEBUG.log(f"Error in edit_subtitle_from_table: {e}", "ERROR")
            self.load_subtitle_editor_data()

    def find_table_row_by_key(self, target_key):
        """Find table row by subtitle key"""
        for row in range(self.subtitle_table.rowCount()):
            try:
                key_item = self.subtitle_table.item(row, 0)
                if key_item and key_item.text() == target_key:
                    return row
            except RuntimeError:
                continue
        return -1

    def edit_selected_subtitle(self):
        """Edit currently selected subtitle"""
        current_row = self.subtitle_table.currentRow()
        if current_row >= 0:
            item = self.subtitle_table.item(current_row, 0)
            if item:
                self.edit_subtitle_from_table(item)

    def save_all_subtitle_changes(self):
        """Save all subtitle changes to working files in a separate thread."""
        if not self.ensure_active_profile():
            return
            
        if not self.modified_subtitles:
            QtWidgets.QMessageBox.information(self, self.tr("no_changes"), self.tr("no_modified_subtitles"))
            return

        self.progress_dialog = ProgressDialog(self, self.tr("Saving Subtitles..."))
        self.progress_dialog.show()

        self.save_thread = SaveSubtitlesThread(self)
        self.save_thread.progress_updated.connect(self.progress_dialog.set_progress)
        self.save_thread.finished.connect(self.on_save_finished)
        self.save_thread.start()

    def on_save_finished(self, count, errors):
        """Handles the completion of the subtitle saving thread."""
        self.progress_dialog.close()
        
        self.update_status()
        for lang in self.populated_tabs:
            self.populate_tree(lang)
        
        if not errors:
            self.dirty_subtitle_files.clear()
            QtWidgets.QMessageBox.information(self, self.tr("success"), 
                f"{self.tr('subtitle_save_success')}\n\nUpdated {count} file(s) in your mod profile.")
            self.status_bar.showMessage(self.tr("subtitle_save_success"), 3000)
        else:
            error_details = "\n".join(errors)
            msg_box = QtWidgets.QMessageBox()
            msg_box.setIcon(QtWidgets.QMessageBox.Warning)
            msg_box.setWindowTitle(self.tr("save_error"))
            msg_box.setText(f"Completed with {len(errors)} error(s).")
            msg_box.setDetailedText(error_details)
            msg_box.exec_()
            self.status_bar.showMessage(f"Save completed with {len(errors)} error(s)", 5000)

    def show_subtitle_table_context_menu(self, pos):
        selected_items = self.subtitle_table.selectedItems()
        if not selected_items:
            return
        
        selected_rows = sorted(list(set(item.row() for item in selected_items)))
        
        first_row = selected_rows[0]
        key = self.subtitle_table.item(first_row, 0).text()
        has_audio = self.subtitle_table.item(first_row, 3).text() == "🔊"

        menu = QtWidgets.QMenu()
        if self.settings.data["theme"] == "dark":
            menu.setStyleSheet(self.get_dark_menu_style())
        
        if len(selected_rows) > 1:
            edit_action = menu.addAction(f"✏️ {self.tr('edit_subtitle')} ({len(selected_rows)} items)")
            edit_action.setEnabled(False) 
            
            revert_action = menu.addAction(f"↩️ {self.tr('revert_to_original')} ({len(selected_rows)} items)")
        else:
            edit_action = menu.addAction(f"✏️ {self.tr('edit_subtitle')}")
            revert_action = menu.addAction(f"↩️ {self.tr('revert_to_original')}")

        edit_action.triggered.connect(lambda: self.edit_subtitle_from_table(self.subtitle_table.item(first_row, 0)))
        revert_action.triggered.connect(lambda: self.revert_subtitle_from_table(selected_rows))
        
        menu.addSeparator()
        
        if len(selected_rows) == 1 and has_audio:
            goto_audio_action = menu.addAction(f"🔊 {self.tr('go_to_audio_action')}")
            goto_audio_action.triggered.connect(lambda: self.go_to_audio_file(key))
            menu.addSeparator()
        
        copy_key_action = menu.addAction(f"{self.tr('copy_key')}")
        copy_key_action.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(key))
        
        copy_text_action = menu.addAction(f"{self.tr('copy_text')}")
        current_text = self.subtitle_table.item(first_row, 2).toolTip() or self.subtitle_table.item(first_row, 2).text()
        copy_text_action.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(current_text))
        
        menu.exec_(self.subtitle_table.mapToGlobal(pos))

    def go_to_audio_file(self, subtitle_key):
        """Navigate to audio file corresponding to subtitle"""
        DEBUG.log(f"Looking for audio file for subtitle key: {subtitle_key}")
        
        target_entry = None
        target_lang = None
        
        for entry in self.all_files:
            shortname = entry.get("ShortName", "")
            if shortname:
                audio_key = os.path.splitext(shortname)[0]
                if audio_key == subtitle_key:
                    target_entry = entry
                    target_lang = entry.get("Language", "SFX")
                    break
        
        if not target_entry:
            QtWidgets.QMessageBox.information(
                self, self.tr("info"), 
                self.tr("tab_not_found_for_lang").format(lang=target_lang)
            )
            return
        
        DEBUG.log(f"Found audio file: {target_entry.get('ShortName')} in language: {target_lang}")
        
        for i in range(self.tabs.count()):
            tab_text = self.tabs.tabText(i)
            if target_lang in tab_text:
                self.tabs.setCurrentIndex(i)
                
                if target_lang not in self.populated_tabs:
                    self.populate_tree(target_lang)
                    self.populated_tabs.add(target_lang)
                
                self.find_and_select_audio_item(target_lang, target_entry)
                
                self.status_bar.showMessage(f"Navigated to audio file: {target_entry.get('ShortName')}", 3000)
                return
        
        QtWidgets.QMessageBox.information(
            self, self.tr("audio_not_found"), 
            self.tr("audio_not_found_for_key").format(key=subtitle_key)
        )

    def find_and_select_audio_item(self, lang, target_entry):
        """Find and select audio item in tree"""
        if lang not in self.tab_widgets:
            return
        
        tree = self.tab_widgets[lang]["tree"]
        target_id = target_entry.get("Id", "")
        target_shortname = target_entry.get("ShortName", "")
        
        def search_items(parent_item):
            for i in range(parent_item.childCount()):
                item = parent_item.child(i)
                
                if item.childCount() == 0:
                    try:
                        entry = item.data(0, QtCore.Qt.UserRole)
                        if entry:
                            if (entry.get("Id") == target_id or 
                                entry.get("ShortName") == target_shortname):
                                tree.clearSelection()
                                tree.setCurrentItem(item)
                                item.setSelected(True)
                                
                                parent = item.parent()
                                if parent:
                                    parent.setExpanded(True)
                                
                                tree.scrollToItem(item)
                                self.on_selection_changed(lang)
                                
                                return True
                    except RuntimeError:
                        continue
                else:
                    if search_items(item):
                        return True
            return False
        
        try:
            root = tree.invisibleRootItem()
            if not search_items(root):
                DEBUG.log(f"Could not find item in tree for: {target_shortname}")
        except RuntimeError:
            pass

    def revert_subtitle_from_table(self, rows_to_revert):
        """Revert subtitle(s) to original from table for a list of row indices."""
        if not rows_to_revert:
            return

        reverted_count = 0
        for row in rows_to_revert:
            try:
                key_item = self.subtitle_table.item(row, 0)
                if not key_item:
                    continue
                
                key = key_item.text()
                
                if key in self.original_subtitles:
                    original_text = self.original_subtitles[key]
                    
                    self.subtitles[key] = original_text
                    self.modified_subtitles.discard(key)
                    if key in self.key_to_file_map:
                        file_info = self.key_to_file_map[key]
                        self.dirty_subtitle_files.add(file_info['path'])
                        DEBUG.log(f"Marked file as dirty due to revert: {file_info['path']}")

                    current_item = self.subtitle_table.item(row, 2)
                    current_item.setText(self.truncate_text(original_text, 150))
                    current_item.setToolTip(original_text)
                    
                    base_color = self.palette().color(QtGui.QPalette.Base)
                    for col in range(4):
                        item = self.subtitle_table.item(row, col)
                        if item:
                            item.setBackground(base_color)

                    reverted_count += 1
            except Exception as e:
                DEBUG.log(f"Error reverting subtitle at row {row}: {e}", "ERROR")

        if reverted_count > 0:
            self.update_status()
            self.status_bar.showMessage(f"Reverted {reverted_count} subtitle(s) to original", 3000)

 
    def process_wem_files(self):
        wwise_root = self.wwise_path_edit_old.text()
        if not wwise_root or not os.path.exists(wwise_root):
            QtWidgets.QMessageBox.warning(self, "Error", "Invalid WWISE folder path!")
            return
            
        progress = ProgressDialog(self, "Processing WEM Files")
        progress.show()
        
        # Find SFX paths
        sfx_paths = []
        for root, dirs, files in os.walk(wwise_root):
            if root.endswith(".cache\\Windows\\SFX"):
                sfx_paths.append(root)
                
        if not sfx_paths:
            progress.close()
            QtWidgets.QMessageBox.warning(self, "Error", "No .cache/Windows/SFX/ folders found!")
            return
        

        selected_language = self.settings.data.get("wem_process_language", "english")
        DEBUG.log(f"Selected WEM process language: {selected_language}")
        

        if selected_language == "english":
            target_dir_voice = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", "English(US)")
            voice_lang_filter = ["English(US)"]
        elif selected_language == "french":
            target_dir_voice = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", "Francais")
            voice_lang_filter = ["French(France)", "Francais"]
        else:
            target_dir_voice = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "English(US)")
            voice_lang_filter = ["English(US)"]
        
        target_dir_sfx = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media")
        
        os.makedirs(target_dir_voice, exist_ok=True)
        os.makedirs(target_dir_sfx, exist_ok=True)
        
        all_wem_files = []
        vo_wem_files = []
        
        for sfx_path in sfx_paths:
            for filename in os.listdir(sfx_path):
                if filename.endswith(".wem"):
                    base_name = os.path.splitext(filename)[0]
                    all_wem_files.append(base_name)
                    if base_name.startswith("VO_"):
                        vo_wem_files.append(base_name)
        
        DEBUG.log(f"Found {len(all_wem_files)} total WEM files on disk")
        DEBUG.log(f"Found {len(vo_wem_files)} VO WEM files on disk")
        DEBUG.log(f"First 10 VO WEM files on disk: {vo_wem_files[:10]}")
        
        voice_mapping = {}  
        sfx_mapping = {}    
        voice_files_in_db = []

        vo_from_streamed = 0
        vo_from_media_files = 0
        vo_skipped_wrong_lang = 0
        
        for entry in self.all_files:
            shortname = entry.get("ShortName", "")
            base_shortname = os.path.splitext(shortname)[0]
            file_id = entry.get("Id", "")
            language = entry.get("Language", "")
            source = entry.get("Source", "")
            
            file_info = {
                'id': file_id,
                'language': language,
                'source': source,
                'original_name': shortname
            }

            if base_shortname.startswith("VO_"):

                if language in voice_lang_filter:
                    voice_mapping[base_shortname] = file_info
                    voice_files_in_db.append(base_shortname)
                    
                    if source == "StreamedFiles":
                        vo_from_streamed += 1
                        DEBUG.log(f"Added StreamedFiles VO: {base_shortname} -> ID {file_id} ({language})")
                    elif source == "MediaFilesNotInAnyBank":
                        vo_from_media_files += 1
                        if vo_from_media_files <= 10:  
                            DEBUG.log(f"Added MediaFilesNotInAnyBank VO: {base_shortname} -> ID {file_id} ({language})")
                else:
          
                    vo_skipped_wrong_lang += 1
                    if vo_skipped_wrong_lang <= 5: 
                        DEBUG.log(f"Skipped VO (wrong language): {base_shortname} -> ID {file_id} ({language})")
            
            elif language == "SFX" or (source == "MediaFilesNotInAnyBank" and not base_shortname.startswith("VO_")):
                sfx_mapping[base_shortname] = file_info
        
        DEBUG.log(f"Voice files from StreamedFiles: {vo_from_streamed}")
        DEBUG.log(f"Voice files from MediaFilesNotInAnyBank: {vo_from_media_files}")
        DEBUG.log(f"Voice files skipped (wrong language): {vo_skipped_wrong_lang}")
        DEBUG.log(f"Total voice files for {selected_language}: {len(voice_files_in_db)}")
        DEBUG.log(f"First 10 voice files in database: {voice_files_in_db[:10]}")

        exact_matches = []
        potential_matches = []
        
        for wem_file in vo_wem_files:
            if wem_file in voice_mapping:
                exact_matches.append(wem_file)
            else:
   
                wem_without_hex = wem_file

                if '_' in wem_file:
                    parts = wem_file.split('_')
   
                    if len(parts) > 1 and len(parts[-1]) == 8:
                        try:
                            int(parts[-1], 16) 
                            wem_without_hex = '_'.join(parts[:-1])
                            DEBUG.log(f"Removing hex suffix: {wem_file} -> {wem_without_hex}")
                        except ValueError:
                            pass
                
                if wem_without_hex in voice_mapping and wem_without_hex != wem_file:
                    potential_matches.append((wem_file, wem_without_hex))
        
        DEBUG.log(f"Exact matches found: {len(exact_matches)}")
        DEBUG.log(f"Potential matches (after removing hex): {len(potential_matches)}")
        DEBUG.log(f"First 5 exact matches: {exact_matches[:5]}")
        DEBUG.log(f"First 5 potential matches: {potential_matches[:5]}")

        for wem_file, db_file in potential_matches:
            if db_file in voice_mapping:
                voice_mapping[wem_file] = voice_mapping[db_file].copy()
                voice_mapping[wem_file]['matched_via'] = f"hex_removal_from_{db_file}"
                DEBUG.log(f"Added potential match: {wem_file} -> {voice_mapping[wem_file]['id']} (via {db_file}) [{voice_mapping[wem_file]['language']}]")
        
        DEBUG.log(f"Voice mapping after adding potential matches: {len(voice_mapping)} files")

        name_to_ids = {}
        for name, info in voice_mapping.items():
            base_name = name.split('_')
            if len(base_name) > 3:
                check_name = '_'.join(base_name[:4]) 
                if check_name not in name_to_ids:
                    name_to_ids[check_name] = []
                name_to_ids[check_name].append((info['id'], info['language']))
        
        for name, ids in name_to_ids.items():
            if len(ids) > 1:
                DEBUG.log(f"WARNING: Multiple IDs for similar name '{name}': {ids}")
        
        self.converter_status_old.clear()
        self.converter_status_old.append(f"=== Processing WEM Files for {selected_language.capitalize()} ===")
        self.converter_status_old.append(f"Voice target: {target_dir_voice}")
        self.converter_status_old.append(f"SFX target: {target_dir_sfx}")
        self.converter_status_old.append("")
        self.converter_status_old.append(f"Analysis Results:")
        self.converter_status_old.append(f"  WEM files on disk: {len(all_wem_files)} total, {len(vo_wem_files)} VO files")
        self.converter_status_old.append(f"  Voice files in database for {selected_language}: {len(voice_files_in_db)}")
        self.converter_status_old.append(f"    - From StreamedFiles: {vo_from_streamed}")
        self.converter_status_old.append(f"    - From MediaFilesNotInAnyBank: {vo_from_media_files}")
        self.converter_status_old.append(f"    - Skipped (wrong language): {vo_skipped_wrong_lang}")
        self.converter_status_old.append(f"  Exact matches: {len(exact_matches)}")
        self.converter_status_old.append(f"  Potential matches (hex removal): {len(potential_matches)}")
        self.converter_status_old.append(f"  Total mappable files: {len(exact_matches) + len(potential_matches)}")
        self.converter_status_old.append("")
        
        processed = 0
        voice_processed = 0
        sfx_processed = 0
        skipped = 0
        renamed_count = 0
        total_files = len(all_wem_files)
        
        for sfx_path in sfx_paths:
            DEBUG.log(f"Processing folder: {sfx_path}")
            
            for filename in os.listdir(sfx_path):
                if filename.endswith(".wem"):
                    src_path = os.path.join(sfx_path, filename)
                    base_name = os.path.splitext(filename)[0]
                    
                    file_info = None
                    dest_filename = filename
                    target_dir = target_dir_sfx
                    is_voice = base_name.startswith("VO_")
                    classification = "Unknown"
                    
                    if is_voice:
                        target_dir = target_dir_voice
                        classification = f"Voice ({selected_language})"

                        if base_name in voice_mapping:
                            file_info = voice_mapping[base_name]
                            dest_filename = f"{file_info['id']}.wem"
                            match_method = file_info.get('matched_via', 'exact_match')
                            file_language = file_info.get('language', 'Unknown')
                            classification += f" (ID {file_info['id']}, {match_method}, {file_language})"
                            renamed_count += 1
                            DEBUG.log(f"FOUND MATCH: {filename} -> {dest_filename} ({match_method}) [Language: {file_language}]")
                        else:
                            classification += " (no ID found - keeping original name)"
                            DEBUG.log(f"NO MATCH FOUND for {filename}")
                            
                    else:

                        classification = "SFX"
                        search_keys = [
                            base_name,
                            base_name.rsplit("_", 1)[0] if "_" in base_name else base_name,
                        ]
                        
                        for search_key in search_keys:
                            if search_key in sfx_mapping:
                                file_info = sfx_mapping[search_key]
                                dest_filename = f"{file_info['id']}.wem"
                                classification += f" (matched '{search_key}' -> ID {file_info['id']})"
                                renamed_count += 1
                                break
                        
                        if not file_info:
                            classification += " (no ID found - keeping original name)"
                    
                    dest_path = os.path.join(target_dir, dest_filename)
                    
                    try:

                        if os.path.exists(dest_path):
                            base_dest_name = os.path.splitext(dest_filename)[0]
                            counter = 1
                            while os.path.exists(os.path.join(target_dir, f"{base_dest_name}_{counter}.wem")):
                                counter += 1
                            dest_filename = f"{base_dest_name}_{counter}.wem"
                            dest_path = os.path.join(target_dir, dest_filename)
                            classification += " (duplicate renamed)"
                        
                        shutil.move(src_path, dest_path)
                        processed += 1
                        
                        if is_voice:
                            voice_processed += 1
                            icon = "🎙"
                        else:
                            sfx_processed += 1
                            icon = "🔊"
                        
                        progress.set_progress(int((processed / total_files) * 100), f"Processing {filename}...")
                        
                        self.converter_status.append(f"{icon} {classification}: {filename} → {dest_filename}")
                        QtWidgets.QApplication.processEvents()
                        
                    except Exception as e:
                        self.converter_status.append(f"✗ ERROR: {filename} - {str(e)} [{classification}]")
                        skipped += 1
                        DEBUG.log(f"Error processing {filename}: {e}", "ERROR")
                        
        progress.close()
        
        success_rate = (renamed_count / voice_processed * 100) if voice_processed > 0 else 0
        
        self.converter_status_old.append("")
        self.converter_status_old.append("=== Processing Complete ===")
        self.converter_status_old.append(f"Total files processed: {processed}")
        self.converter_status_old.append(f"Voice files ({selected_language}): {voice_processed}")
        self.converter_status_old.append(f"SFX files: {sfx_processed}")
        self.converter_status_old.append(f"Files renamed to ID: {renamed_count}")
        self.converter_status_old.append(f"Files kept original name: {processed - renamed_count}")
        self.converter_status_old.append(f"Voice rename success rate: {success_rate:.1f}%")
        if skipped > 0:
            self.converter_status.append(f"Skipped/Errors: {skipped}")
        
        QtWidgets.QMessageBox.information(
            self, "Processing Complete",
            f"Processed {processed} files for {selected_language.capitalize()} language.\n"
            f"Voice files: {voice_processed}\n"
            f"Renamed to ID: {renamed_count}\n"
            f"Success rate: {success_rate:.1f}%\n"
            f"Kept original names: {processed - renamed_count}"
        )
    def cleanup_working_locres(self):
        DEBUG.log("=== Cleanup Working Locres Files ===")
        localization_path = os.path.join(self.base_path, "Localization")
        if not os.path.exists(localization_path):
            QtWidgets.QMessageBox.information(
                self, self.tr("no_localization_found"), 
                self.tr("no_localization_message").format(path=localization_path)
            )
            return

        working_files = []
        for root, dirs, files in os.walk(localization_path):
            for file in files:
                if file.endswith('_working.locres'):
                    file_path = os.path.join(root, file)
                    working_files.append(file_path)

        if not working_files:
            QtWidgets.QMessageBox.information(
                self, self.tr("no_localization_found"), 
                "No working subtitle files (_working.locres) found in Localization."
            )
            return

        deleted = 0
        errors = 0
        for file_path in working_files:
            try:
                os.remove(file_path)
                DEBUG.log(f"Deleted: {file_path}")
                deleted += 1

                parent = os.path.dirname(file_path)
                while parent != localization_path and os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
            except Exception as e:
                DEBUG.log(f"Error deleting {file_path}: {e}", "ERROR")
                errors += 1

        msg = f"Deleted {deleted} working subtitle files."
        if errors:
            msg += f"\nErrors: {errors}"
        QtWidgets.QMessageBox.information(self, "Cleanup Complete", msg)
    def save_subtitles_to_file(self):

        if not self.dirty_subtitle_files:
            return True

        DEBUG.log(f"=== Performing Blocking Save for {len(self.dirty_subtitle_files)} files ===")
        try:
            for original_path in list(self.dirty_subtitle_files):
                file_info = None
                for info in self.all_subtitle_files.values():
                    if info['path'] == original_path:
                        file_info = info
                        break
                
                if not file_info:
                    DEBUG.log(f"Could not find file info for dirty path: {original_path}", "WARNING")
                    continue
                
                target_dir = os.path.join(self.mod_p_path, "OPP", "Content", "Localization", file_info['category'], file_info['language'])
                os.makedirs(target_dir, exist_ok=True)
                target_path = os.path.join(target_dir, file_info['filename'])

                subtitles_to_write = self.locres_manager.export_locres(original_path)
                
                for key in subtitles_to_write.keys():
                    if key in self.subtitles:
                        subtitles_to_write[key] = self.subtitles[key]

                shutil.copy2(original_path, target_path)

                if not self.locres_manager.import_locres(target_path, subtitles_to_write):
                    raise Exception(f"Failed to write to {target_path}")

            self.dirty_subtitle_files.clear()
            DEBUG.log("Blocking save successful, dirty files cleared.")
            return True
        except Exception as e:
            DEBUG.log(f"Blocking save error: {e}", "ERROR")
            return False
    def show_settings_dialog(self):
        dialog = QtWidgets.QDialog(self)    
        dialog.setWindowTitle(self.tr("settings"))
        dialog.setMinimumWidth(500)
        
        layout = QtWidgets.QFormLayout(dialog)
        
        lang_combo = QtWidgets.QComboBox()
        lang_map = [("English", "en"), ("Русский", "ru"), ("Polski", "pl"), ("Español (México)", "es-MX")]
        for name, code in lang_map:
            lang_combo.addItem(name, code)
        
        current_lang_code = self.settings.data["ui_language"]
        index = next((i for i, (name, code) in enumerate(lang_map) if code == current_lang_code), 0)
        lang_combo.setCurrentIndex(index)
        
        theme_combo = QtWidgets.QComboBox()
        theme_combo.addItem(self.tr("light"), "light")
        theme_combo.addItem(self.tr("dark"), "dark")
        theme_combo.setCurrentIndex(0 if self.settings.data["theme"] == "light" else 1)
        
        subtitle_combo = QtWidgets.QComboBox()
        subtitle_langs = [
            "de-DE", "en", "es-ES", "es-MX", "fr-FR", "it-IT", "ja-JP", "ko-KR",
            "pl-PL", "pt-BR", "ru-RU", "tr-TR", "zh-CN", "zh-TW"
        ]
        subtitle_combo.addItems(subtitle_langs)
        subtitle_combo.setCurrentText(self.settings.data["subtitle_lang"])
        
        game_path_widget = QtWidgets.QWidget()
        game_path_layout = QtWidgets.QHBoxLayout(game_path_widget)
        game_path_layout.setContentsMargins(0, 0, 0, 0)
        
        game_path_edit = QtWidgets.QLineEdit()
        game_path_edit.setText(self.settings.data.get("game_path", ""))
        game_path_edit.setPlaceholderText("Path to game root folder")
        
        game_path_btn = QtWidgets.QPushButton(self.tr("browse"))
        game_path_btn.clicked.connect(lambda: self.browse_game_path(game_path_edit))
        
        game_path_layout.addWidget(game_path_edit)
        game_path_layout.addWidget(game_path_btn)

        auto_save_check = QtWidgets.QCheckBox(self.tr("auto_save"))
        auto_save_check.setChecked(self.settings.data.get("auto_save", True))

        layout.addRow(self.tr("interface_language"), lang_combo)
        layout.addRow(self.tr("theme"), theme_combo)
        layout.addRow(self.tr("subtitle_language"), subtitle_combo)
        layout.addRow(self.tr("game_path"), game_path_widget)
        
        quick_load_group = QtWidgets.QGroupBox(self.tr("quick_load_settings_group"))
        quick_load_layout = QtWidgets.QVBoxLayout(quick_load_group)
        
        quick_load_label = QtWidgets.QLabel(self.tr("quick_load_mode_label"))
        quick_load_layout.addWidget(quick_load_label)
        
        quick_load_strict = QtWidgets.QRadioButton(self.tr("quick_load_strict"))
        quick_load_adaptive = QtWidgets.QRadioButton(self.tr("quick_load_adaptive"))
        
        current_quick_mode = self.settings.data.get("quick_load_mode", "strict")
        if current_quick_mode == "adaptive":
            quick_load_adaptive.setChecked(True)
        else:
            quick_load_strict.setChecked(True)
        
        quick_load_layout.addWidget(quick_load_strict)
        quick_load_layout.addWidget(quick_load_adaptive)
        
        layout.addRow(quick_load_group)
        layout.addRow(auto_save_check)
        wem_lang_combo = QtWidgets.QComboBox()
        wem_lang_combo.addItem("English (US)", "english")
        wem_lang_combo.addItem("Francais (France)", "french")
        current_wem_lang = self.settings.data.get("wem_process_language", "english")
        wem_lang_combo.setCurrentIndex(0 if current_wem_lang == "english" else 1)
        wem_lang_combo.setToolTip(self.tr("wemprocces_desc"))

        layout.addRow(self.tr("wem_process_language"), wem_lang_combo)
        conversion_method_group = QtWidgets.QGroupBox(self.tr("conversion_method_group"))
        conversion_method_layout = QtWidgets.QVBoxLayout(conversion_method_group)
        
        self.bnk_overwrite_radio = QtWidgets.QRadioButton(self.tr("bnk_overwrite_radio"))
        self.bnk_overwrite_radio.setToolTip(self.tr("bnk_overwrite_tooltip"))
        self.adaptive_radio = QtWidgets.QRadioButton(self.tr("adaptive_size_matching_radio"))
        self.adaptive_radio.setToolTip(self.tr("adaptive_size_matching_tooltip"))
        
        current_method = self.settings.data.get("conversion_method", "adaptive")
        if current_method == "bnk":
            self.bnk_overwrite_radio.setChecked(True)
        else:
            self.adaptive_radio.setChecked(True)
            
        conversion_method_layout.addWidget(self.adaptive_radio)
        conversion_method_layout.addWidget(self.bnk_overwrite_radio)
        
        layout.addRow(conversion_method_group)
        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        layout.addRow(btn_box)
        
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            old_subtitle_lang = self.settings.data["subtitle_lang"]
            old_ui_lang = self.settings.data["ui_language"]
            
            new_ui_lang = lang_combo.currentData()
            new_subtitle_lang = subtitle_combo.currentText()

            self.settings.data["ui_language"] = new_ui_lang
            self.settings.data["theme"] = theme_combo.currentData()
            self.settings.data["subtitle_lang"] = new_subtitle_lang
            self.settings.data["game_path"] = game_path_edit.text()
            self.settings.data["auto_save"] = auto_save_check.isChecked()
            self.settings.data["wem_process_language"] = wem_lang_combo.currentData() 
            if self.bnk_overwrite_radio.isChecked():
                self.settings.data["conversion_method"] = "bnk"
            else:
                self.settings.data["conversion_method"] = "adaptive"
            
            if quick_load_adaptive.isChecked():
                self.settings.data["quick_load_mode"] = "adaptive"
            else:
                self.settings.data["quick_load_mode"] = "strict"
            
            self.settings.save()

            self.apply_settings()

            if new_ui_lang != old_ui_lang:
                self.current_lang = new_ui_lang
                
                msg_box = QtWidgets.QMessageBox(self)
                msg_box.setWindowTitle(self.tr("settings_saved_title"))
                msg_box.setText(self.tr("close_required_message"))
                msg_box.setIcon(QtWidgets.QMessageBox.Information)
                
                close_btn = msg_box.addButton(self.tr("close_now_button"), QtWidgets.QMessageBox.AcceptRole)
                later_btn = msg_box.addButton(self.tr("cancel"), QtWidgets.QMessageBox.RejectRole)
                
                msg_box.exec_()

                if msg_box.clickedButton() == close_btn:
                    self.close()
                else:
                    self.current_lang = old_ui_lang

            if new_subtitle_lang != old_subtitle_lang:
                DEBUG.log(f"Subtitle language changed from {old_subtitle_lang} to {new_subtitle_lang}")
                self.load_subtitles()
                self.modified_subtitles.clear()
                for key, value in self.subtitles.items():
                    if key in self.original_subtitles and self.original_subtitles[key] != value:
                        self.modified_subtitles.add(key)
                    elif key not in self.original_subtitles:
                        self.modified_subtitles.add(key)
                DEBUG.log(f"Recalculated modified subtitles for {new_subtitle_lang}: {len(self.modified_subtitles)} found.")
                for lang in list(self.populated_tabs):
                    self.populate_tree(lang)
                self.update_status()

                if hasattr(self, 'subtitle_table'):
                    self.load_subtitle_editor_data()
           
    def browse_game_path(self, edit_widget):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, self.tr("select_game_path"), 
            edit_widget.text() or ""
        )
        
        if folder:
            edit_widget.setText(folder)

    def update_ui_language(self):
        self.setWindowTitle(self.tr("app_title"))
        
        # update menus
        self.menuBar().clear()
        self.create_menu_bar()
        
        # update tabs
        for i, (lang, widgets) in enumerate(self.tab_widgets.items()):
            if i < self.tabs.count() - 1:
                # update filter combo
                current_filter = widgets["filter_combo"].currentIndex()
                widgets["filter_combo"].clear()
                widgets["filter_combo"].addItems([
                    self.tr("all_files"), 
                    self.tr("with_subtitles"), 
                    self.tr("without_subtitles"), 
                    self.tr("modified"),
                    self.tr("modded")
                ])
                widgets["filter_combo"].setCurrentIndex(current_filter)
                
                tab_widget = self.tabs.widget(i)
                if tab_widget:
                    self.update_group_boxes_recursively(tab_widget)

    def update_group_boxes_recursively(self, widget):

        if isinstance(widget, QtWidgets.QGroupBox):
            title = widget.title()

            if "subtitle" in title.lower() or "preview" in title.lower():
                widget.setTitle(self.tr("subtitle_preview"))
            elif "file" in title.lower() or "info" in title.lower():
                widget.setTitle(self.tr("file_info"))

        for child in widget.findChildren(QtWidgets.QWidget):
            if isinstance(child, QtWidgets.QGroupBox):
                title = child.title()

                if "subtitle" in title.lower() or "preview" in title.lower():
                    child.setTitle(self.tr("subtitle_preview"))
                elif "file" in title.lower() or "info" in title.lower():
                    child.setTitle(self.tr("file_info"))

    def update_status(self):
        total_files = len(self.all_files)
        total_subtitles = len(self.subtitles)
        modified = len(self.modified_subtitles)
        
        status_text = f"Files: {total_files} | Subtitles: {total_subtitles}"
        if modified > 0:
            status_text += f" | Modified: {modified}"
            
        self.status_bar.showMessage(status_text)
    def load_all_soundbank_files(self, path=None):
        DEBUG.log(f"Loading soundbank files from: {path}")
        all_files = []
        
        if not path or not os.path.exists(path):
            DEBUG.log("SoundbanksInfo file not found.", "WARNING")
            return []

        try:
            ext = os.path.splitext(path)[1].lower()
            
            if ext == '.json':
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                soundbanks_info = data.get("SoundBanksInfo") or data.get("SoundbanksInfo") or data
                
                if not soundbanks_info:
                    DEBUG.log("ERROR: Could not find SoundBanksInfo block.", "ERROR")
                    return []
                
                streamed_files = soundbanks_info.get("StreamedFiles", [])
                for file_entry in streamed_files:
                    file_entry["Source"] = "StreamedFiles"
                    if "Path" in file_entry:
                        file_entry["Path"] = file_entry["Path"].replace("Media/", "").replace("Media\\", "")
                all_files.extend(streamed_files)
                
                media_not_in_bank = soundbanks_info.get("MediaFilesNotInAnyBank", [])
                for file_entry in media_not_in_bank:
                    file_entry["Source"] = "MediaFilesNotInAnyBank"
                    if "Path" in file_entry:
                        file_entry["Path"] = file_entry["Path"].replace("Media/", "").replace("Media\\", "")
                all_files.extend(media_not_in_bank)

                soundbanks_list = soundbanks_info.get("SoundBanks", [])
                
                unique_files_map = {f["Id"]: f for f in all_files}
                
                for sb in soundbanks_list:
                 
                    bnk_name = sb.get("ShortName", "UnknownBank")
                    
                    media_list = sb.get("Media", [])
                    for media_entry in media_list:
                        file_id = media_entry.get("Id")
                        
                        if file_id and file_id not in unique_files_map:
                      
                            if "Path" in media_entry:
                                media_entry["Path"] = media_entry["Path"].replace("Media/", "").replace("Media\\", "")
                            
                            media_entry["Source"] = f"Bank: {bnk_name}"
                            
                            unique_files_map[file_id] = media_entry
                            all_files.append(media_entry)
                
                DEBUG.log(f"Loaded {len(streamed_files)} StreamedFiles, {len(media_not_in_bank)} LooseMedia, and {len(all_files) - len(streamed_files) - len(media_not_in_bank)} files from SoundBanks Media.")

            elif ext == '.xml':
          
                tree = ET.parse(path)
                root = tree.getroot()
                
                streamed_files_elem = root.find("StreamedFiles")
                if streamed_files_elem is not None:
                    for file_elem in streamed_files_elem.findall("File"):
                        raw_path = file_elem.find("Path").text if file_elem.find("Path") is not None else ""
                        clean_path = raw_path.replace("Media/", "").replace("Media\\", "")
                        
                        file_entry = { 
                            "Id": file_elem.get("Id"), 
                            "Language": file_elem.get("Language"), 
                            "ShortName": file_elem.find("ShortName").text if file_elem.find("ShortName") is not None else "", 
                            "Path": clean_path, 
                            "Source": "StreamedFiles" 
                        }
                        all_files.append(file_entry)
                
                media_files_elem = root.find("MediaFilesNotInAnyBank")
                if media_files_elem is not None:
                    for file_elem in media_files_elem.findall("File"):
                        raw_path = file_elem.find("Path").text if file_elem.find("Path") is not None else ""
                        clean_path = raw_path.replace("Media/", "").replace("Media\\", "")

                        file_entry = { 
                            "Id": file_elem.get("Id"), 
                            "Language": file_elem.get("Language"), 
                            "ShortName": file_elem.find("ShortName").text if file_elem.find("ShortName") is not None else "", 
                            "Path": clean_path, 
                            "Source": "MediaFilesNotInAnyBank" 
                        }
                        all_files.append(file_entry)
                        
                soundbanks_elem = root.find("SoundBanks")
                if soundbanks_elem is not None:
                    for sb_elem in soundbanks_elem.findall("SoundBank"):
                        bnk_name = sb_elem.find("ShortName").text if sb_elem.find("ShortName") is not None else "Unknown"
                        media_elem = sb_elem.find("Media")
                        if media_elem is not None:
                            for file_elem in media_elem.findall("File"):
                                file_id = file_elem.get("Id")
                       
                                raw_path = file_elem.find("Path").text if file_elem.find("Path") is not None else ""
                                clean_path = raw_path.replace("Media/", "").replace("Media\\", "")
                                
                                file_entry = {
                                    "Id": file_id,
                                    "Language": file_elem.get("Language"),
                                    "ShortName": file_elem.find("ShortName").text if file_elem.find("ShortName") is not None else "",
                                    "Path": clean_path,
                                    "Source": f"Bank: {bnk_name}"
                                }
                                all_files.append(file_entry)

            else:
                raise ValueError(f"Unsupported file format: {ext}")
            
            unique_files = {}
            for f in all_files:
                fid = f.get("Id")
                if fid and fid not in unique_files:
                    unique_files[fid] = f
            
            final_list = list(unique_files.values())
            DEBUG.log(f"Total unique files loaded from SoundbanksInfo: {len(final_list)}")
            return final_list
            
        except Exception as e:
            DEBUG.log(f"Error loading soundbank: {e}", "ERROR")
            import traceback
            DEBUG.log(traceback.format_exc(), "ERROR")
            return []
    def _scan_and_add_orphaned_wems(self, known_ids):
        """Scans the Wems directory to find and add files not listed in SoundbanksInfo."""
        orphaned_entries = []
        if not os.path.exists(self.wem_root):
            DEBUG.log(f"Wems directory not found at {self.wem_root}, skipping scan.", "WARNING")
            return orphaned_entries

        for root, _, files in os.walk(self.wem_root):
            for file in files:
                if not file.lower().endswith('.wem'):
                    continue

                file_id = os.path.splitext(file)[0]
                if file_id in known_ids:
                    continue

                full_path = os.path.join(root, file)
                
                rel_path = os.path.relpath(root, self.wem_root)
                lang = "SFX" if rel_path == '.' else rel_path

                short_name = f"{file_id}.wav"
                try:
                    analyzer = WEMAnalyzer(full_path)
                    if analyzer.analyze():
                        markers = analyzer.get_markers_info()
                        if markers and markers[0]['label']:
                            short_name = f"{markers[0]['label']}.wav"
                            DEBUG.log(f"Orphaned file '{file}' named from marker: '{short_name}'")
                except Exception as e:
                    DEBUG.log(f"Could not analyze markers for orphaned file {file}: {e}", "WARNING")

                new_entry = {
                    "Id": file_id,
                    "Language": lang,
                    "ShortName": short_name,
                    "Path": file, 
                    "Source": "ScannedFromFileSystem"
                }
                orphaned_entries.append(new_entry)

        if orphaned_entries:
            DEBUG.log(f"Added {len(orphaned_entries)} orphaned WEM files found on disk.")
        else:
            DEBUG.log("No orphaned WEM files found on disk.")
            
        return orphaned_entries
    def group_by_language(self):
        entries_by_lang = {}
        for entry in self.all_files:
            lang = entry.get("Language", "SFX") 
            entries_by_lang.setdefault(lang, []).append(entry)
            
        DEBUG.log(f"Files grouped by language: {list(entries_by_lang.keys())}")
        for lang, entries in entries_by_lang.items():
            DEBUG.log(f"  {lang}: {len(entries)} files")
            
        return entries_by_lang

    def get_current_language(self):
   
        current_index = self.tabs.currentIndex()
        if current_index >= 0 and current_index < len(self.tab_widgets):
            languages = list(self.tab_widgets.keys())
            if current_index < len(languages):
                return languages[current_index]
        return None
    def _tree_populate_generator(self, tree, filtered_wrappers, lang, is_flat_view, selected_keys):

        
        root_groups = {}
        id_only_category = "Numeric ID Files"
        id_only_item = None
        
        
        for i, wrapper in enumerate(filtered_wrappers):
            entry = wrapper['_orig']
            has_mod = wrapper['has_mod_audio']
            
            if is_flat_view:
        
                parent_item = tree.invisibleRootItem()
                item = self.add_tree_item(parent_item, entry, lang, has_mod)
            else:
             
                shortname = entry.get("ShortName", "")
                name_without_ext = shortname.rsplit('.', 1)[0]
                
                if name_without_ext.isdigit():
                    if id_only_item is None:
                        id_only_item = QtWidgets.QTreeWidgetItem(tree, [f"{id_only_category}"])
                    
                    self.add_tree_item(id_only_item, entry, lang, has_mod)
                else:
                    parts = name_without_ext.split("_")[:3]
                    
                    if not parts:
                        self.add_tree_item(tree.invisibleRootItem(), entry, lang, has_mod)
                        continue

                    current_parent_dict = root_groups
                    current_parent_item = tree.invisibleRootItem()

                    for level_idx, part in enumerate(parts):
                        if part not in current_parent_dict:
                            display_name = "VO (Voice)" if level_idx == 0 and part.upper() == "VO" else part
                            new_item = QtWidgets.QTreeWidgetItem(current_parent_item, [display_name])
                            
                            if level_idx == 0 and part.upper() == "VO":
                                new_item.setExpanded(True)
                            
                            current_parent_dict[part] = {"__item__": new_item, "__children__": {}}
                        
                        current_parent_item = current_parent_dict[part]["__item__"]
                        current_parent_dict = current_parent_dict[part]["__children__"]

                    self.add_tree_item(current_parent_item, entry, lang, has_mod)

            if selected_keys:
                key = os.path.splitext(entry.get("ShortName", ""))[0]
                if key in selected_keys:

                    pass 

            if i % 50 == 0:
                yield

        if not is_flat_view:
            self._update_group_counts_recursive(tree.invisibleRootItem(), id_only_category)
            if id_only_item:
                id_only_item.setText(0, f"{id_only_category} ({id_only_item.childCount()})")

        if selected_keys:
            self.restore_tree_selection(tree, selected_keys)
        
        yield

    def _process_tree_batch(self):
      
        if not self.tree_loader_generator or not self.current_loading_lang:
            self.tree_loader_timer.stop()
            return

        widgets = self.tab_widgets.get(self.current_loading_lang)
        if not widgets:
            self.tree_loader_timer.stop()
            return
            
        tree = widgets["tree"]
        
        tree.setUpdatesEnabled(False)
        
        start_time = time.time()
        try:
          
            while (time.time() - start_time) < 0.015:
                next(self.tree_loader_generator)
                
        except StopIteration:
            
            self.tree_loader_timer.stop()
            self.tree_loader_generator = None
            tree.setUpdatesEnabled(True)
            # DEBUG.log("Tree population complete")
        except Exception as e:
            DEBUG.log(f"Error in tree population: {e}", "ERROR")
            self.tree_loader_timer.stop()
            self.tree_loader_generator = None
            tree.setUpdatesEnabled(True)
        finally:
        
            tree.setUpdatesEnabled(True)

    def _update_group_counts_recursive(self, item, id_category_name):
       
        count = 0
        for i in range(item.childCount()):
            child = item.child(i)
           
            if child.text(0).startswith(id_category_name):
                continue
                
            if child.childCount() > 0:
                count += self._update_group_counts_recursive(child, id_category_name)
            else:
                count += 1
        
        if item.parent() is not None and item.childCount() > 0:
            current_text = item.text(0)
            if "(" not in current_text:
                item.setText(0, f"{current_text} ({count})")
        
        return count
    @QtCore.pyqtSlot(str)
    def populate_tree(self, lang):
        DEBUG.log(f"Populating tree for language: {lang}")
        
        if lang not in self.tab_widgets:
            return
            
        widgets = self.tab_widgets[lang]
        tree = widgets["tree"]
        
        if self.tree_loader_timer.isActive():
            self.tree_loader_timer.stop()
            self.tree_loader_generator = None
   
            if self.current_loading_lang and self.current_loading_lang in self.tab_widgets:
                self.tab_widgets[self.current_loading_lang]["tree"].setUpdatesEnabled(True)

        selected_keys = []
        try:
            for item in tree.selectedItems():
                if item.childCount() == 0:
                    entry = item.data(0, QtCore.Qt.UserRole)
                    if entry:
                        shortname = entry.get("ShortName", "")
                        key = os.path.splitext(shortname)[0]
                        selected_keys.append(key)
        except RuntimeError:
            pass
        
        tree.clear()
        
        filter_text = widgets["filter_combo"].currentText()
        filter_type = widgets["filter_combo"].currentIndex()
        sort_type = widgets["sort_combo"].currentIndex() 
        search_text = self.global_search.text().lower()
        
        filtered_entries = []
        source_entries = self.entries_by_lang.get(lang, [])
        
        search_terms = []
        if search_text:
           
            search_terms = [term.strip() for term in search_text.split() if term.strip()]
        
        if filter_text.startswith("With Tag: "):
            selected_tag = filter_text.split(": ", 1)[1]
            for entry in source_entries:
                key = os.path.splitext(entry.get("ShortName", ""))[0]
                
                if self.marked_items.get(key, {}).get('tag') != selected_tag:
                    continue
                    
                if search_terms:
                    
                    content_to_search = f"{entry.get('Id', '')} {entry.get('ShortName', '')} {self.subtitles.get(key, '')}".lower()
                    
                    if not all(term in content_to_search for term in search_terms):
                        continue
                        
                filtered_entries.append({'_orig': entry, 'has_mod_audio': False})
        else:
            for entry in source_entries:
                key = os.path.splitext(entry.get("ShortName", ""))[0]
                subtitle = self.subtitles.get(key, "")
                
                has_mod_audio = False
                if filter_type == 4: 
                    mod_path = self.get_mod_path(entry.get("Id", ""), lang)
                    has_mod_audio = os.path.exists(mod_path) if mod_path else False
                
                if filter_type == 1 and not subtitle: continue          # With Subtitles
                elif filter_type == 2 and subtitle: continue            # Without Subtitles
                elif filter_type == 3 and key not in self.modified_subtitles: continue # Modified
                elif filter_type == 4 and not has_mod_audio: continue   # Modded (Audio)
                
                if search_terms:
                    content_to_search = f"{entry.get('Id', '')} {entry.get('ShortName', '')} {subtitle}".lower()
                    
                    match = True
                    for term in search_terms:
                        if term not in content_to_search:
                            match = False
                            break
                    if not match:
                        continue
                
                if filter_type != 4:
                     mod_path = self.get_mod_path(entry.get("Id", ""), lang)
                     has_mod_audio = os.path.exists(mod_path) if mod_path else False

                entry_wrapper = {'_orig': entry, 'has_mod_audio': has_mod_audio}
                filtered_entries.append(entry_wrapper)

        if sort_type == 4: # Recent First
            mod_times_cache = {}
            for wrapper in filtered_entries:
                entry = wrapper['_orig']
                file_id = entry.get("Id", "")
                mod_wem_path = self.get_mod_path(file_id, lang)
                if os.path.exists(mod_wem_path):
                    try: mod_times_cache[file_id] = os.path.getmtime(mod_wem_path)
                    except OSError: mod_times_cache[file_id] = 0
                else: mod_times_cache[file_id] = 0
            
            filtered_entries.sort(key=lambda x: mod_times_cache.get(x['_orig'].get("Id", ""), 0), reverse=True)
        elif sort_type == 0: filtered_entries.sort(key=lambda x: x['_orig'].get("ShortName", "").lower())
        elif sort_type == 1: filtered_entries.sort(key=lambda x: x['_orig'].get("ShortName", "").lower(), reverse=True)
        elif sort_type == 2: filtered_entries.sort(key=lambda x: int(x['_orig'].get("Id", "0")))
        elif sort_type == 3: filtered_entries.sort(key=lambda x: int(x['_orig'].get("Id", "0")), reverse=True)

        subtitle_count = sum(1 for w in filtered_entries if self.subtitles.get(os.path.splitext(w['_orig'].get("ShortName", ""))[0], ""))
        total_lang_entries = len(source_entries)
        stats_text = self.tr("stats_label_text").format(
            filtered_count=len(filtered_entries),
            total_count=total_lang_entries,
            subtitle_count=subtitle_count
        )
        widgets["stats_label"].setText(stats_text)

        self.current_loading_lang = lang
        is_flat_view = bool(search_text or sort_type == 4)
        
        self.tree_loader_generator = self._tree_populate_generator(
            tree, filtered_entries, lang, is_flat_view, selected_keys
        )
        
        self.tree_loader_timer.start()
    def add_tree_item(self, parent_item, entry, lang, has_mod_audio):
        """Adds a single entry as an item to the tree."""
        shortname = entry.get("ShortName", "")
        key = os.path.splitext(shortname)[0]
        subtitle = self.subtitles.get(key, "")
        
        mod_status = ""
        if has_mod_audio:
            mod_status = "♪"
        
        item = QtWidgets.QTreeWidgetItem(parent_item, [
            shortname,
            entry.get("Id", ""),
            subtitle,
            "✓" + mod_status if key in self.modified_subtitles else mod_status,
            ""  
        ])

        marking = self.marked_items.get(key, {})
        if 'color' in marking and marking['color'] is not None:
            for col in range(5):
                item.setBackground(col, marking['color'])
        
        if 'tag' in marking:
            item.setText(4, marking['tag'])
        
        item.setData(0, QtCore.Qt.UserRole, entry)
        
        if not subtitle:
            item.setForeground(2, QtGui.QBrush(QtGui.QColor(128, 128, 128)))
            
        if entry.get("Source") == "MediaFilesNotInAnyBank":
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(100, 100, 200)))
            
        return item 
    def restore_tree_selection(self, tree, target_keys):
        """Restore tree selection after refresh"""
        def search_and_select(parent_item):
            for i in range(parent_item.childCount()):
                try:
                    item = parent_item.child(i)
                    if item.childCount() == 0:
                        entry = item.data(0, QtCore.Qt.UserRole)
                        if entry:
                            shortname = entry.get("ShortName", "")
                            key = os.path.splitext(shortname)[0]
                            if key in target_keys:
                                item.setSelected(True)
                                tree.setCurrentItem(item)
                                return True
                    else:
                        if search_and_select(item):
                            return True
                except RuntimeError:
                    continue
            return False
        
        try:
            root = tree.invisibleRootItem()
            search_and_select(root)
        except RuntimeError:
            pass

    def on_selection_changed(self, lang):
        """Updated selection handler without summary"""
        if not self.mod_p_path:
            return

        widgets = self.tab_widgets[lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        file_items = [item for item in items if item.childCount() == 0 and item.data(0, QtCore.Qt.UserRole)]
        if hasattr(self, 'volume_adjust_action'):
            if len(file_items) == 0:
                self.volume_adjust_action.setToolTip(self.tr("volume_adjust_tooltip_no_selection"))
                self.volume_adjust_action.setEnabled(False)
            elif len(file_items) == 1:
                entry = file_items[0].data(0, QtCore.Qt.UserRole)
                filename = entry.get('ShortName', 'file') if entry else 'file'
                self.volume_adjust_action.setToolTip(self.tr("volume_adjust_tooltip_single").format(filename=filename))
                self.volume_adjust_action.setEnabled(True)
            else:
                self.volume_adjust_action.setToolTip(self.tr("volume_adjust_tooltip_batch").format(count=len(file_items)))
                self.volume_adjust_action.setEnabled(True)
        if not items:
            widgets["play_mod_btn"].hide()
            return
            
        item = items[0]
        if item.childCount() > 0:
            widgets["play_mod_btn"].hide()
            return
            
        entry = item.data(0, QtCore.Qt.UserRole)
        if not entry:
            widgets["play_mod_btn"].hide()
            return

        shortname = entry.get("ShortName", "")
        key = os.path.splitext(shortname)[0]
        subtitle = self.subtitles.get(key, "")
        original_subtitle = self.original_subtitles.get(key, "")
        marking = self.marked_items.get(key, {})
        tag = marking.get('tag', 'None')
        widgets["info_labels"]["tag"].setText(tag)
        widgets["subtitle_text"].setPlainText(subtitle)

        if original_subtitle and original_subtitle != subtitle:
            widgets["original_subtitle_label"].setText(f"{self.tr('original')}: {original_subtitle}")
            widgets["original_subtitle_label"].show()
        else:
            widgets["original_subtitle_label"].hide()
        
        widgets["info_labels"]["id"].setText(entry.get("Id", ""))
        widgets["info_labels"]["name"].setText(shortname)
        widgets["info_labels"]["path"].setText(entry.get("Path", ""))
        widgets["info_labels"]["source"].setText(entry.get("Source", ""))
        
        file_id = entry.get("Id", "")
        mod_wem_path = self.get_mod_path(file_id, lang)
        
        has_mod = os.path.exists(mod_wem_path) if mod_wem_path else False
        widgets["play_mod_btn"].setVisible(has_mod)
        
        self.load_audio_comparison_info(file_id, lang, widgets)
    def load_audio_comparison_info(self, file_id, lang, widgets):
        self.current_bnk_request_id += 1
        request_id = self.current_bnk_request_id

        original_wem_path = self.get_original_path(file_id, lang)
        mod_wem_path = self.get_mod_path(file_id, lang)
        
        date_format = "%Y-%m-%d %H:%M:%S"

        original_info = self.get_wem_audio_info_with_markers(original_wem_path) if os.path.exists(original_wem_path) else None
        if original_info:
            original_info['file_size'] = os.path.getsize(original_wem_path)

        modified_info = self.get_wem_audio_info_with_markers(mod_wem_path) if os.path.exists(mod_wem_path) else None

        if modified_info:
            modified_info['file_size'] = os.path.getsize(mod_wem_path)
            try:
                mtime = os.path.getmtime(mod_wem_path)
                modified_info['modified_date'] = datetime.fromtimestamp(mtime).strftime(date_format)
            except OSError:
                modified_info['modified_date'] = "N/A"
        
        if original_info:
            formatted_original = self.format_audio_info(original_info)
            for key, label in widgets["original_info_labels"].items():
                if key in formatted_original: label.setText(formatted_original[key])
            size_kb = original_info['file_size'] / 1024
            widgets["original_info_labels"]["size"].setText(f"{size_kb/1024:.1f} KB" if size_kb >= 1024 else f"{size_kb:.1f} KB")
            widgets["original_info_labels"]["modified_date"].setText(original_info.get('modified_date', 'N/A'))
            widgets["original_markers_list"].clear()
            original_markers = self.format_markers_for_display(original_info.get('markers', []))
            widgets["original_markers_list"].addItems(original_markers or ["No markers found"])
        else:
            for label_key in ["duration", "size", "sample_rate", "bitrate", "channels", "modified_date"]: 
                widgets["original_info_labels"][label_key].setText("N/A")
            widgets["original_markers_list"].clear()
            widgets["original_markers_list"].addItem("File not available")

        if modified_info:
            formatted_modified = self.format_audio_info(modified_info)
            for key, label in widgets["modified_info_labels"].items():
                if key in formatted_modified: label.setText(formatted_modified[key])
            size_kb = modified_info['file_size'] / 1024
            size_text = f"{size_kb/1024:.1f} MB" if size_kb >= 1024 else f"{size_kb:.1f} KB"
            widgets["modified_info_labels"]["size"].setStyleSheet("")
            widgets["modified_info_labels"]["size"].setText(size_text)
            widgets["modified_info_labels"]["modified_date"].setText(modified_info.get('modified_date', 'N/A'))
            widgets["modified_markers_list"].clear()
            modified_markers = self.format_markers_for_display(modified_info.get('markers', []))
            widgets["modified_markers_list"].addItems(modified_markers or ["No markers found"])
        else:
            for label_key in ["duration", "size", "sample_rate", "bitrate", "channels", "modified_date"]:
                widgets["modified_info_labels"][label_key].setText("N/A")
                widgets["modified_info_labels"][label_key].setStyleSheet("")
            widgets["modified_markers_list"].clear()
            widgets["modified_markers_list"].addItem("No modified audio")

        for label in ["bnk_size", "override_fx"]:
            widgets["original_info_labels"][label].setText("<i>Loading...</i>")
            widgets["modified_info_labels"][label].setText("<i>Loading...</i>")
        
        if self.bnk_loader_thread and self.bnk_loader_thread.isRunning():
            self.bnk_loader_thread.terminate()
            self.bnk_loader_thread.wait()

        try:
            source_id = int(file_id)
        except (ValueError, TypeError):
            DEBUG.log(f"Invalid file_id for BNK search: {file_id}", "ERROR")
            for label in ["bnk_size", "override_fx"]:
                widgets["original_info_labels"][label].setText("<span style='color:red;'>Error</span>")
                widgets["modified_info_labels"][label].setText("<span style='color:red;'>Error</span>")
            return
            
        bnk_files_info = self.find_relevant_bnk_files() 
        
        self.bnk_loader_thread = BnkInfoLoader(self, source_id, bnk_files_info, self.mod_p_path, os.path.join(self.base_path, "Wems"))
        
        real_original_wem_size = original_info['file_size'] if original_info else 0
        real_modified_wem_size = modified_info['file_size'] if modified_info else 0

        self.bnk_loader_thread.info_loaded.connect(
            lambda sid, orig_info, mod_info: self.update_bnk_info_ui(
                request_id, sid, widgets, orig_info, mod_info, 
                real_original_wem_size, real_modified_wem_size
            )
        )

        self.bnk_loader_thread.start()

    def fix_bnk_size(self, file_id, lang, new_size):
        """Updates the BNK file with the correct WEM file size."""
        DEBUG.log(f"Attempting to fix BNK size for ID {file_id} in lang {lang} to new size {new_size}")
        
        try:
            source_id = int(file_id)
            bnk_fixed = False
            
            bnk_files_info = self.find_relevant_bnk_files()

            for bnk_path, bnk_type in bnk_files_info:
                if bnk_type == 'sfx':
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems", "SFX"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                else:
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                
                if not os.path.exists(mod_bnk_path):
                    continue
                
                editor = BNKEditor(mod_bnk_path)
                
                if editor.modify_sound(source_id, new_size=new_size, find_by_size=None):
                    editor.save_file()
                    self.invalidate_bnk_cache(source_id)
                    
                    DEBUG.log(f"Successfully fixed size in {os.path.basename(mod_bnk_path)}.")
                    bnk_fixed = True
                    break

            if bnk_fixed:
                QtWidgets.QMessageBox.information(self, "Success", "BNK file size has been successfully updated!")
                self.on_selection_changed(lang)
            else:
                QtWidgets.QMessageBox.warning(self, "Error", f"Could not find an entry for ID {file_id} in any modded BNK file to fix.")
        
        except Exception as e:
            DEBUG.log(f"Error fixing BNK size: {e}", "ERROR")
            QtWidgets.QMessageBox.critical(self, "Error", f"An unexpected error occurred while fixing the BNK file:\n{str(e)}")
    def update_bnk_info_ui(self, request_id, source_id, widgets, original_bnk_info, modified_bnk_info, real_original_wem_size, real_modified_wem_size):
        if request_id != self.current_bnk_request_id:
            return

        try:
            widgets["original_info_labels"]["bnk_size"].isVisible()
        except RuntimeError:
            DEBUG.log("Widgets were deleted, BNK UI update cancelled.", "WARNING")
            return

        bnk_size_button = widgets["modified_info_labels"]["bnk_size"]
        
        try:
            bnk_size_button.clicked.disconnect()
        except TypeError:
            pass
        bnk_size_button.setEnabled(False)
        bnk_size_button.setCursor(QtCore.Qt.ArrowCursor)

        is_dark = self.settings.data.get("theme", "light") == "dark"
        text_color = "#d4d4d4" if is_dark else "#000000"  

        bnk_size_button.setStyleSheet(f"QPushButton {{ text-align: left; padding: 0; border: none; background: transparent; color: {text_color}; }}")

        if original_bnk_info:
            widgets["original_info_labels"]["bnk_size"].setText(f"{original_bnk_info.file_size / 1024:.1f} KB")
            fx_status = "Disabled" if original_bnk_info.override_fx else "Enabled"
            fx_color = "#F44336" if original_bnk_info.override_fx else "#4CAF50"
            widgets["original_info_labels"]["override_fx"].setText(f"<b style='color:{fx_color};'>{fx_status}</b>")
        else:
            widgets["original_info_labels"]["bnk_size"].setText("N/A")
            widgets["original_info_labels"]["override_fx"].setText("N/A")
            
        file_id = str(source_id)
        current_lang = self.get_current_language()
        
        mod_wem_exists = real_modified_wem_size > 0

        if modified_bnk_info:
            expected_bnk_size = modified_bnk_info.file_size
            
            if mod_wem_exists:
                actual_wem_size = real_modified_wem_size 
                
                if actual_wem_size == expected_bnk_size:
                    bnk_size_button.setText(f"{expected_bnk_size / 1024:.1f} KB")
                    bnk_size_button.setToolTip("OK: Actual file size matches the BNK record.")
            
                    bnk_size_button.setStyleSheet("QPushButton { text-align: left; padding: 0; border: none; color: green; font-weight: bold; background: transparent; }")
                else:
                    bnk_size_button.setText(f"Mismatch! Click to fix")
                    bnk_size_button.setToolTip(f"BNK expects {expected_bnk_size:,} bytes, but file is {actual_wem_size:,} bytes.\nClick to update the BNK record.")
               
                    bnk_size_button.setStyleSheet("QPushButton { text-align: left; padding: 0; border: none; color: red; font-weight: bold; text-decoration: underline; background: transparent; }")
                    bnk_size_button.setCursor(QtCore.Qt.PointingHandCursor)
                    bnk_size_button.setEnabled(True)
                    bnk_size_button.clicked.connect(lambda: self.fix_bnk_size(file_id, current_lang, actual_wem_size))
            else:
                if original_bnk_info and expected_bnk_size != original_bnk_info.file_size:
                    bnk_size_button.setText("Missing WEM! Click to revert")
                    bnk_size_button.setToolTip(f"BNK record was modified, but the WEM file is missing.\nClick to revert the BNK record to its original state.")
                 
                    bnk_size_button.setStyleSheet("QPushButton { text-align: left; padding: 0; border: none; color: red; font-weight: bold; text-decoration: underline; background: transparent; }")
                    bnk_size_button.setCursor(QtCore.Qt.PointingHandCursor)
                    bnk_size_button.setEnabled(True)
                    bnk_size_button.clicked.connect(lambda: self.revert_single_bnk_entry(file_id, current_lang))
                else:
            
                    bnk_size_button.setText(f"{expected_bnk_size / 1024:.1f} KB")
                    bnk_size_button.setStyleSheet(f"QPushButton {{ text-align: left; padding: 0; border: none; color: {text_color}; background: transparent; }}")

            fx_status = "Disabled" if modified_bnk_info.override_fx else "Enabled"
            fx_color = "#F44336" if modified_bnk_info.override_fx else "#4CAF50"
            widgets["modified_info_labels"]["override_fx"].setText(f"<b style='color:{fx_color};'>{fx_status}</b>")
        
        else:
            bnk_size_button.setText("N/A")
            widgets["modified_info_labels"]["override_fx"].setText("N/A")
    def revert_single_bnk_entry(self, file_id, lang):
        """Reverts BNK entry to original values in ALL matching BNK files."""
        DEBUG.log(f"Reverting BNK entries for ID {file_id}")
        try:
            source_id = int(file_id)
            reverted_count = 0
            
            bnk_files_info = self.find_relevant_bnk_files()

            for bnk_path, bnk_type in bnk_files_info:
               
                original_bnk = BNKEditor(bnk_path)
                original_entries = original_bnk.find_sound_by_source_id(source_id)
                if not original_entries:
                    continue
                
                original_entry = original_entries[0]

                if bnk_type == 'sfx':
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems", "SFX"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                else:
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                
                if os.path.exists(mod_bnk_path):
                    mod_editor = BNKEditor(mod_bnk_path)
                    
                    if mod_editor.modify_sound(source_id, 
                                               new_size=original_entry.file_size, 
                                               override_fx=original_entry.override_fx):
                        mod_editor.save_file()
                        self.invalidate_bnk_cache(source_id)
                        reverted_count += 1
                        DEBUG.log(f"Reverted entry in {os.path.basename(mod_bnk_path)}")

            if reverted_count > 0:
                QtWidgets.QMessageBox.information(self, "Success", f"Reverted {reverted_count} BNK entries.")
                self.on_selection_changed(lang)
            else:
                QtWidgets.QMessageBox.warning(self, "Error", "No BNK entries needed reverting.")

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))
    def get_file_durations(self, file_id, lang, widgets):

        wem_path = os.path.join(self.wem_root, lang, f"{file_id}.wem")
        self.original_duration = 0
        
        if os.path.exists(wem_path):
            duration = self.get_wem_duration(wem_path)
            if duration > 0:
                self.original_duration = duration
                minutes = int(duration // 60000)
                seconds = (duration % 60000) / 1000.0
                widgets["info_labels"]["duration"].setText(f"{minutes:02d}:{seconds:05.2f}")
            else:
                widgets["info_labels"]["duration"].setText("Unknown")
        else:
            widgets["info_labels"]["duration"].setText("N/A")
            

        mod_wem_path = self.get_mod_path(file_id, lang)
        self.mod_duration = 0
        
        if os.path.exists(mod_wem_path):
            duration = self.get_wem_duration(mod_wem_path)
            if duration > 0:
                self.mod_duration = duration
                minutes = int(duration // 60000)
                seconds = (duration % 60000) / 1000.0
                widgets["info_labels"]["mod_duration"].setText(f"{minutes:02d}:{seconds:05.2f}")
                
            else:
                widgets["info_labels"]["mod_duration"].setText("Unknown")
        else:
            widgets["info_labels"]["mod_duration"].setText("N/A")
    
    def get_wem_duration(self, wem_path):

        try:
            result = subprocess.run(
                [self.vgmstream_path, "-m", wem_path],
                capture_output=True,
                text=True,
                timeout=5,
                startupinfo=startupinfo,
                creationflags=CREATE_NO_WINDOW,
                encoding='utf-8',
                errors='ignore'
            )
            
            if result.returncode == 0:
                samples = None
                sample_rate = 48000 
                
                for line in result.stdout.split('\n'):
                    if "stream total samples:" in line:
                        samples = int(line.split(':')[1].strip().split()[0])
                    elif "sample rate:" in line:
                        sample_rate = int(line.split(':')[1].strip().split()[0])
                
                if samples:
                    duration_ms = int((samples / sample_rate) * 1000)
                    return duration_ms
                    
        except Exception as e:
            DEBUG.log(f"Error getting duration: {e}", "ERROR")
            
        return 0   
    def get_file_size(self, file_id, lang, widgets):
   
        wem_path = os.path.join(self.wem_root, lang, f"{file_id}.wem")
        if os.path.exists(wem_path):
            self.original_size = os.path.getsize(wem_path)
            widgets["info_labels"]["size"].setText(f"{self.original_size / 1024:.1f} KB")
        else:
            self.original_size = 0
            widgets["info_labels"]["size"].setText("N/A")
            
        mod_wem_path = self.get_mod_path(file_id, lang)
        
        if os.path.exists(mod_wem_path):
            self.mod_size = os.path.getsize(mod_wem_path)
            widgets["info_labels"]["mod_size"].setText(f"{self.mod_size / 1024:.1f} KB")
            
            
        else:
            self.mod_size = 0
            widgets["info_labels"]["mod_size"].setText("N/A")
            widgets["size_warning"].hide()
        

    def play_current(self, play_mod=False):
        current_lang = self.get_current_language()
        if not current_lang or current_lang not in self.tab_widgets:
            return
            
        widgets = self.tab_widgets[current_lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        if not items or items[0].childCount() > 0:
            return
        self.stop_audio()    
        item = items[0]
        entry = item.data(0, QtCore.Qt.UserRole)
        if not entry:
            return
            
        id_ = entry.get("Id", "")
        
        if play_mod:

            if current_lang != "SFX":
                wem_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", current_lang, f"{id_}.wem")
            else:
                wem_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", f"{id_}.wem")
          
            
            if not os.path.exists(wem_path):
             
                old_wem_path_lang = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", current_lang, f"{id_}.wem")
                old_wem_path_sfx = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", f"{id_}.wem")
                
                if os.path.exists(old_wem_path_lang):
                    wem_path = old_wem_path_lang
                elif os.path.exists(old_wem_path_sfx):
                    wem_path = old_wem_path_sfx
                else:
                    self.status_bar.showMessage(f"Mod audio not found: {wem_path}", 3000)
                    DEBUG.log(f"Mod audio not found at: {wem_path}", "WARNING")
                    return
            self.is_playing_mod = True
        else:
      
            wem_path = self.get_original_path(id_, current_lang)
            
            if not os.path.exists(wem_path):
                self.status_bar.showMessage(f"File not found: {wem_path}", 3000)
                return
            self.is_playing_mod = False
            
        source_type = "MOD" if play_mod else "Original"
        self.status_bar.showMessage(f"Converting {source_type} to WAV...")
        QtWidgets.QApplication.processEvents()
        
        try:
            temp_file_handle = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            temp_wav = temp_file_handle.name
            temp_file_handle.close()
            DEBUG.log(f"Generated unique temp WAV path: {temp_wav}")
        except Exception as e:
            DEBUG.log(f"Failed to create temp file: {e}", "ERROR")
            self.status_bar.showMessage("Error creating temporary file", 3000)
            return
        
        thread = threading.Thread(target=self._convert_and_play, args=(wem_path, temp_wav, current_lang))
        thread.start()
    def _convert_and_play(self, wem_path, wav_path, lang):
        ok, err = self.wem_to_wav_vgmstream(wem_path, wav_path)
        
        QtCore.QMetaObject.invokeMethod(self, "_play_converted", 
                                       QtCore.Qt.QueuedConnection,
                                       QtCore.Q_ARG(bool, ok),
                                       QtCore.Q_ARG(str, wav_path),
                                       QtCore.Q_ARG(str, err),
                                       QtCore.Q_ARG(str, lang))

    @QtCore.pyqtSlot(bool, str, str, str)
    def _play_converted(self, ok, wav_path, error, lang):
        if ok:
            self.temp_wav = wav_path
            self.audio_player.play(wav_path)
            source_type = "MOD" if self.is_playing_mod else "Original"
            self.status_bar.showMessage(f"Playing {source_type} audio...", 2000)
            

            if lang in self.tab_widgets:
                widgets = self.tab_widgets[lang]
                
                try:
                    self.audio_player.positionChanged.disconnect()
                    self.audio_player.durationChanged.disconnect()
                except:
                    pass
                    
                self.audio_player.positionChanged.connect(
                    lambda pos: self.update_audio_position(pos, widgets))
                self.audio_player.durationChanged.connect(
                    lambda dur: self.update_audio_duration(dur, widgets))
        else:
            self.status_bar.showMessage(f"Conversion failed: {error}", 3000)

    def update_audio_position(self, position, widgets):
        widgets["audio_progress"].setValue(position)
        self.update_time_label(widgets)

    def update_audio_duration(self, duration, widgets):
        widgets["audio_progress"].setMaximum(duration)
        self.update_time_label(widgets)

    def update_time_label(self, widgets):
        position = self.audio_player.player.position()
        duration = self.audio_player.player.duration()
        pos_min = position // 60000
        pos_sec = (position % 60000) / 1000
        pos_str = f"{pos_min:02d}:{pos_sec:06.3f}" 

        dur_min = duration // 60000
        dur_sec = (duration % 60000) / 1000
        dur_str = f"{dur_min:02d}:{dur_sec:06.3f}"

        source_type = " [MOD]" if self.is_playing_mod else ""
        
        time_text = f"{pos_str} / {dur_str} {source_type}"
        
        widgets["time_label"].setText(time_text)

    def stop_audio(self):
        self.audio_player.stop()
        if self.temp_wav and os.path.exists(self.temp_wav):
            try:
                os.remove(self.temp_wav)
            except:
                pass
        self.temp_wav = None
        self.is_playing_mod = False

    def edit_current_subtitle(self):
        current_lang = self.get_current_language()
        if not current_lang or current_lang not in self.tab_widgets:
            return
            
        widgets = self.tab_widgets[current_lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        if not items or items[0].childCount() > 0:
            return
            
        item = items[0]
        entry = item.data(0, QtCore.Qt.UserRole)
        if not entry:
            return
            
        shortname = entry.get("ShortName", "")
        key = os.path.splitext(shortname)[0]
        current_subtitle = self.subtitles.get(key, "")
        original_subtitle = self.original_subtitles.get(key, "")
        
        DEBUG.log(f"Editing subtitle for: {key} from main audio tab")
        
        editor = SubtitleEditor(self, key, current_subtitle, original_subtitle)
        if editor.exec_() == QtWidgets.QDialog.Accepted:
            new_subtitle = editor.get_text()
            self.subtitles[key] = new_subtitle
        
            if key in self.key_to_file_map:
                file_info = self.key_to_file_map[key]
                self.dirty_subtitle_files.add(file_info['path'])
                DEBUG.log(f"Marked file as dirty from main tab edit: {file_info['path']}")

            if new_subtitle != original_subtitle:
                self.modified_subtitles.add(key)
            else:
                self.modified_subtitles.discard(key)
            
            try:
                if not self.is_item_deleted(item):
                    item.setText(2, new_subtitle)
                    current_status = item.text(3).replace("✓", "")
                    if key in self.modified_subtitles:
                        item.setText(3, "✓" + current_status)
                    else:
                        item.setText(3, current_status)
                    
                    widgets["subtitle_text"].setPlainText(new_subtitle)
                    if original_subtitle and original_subtitle != new_subtitle:
                        widgets["original_subtitle_label"].setText(f"{self.tr('original')}: {original_subtitle}")
                        widgets["original_subtitle_label"].show()
                    else:
                        widgets["original_subtitle_label"].hide()
            except RuntimeError:
                DEBUG.log("Item was deleted during update from main tab, refreshing tree.", "WARNING")
                self.populate_tree(current_lang)

            self.status_bar.showMessage("Subtitle updated", 2000)
            self.update_status()

    def find_tree_item_by_key(self, tree, target_key, target_entry):

        def search_items(parent_item):
            for i in range(parent_item.childCount()):
                item = parent_item.child(i)
                
                if item.childCount() == 0: 
                    try:
                        entry = item.data(0, QtCore.Qt.UserRole)
                        if entry:
                            shortname = entry.get("ShortName", "")
                            key = os.path.splitext(shortname)[0]
                            
                            if key == target_key:
                                return item
                    except RuntimeError:
                  
                        continue
                else:
           
                    result = search_items(item)
                    if result:
                        return result
            return None
        
        try:
            root = tree.invisibleRootItem()
            return search_items(root)
        except RuntimeError:
            return None

    def is_item_deleted(self, item):
        """Check if QTreeWidgetItem is still valid"""
        try:
 
            _ = item.text(0)
            return False
        except RuntimeError:
            return True

    def revert_subtitle(self):
        """Revert selected subtitle to original"""
        current_lang = self.get_current_language()
        if not current_lang or current_lang not in self.tab_widgets:
            return
            
        widgets = self.tab_widgets[current_lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        if not items or items[0].childCount() > 0:
            return
            
        item = items[0]
        entry = item.data(0, QtCore.Qt.UserRole)
        if not entry:
            return
            
        shortname = entry.get("ShortName", "")
        key = os.path.splitext(shortname)[0]
        
        if key in self.original_subtitles:
            original = self.original_subtitles[key]
            self.subtitles[key] = original
            self.modified_subtitles.discard(key)
            

            item.setText(2, original)
            current_status = item.text(3).replace("✓", "")
            item.setText(3, current_status)
            
            widgets["subtitle_text"].setPlainText(original)
            widgets["original_subtitle_label"].hide()
            
            self.status_bar.showMessage("Subtitle reverted to original", 2000)
            self.update_status()

    def import_custom_subtitles(self):
        """Import custom subtitles from another locres file"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, self.tr("import_custom_subtitles"), "", "Locres Files (*.locres)"
        )
        
        if not path:
            return
            
        DEBUG.log(f"Importing custom subtitles from: {path}")
        
        try:

            custom_subtitles = self.locres_manager.export_locres(path)
            
            if not custom_subtitles:
                QtWidgets.QMessageBox.warning(self, "Import Error", "No subtitles found in the selected file")
                return
                
            DEBUG.log(f"Found {len(custom_subtitles)} subtitles in custom file")
            
            conflicts = {}
            for key, new_value in custom_subtitles.items():
                if key in self.subtitles and self.subtitles[key]:
                    conflicts[key] = {
                        "existing": self.subtitles[key],
                        "new": new_value
                    }
            
            if conflicts:

                conflict_list = []
                for key, values in list(conflicts.items())[:10]: 
                    conflict_list.append(f"{key}:\n  Existing: {values['existing'][:50]}...\n  New: {values['new'][:50]}...")
                
                if len(conflicts) > 10:
                    conflict_list.append(f"\n... and {len(conflicts) - 10} more conflicts")
                
                msg = QtWidgets.QMessageBox()
                msg.setWindowTitle(self.tr("conflict_detected"))
                msg.setText(self.tr("conflict_message").format(conflicts="\n\n".join(conflict_list)))
                
                use_existing_btn = msg.addButton(self.tr("use_existing"), QtWidgets.QMessageBox.ActionRole)
                use_new_btn = msg.addButton(self.tr("use_new"), QtWidgets.QMessageBox.ActionRole)
                merge_btn = msg.addButton(self.tr("merge_all"), QtWidgets.QMessageBox.ActionRole)
                msg.addButton(QtWidgets.QMessageBox.Cancel)
                
                msg.exec_()
                
                if msg.clickedButton() == use_existing_btn:

                    for key, value in custom_subtitles.items():
                        if key not in self.subtitles or not self.subtitles[key]:
                            self.subtitles[key] = value
                            if key not in self.original_subtitles:
                                self.original_subtitles[key] = ""
                            self.modified_subtitles.add(key)
                elif msg.clickedButton() == use_new_btn:

                    for key, value in custom_subtitles.items():
                        self.subtitles[key] = value
                        if key not in self.original_subtitles:
                            self.original_subtitles[key] = ""
                        if value != self.original_subtitles.get(key, ""):
                            self.modified_subtitles.add(key)
                elif msg.clickedButton() == merge_btn:

                    for key, value in custom_subtitles.items():
                        if key not in self.subtitles or not self.subtitles[key]:
                            self.subtitles[key] = value
                            if key not in self.original_subtitles:
                                self.original_subtitles[key] = ""
                            self.modified_subtitles.add(key)
                else:
                    return  
            else:
                
                for key, value in custom_subtitles.items():
                    self.subtitles[key] = value
                    if key not in self.original_subtitles:
                        self.original_subtitles[key] = ""
                    if value != self.original_subtitles.get(key, ""):
                        self.modified_subtitles.add(key)
            
            current_lang = self.get_current_language()
            if current_lang and current_lang in self.tab_widgets:
                self.populate_tree(current_lang)
                
            self.update_status()
            self.status_bar.showMessage(f"Imported {len(custom_subtitles)} subtitles", 3000)
            
        except Exception as e:
            DEBUG.log(f"Error importing custom subtitles: {str(e)}", "ERROR")
            QtWidgets.QMessageBox.warning(self, "Import Error", str(e))

    def deploy_and_run_game(self):
        """Deploy mod to game and run it"""
        game_path = self.settings.data.get("game_path", "")
        
        if not game_path or not os.path.exists(game_path):
            QtWidgets.QMessageBox.warning(self, self.tr("error"), self.tr("no_game_path"))
            return
            
        mod_file = f"{self.mod_p_path}.pak"
        
        if not os.path.exists(mod_file):
            reply = QtWidgets.QMessageBox.question(
                self, self.tr("compile_mod"), 
                self.tr("mod_not_found_compile"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            
            if reply == QtWidgets.QMessageBox.Yes:
                self.compile_mod()
                
                import time
                for i in range(10):
                    if os.path.exists(mod_file):
                        break
                    time.sleep(1)
                    
                if not os.path.exists(mod_file):
                    QtWidgets.QMessageBox.warning(self, self.tr("error"), self.tr("mod_compilation_failed"))
                    return
            else:
                return
        

        try:
            paks_path = os.path.join(game_path, "OPP", "Content", "Paks")
            os.makedirs(paks_path, exist_ok=True)
            
            target_mod_path = os.path.join(paks_path, os.path.basename(mod_file))
            
            DEBUG.log(f"Deploying mod from {mod_file} to {target_mod_path}")
            shutil.copy2(mod_file, target_mod_path)
            
            self.status_bar.showMessage(self.tr("mod_deployed"), 3000)
            
            exe_files = []
            for file in os.listdir(game_path):
                if file.endswith(".exe") and "Shipping" in file:
                    exe_files.append(file)
                    
            if not exe_files:

                for file in os.listdir(game_path):
                    if file.endswith(".exe"):
                        exe_files.append(file)
                        
            if exe_files:
                game_exe = os.path.join(game_path, exe_files[0])
                DEBUG.log(f"Launching game: {game_exe}")
                self.status_bar.showMessage(self.tr("game_launching"), 3000)
                subprocess.Popen(
                    [game_exe],
                    startupinfo=startupinfo,
                    creationflags=CREATE_NO_WINDOW
                )
            else:
                QtWidgets.QMessageBox.warning(self, "Error", "Game executable not found")
                
        except Exception as e:
            DEBUG.log(f"Error deploying mod: {str(e)}", "ERROR")
            QtWidgets.QMessageBox.warning(self, "Error", str(e))
    def export_subtitles_for_game(self):
        """Export modified subtitles to game mod structure with language filtering"""
        DEBUG.log("=== Export Subtitles for Game (Fixed Language Filter) ===")
        
        if not self.modified_subtitles:
            QtWidgets.QMessageBox.information(self, "No Changes", "No modified subtitles to export")
            return
        
        current_language = self.settings.data["subtitle_lang"]
        DEBUG.log(f"Exporting for language: {current_language}")
        
        progress = ProgressDialog(self, "Exporting Subtitles for Game")
        progress.show()
        

        self.subtitle_export_status.clear()
        self.subtitle_export_status.append("=== Starting Export ===")
        self.subtitle_export_status.append(f"Language: {current_language}")
        self.subtitle_export_status.append(f"Modified subtitles: {len(self.modified_subtitles)}")
        self.subtitle_export_status.append("")
        
        try:
            exported_files = 0
            
            subtitle_files_to_update = {}
            
            for modified_key in self.modified_subtitles:
                found_in_file = None
                
                for file_key, file_info in self.all_subtitle_files.items():
                    if file_info['language'] != current_language:
                        continue
                        
                    working_path = file_info['path'].replace('.locres', '_working.locres')
                    check_path = working_path if os.path.exists(working_path) else file_info['path']
                    
                    file_subtitles = self.locres_manager.export_locres(check_path)
                    if modified_key in file_subtitles:
                        found_in_file = file_info
                        break
                
                if found_in_file:
                    file_path = found_in_file['path']
                    if file_path not in subtitle_files_to_update:
                        working_path = file_path.replace('.locres', '_working.locres')
                        source_path = working_path if os.path.exists(working_path) else file_path
                        
                        subtitle_files_to_update[file_path] = {
                            'file_info': found_in_file,
                            'all_subtitles': self.locres_manager.export_locres(source_path),
                            'working_path': working_path
                        }

                    subtitle_files_to_update[file_path]['all_subtitles'][modified_key] = self.subtitles[modified_key]
                else:
                    DEBUG.log(f"Warning: Could not find source file for modified key: {modified_key}", "WARNING")
            
            DEBUG.log(f"Found {len(subtitle_files_to_update)} files to save for language {current_language}")
            
            if not subtitle_files_to_update:
                QtWidgets.QMessageBox.warning(
                    self, "Export Error", 
                    f"No subtitle files found for language '{current_language}'.\n"
                    f"Please check that you have the correct subtitle files in your Localization folder."
                )
                progress.close()
                return

            for i, (file_path, data) in enumerate(subtitle_files_to_update.items()):
                file_info = data['file_info']
                all_subtitles = data['all_subtitles']
                
                progress.set_progress(
                    int((i / len(subtitle_files_to_update)) * 100),
                    f"Processing {file_info['filename']} ({current_language})..."
                )
                
                target_dir = os.path.join(
                    self.mod_p_path, "OPP", "Content", 
                    "Localization", file_info['category'], current_language
                )
                os.makedirs(target_dir, exist_ok=True)
                
                target_file = os.path.join(target_dir, file_info['filename'])
                
                DEBUG.log(f"Exporting to: {target_file}")
                
                shutil.copy2(file_path, target_file)
                
                modified_subs = {k: v for k, v in all_subtitles.items() if k in self.modified_subtitles}
                
             
                success = self.locres_manager.import_locres(target_file, all_subtitles)
                
                if success:
                    exported_files += 1
                    self.subtitle_export_status.append(f"✓ {file_info['relative_path']} ({len(modified_subs)} subtitles)")
                    DEBUG.log(f"Successfully exported {file_info['filename']} with {len(modified_subs)} modified subtitles")
                else:
                    self.subtitle_export_status.append(f"✗ {file_info['relative_path']} - FAILED")
                    DEBUG.log(f"Failed to export {file_info['filename']}", "ERROR")
            
            progress.set_progress(100, "Export complete!")
            
            self.subtitle_export_status.append("")
            self.subtitle_export_status.append("=== Export Complete ===")
            self.subtitle_export_status.append(f"Files exported: {exported_files}")
            self.subtitle_export_status.append(f"Location: {os.path.join(self.mod_p_path, 'OPP', 'Content', 'Localization')}")
            
            QtWidgets.QMessageBox.information(
                self, "Success", 
                f"Subtitles exported successfully!\n\n"
                f"Language: {current_language}\n"
                f"Files exported: {exported_files}\n"
                f"Modified subtitles: {len(self.modified_subtitles)}\n\n"
                f"Location: {os.path.join(self.mod_p_path, 'OPP', 'Content', 'Localization')}"
            )
            
        except Exception as e:
            DEBUG.log(f"Export error: {str(e)}", "ERROR")
            self.subtitle_export_status.append(f"ERROR: {str(e)}")
            QtWidgets.QMessageBox.warning(self, "Export Error", str(e))
            
        progress.close()
        DEBUG.log("=== Export Complete ===")
    def save_current_wav(self):
        current_lang = self.get_current_language()
        if not current_lang or current_lang not in self.tab_widgets:
            return
            
        widgets = self.tab_widgets[current_lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        if not items:
            return

        if len(items) > 1:
            self.batch_export_wav(items, current_lang)
            return
            
        item = items[0]
        if item.childCount() > 0:
            return
            
        entry = item.data(0, QtCore.Qt.UserRole)
        if not entry:
            return
            
        id_ = entry.get("Id", "")
        shortname = entry.get("ShortName", "")

        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle(self.tr("export_audio"))
        msg.setText(self.tr("which_version_export"))
        
        original_btn = msg.addButton(self.tr("original"), QtWidgets.QMessageBox.ActionRole)
        mod_btn = None
        
        mod_wem_path = self.get_mod_path(id_, current_lang)
        if mod_wem_path and os.path.exists(mod_wem_path):
            mod_btn = msg.addButton(self.tr("mod"), QtWidgets.QMessageBox.ActionRole)
            
        msg.addButton(QtWidgets.QMessageBox.Cancel)
        self.show_dialog(msg)
        
        clicked_button = msg.clickedButton()
        wem_path = None

        if clicked_button == original_btn:
            wem_path = self.get_original_path(id_, current_lang)
        elif mod_btn and clicked_button == mod_btn:
            wem_path = mod_wem_path
        else:
            return
            
        if not wem_path or not os.path.exists(wem_path):
            self.status_bar.showMessage(f"Source file not found: {wem_path}", 3000)
            return
            
        save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, self.tr("save_as_wav"), shortname, 
            f"{self.tr('wav_files')} (*.wav)"
        )
        
        if save_path:
            if os.path.exists(save_path):
                reply = self.show_message_box(
                    QtWidgets.QMessageBox.Question,
                    "File Exists",
                    f"The file '{os.path.basename(save_path)}' already exists.",
                    "Do you want to overwrite it?",
                    buttons=QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                )
                if reply == QtWidgets.QMessageBox.No:
                    return

            progress = ProgressDialog(self, f"Exporting {shortname}...")
            progress.show()
            progress.raise_()
            progress.activateWindow()

            thread = threading.Thread(
                target=self._export_single_wav_thread, 
                args=(wem_path, save_path, progress)
            )
            thread.daemon = True
            thread.start()
    def _export_single_wav_thread(self, wem_path, save_path, progress_dialog):
        try:
            ok, err = self.wem_to_wav_vgmstream(wem_path, save_path)
            
            QtCore.QMetaObject.invokeMethod(
                self, "_on_single_export_finished", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(bool, ok),
                QtCore.Q_ARG(str, save_path),
                QtCore.Q_ARG(str, err),
                QtCore.Q_ARG(object, progress_dialog)
            )
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(
                self, "_on_single_export_finished", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(bool, False),
                QtCore.Q_ARG(str, save_path),
                QtCore.Q_ARG(str, str(e)),
                QtCore.Q_ARG(object, progress_dialog)
            )
    @QtCore.pyqtSlot(bool, str, str, object)
    def _on_single_export_finished(self, ok, save_path, error_message, progress_dialog):
        progress_dialog.close() 

        if ok:
            self.status_bar.showMessage(f"Saved: {save_path}", 3000)
            self.show_message_box(
                QtWidgets.QMessageBox.Information,
                self.tr("export_complete"),
                f"File successfully exported to:\n{save_path}"
            )
        else:
            self.show_message_box(
                QtWidgets.QMessageBox.Warning,
                "Error",
                f"Conversion failed: {error_message}"
            )
    def wem_to_wav_vgmstream(self, wem_path, wav_path):
        try:
            result = subprocess.run(
                [self.vgmstream_path, wem_path, "-o", wav_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                startupinfo=startupinfo,
                creationflags=CREATE_NO_WINDOW
            )
            return result.returncode == 0, result.stderr.decode()
        except Exception as e:
            return False, str(e)
    def toggle_ingame_effects(self):
        current_lang = self.get_current_language()
        if not current_lang:
            return

        widgets = self.tab_widgets[current_lang]
        tree = widgets["tree"]
        file_items = [item for item in tree.selectedItems() if item.childCount() == 0]

        if not file_items:
            return

        bnk_files = self.find_relevant_bnk_files()
        if not bnk_files:
            QtWidgets.QMessageBox.warning(self, "Error", "No BNK files found for modification.")
            return
            
        modified_count = 0
        for item in file_items:
            entry = item.data(0, QtCore.Qt.UserRole)
            if not entry:
                continue

            source_id = int(entry.get("Id", ""))
            shortname = entry.get("ShortName", "")
            
            bnk_files_info = self.find_relevant_bnk_files()
            for bnk_path, bnk_type in bnk_files_info:
                if bnk_type == 'sfx':
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems", "SFX"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                else: # 'lang'
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)

                original_editor = BNKEditor(bnk_path)
                if not original_editor.find_sound_by_source_id(source_id):
                    continue 

                if not os.path.exists(mod_bnk_path):
                    os.makedirs(os.path.dirname(mod_bnk_path), exist_ok=True)
                    shutil.copy2(bnk_path, mod_bnk_path)
                
                editor = BNKEditor(mod_bnk_path)
                current_entries = editor.find_sound_by_source_id(source_id)

                if current_entries:
                    current_state = current_entries[0].override_fx
                    new_state = not current_state
                    
                    if editor.modify_sound(source_id, override_fx=new_state, find_by_size=None):
                        editor.save_file()
                        self.invalidate_bnk_cache(source_id)
                        DEBUG.log(f"FX for {shortname} (ID: {source_id}) changed from {current_state} to {new_state} in {os.path.basename(mod_bnk_path)}")
                        modified_count += 1
                        bnk_found_and_modified = True
                        break 
            
            if not bnk_found_and_modified:
                DEBUG.log(f"Could not find or modify record for {shortname} (ID: {source_id}) in any BNK file.", "WARNING")

        self.populate_tree(current_lang)
        self.status_bar.showMessage(f"In-Game Effects changed for {modified_count} files.", 3000)
    def create_menu_bar(self):
        menubar = self.menuBar()
        
        file_menu = menubar.addMenu(self.tr("file_menu"))

        self.save_action = file_menu.addAction(self.tr("save_subtitles"))
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self.save_subtitles_to_file)

        # self.export_action = file_menu.addAction(self.tr("export_subtitles"))
        # self.export_action.triggered.connect(self.export_subtitles)

        # self.import_action = file_menu.addAction(self.tr("import_subtitles"))
        # self.import_action.triggered.connect(self.import_subtitles)

        file_menu.addSeparator()

        self.exit_action = file_menu.addAction(self.tr("exit"))
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)
        
        # Edit menu
        edit_menu = menubar.addMenu(self.tr("edit_menu"))
        
        self.revert_action = edit_menu.addAction(self.tr("revert_to_original"))
        self.revert_action.setShortcut("Ctrl+R")
        self.revert_action.triggered.connect(self.revert_subtitle)
        
        edit_menu.addSeparator()
        
        
        # Tools menu
        tools_menu = menubar.addMenu(self.tr("tools_menu"))
        
        self.compile_mod_action = tools_menu.addAction(self.tr("compile_mod"))
        self.compile_mod_action.triggered.connect(self.compile_mod)
        
        self.deploy_action = tools_menu.addAction(self.tr("deploy_and_run"))
        self.deploy_action.setShortcut("F5")
        self.deploy_action.triggered.connect(self.deploy_and_run_game)
        tools_menu.addSeparator()

        self.rebuild_bnk_action = tools_menu.addAction(self.tr("rebuild_bnk_index"))
        self.rebuild_bnk_action.setToolTip(self.tr("rebuild_bnk_tooltip"))
        self.rebuild_bnk_action.triggered.connect(self.rebuild_bnk_index)
        tools_menu.addSeparator()
        self.rescan_orphans_action = tools_menu.addAction(self.tr("rescan_orphans_action"))
        self.rescan_orphans_action.setToolTip(self.tr("rescan_orphans_tooltip"))
        self.rescan_orphans_action.triggered.connect(self.perform_blocking_orphan_scan)
        tools_menu.addSeparator()
        self.debug_action = tools_menu.addAction(self.tr("show_debug"))
        self.debug_action.setShortcut("Ctrl+D")
        self.debug_action.triggered.connect(self.show_debug_console)
        
        tools_menu.addSeparator()
        
        self.settings_action = tools_menu.addAction(self.tr("settings"))
        self.settings_action.setShortcut("Ctrl+,")
        self.settings_action.triggered.connect(self.show_settings_dialog)
        
        # Help menu
        help_menu = menubar.addMenu(self.tr("help_menu"))

        # self.documentation_action = help_menu.addAction("📖 Documentation")
        # self.documentation_action.setShortcut("F1")
        # self.documentation_action.triggered.connect(self.show_documentation)

        self.shortcuts_action = help_menu.addAction(self.tr("keyboard_shortcuts"))
        self.shortcuts_action.triggered.connect(self.show_shortcuts)

        help_menu.addSeparator()

        self.check_updates_action = help_menu.addAction(self.tr("check_updates"))
        self.check_updates_action.triggered.connect(self.check_updates)

        self.report_bug_action = help_menu.addAction(self.tr("report_bug"))
        self.report_bug_action.triggered.connect(self.report_bug)

        help_menu.addSeparator()

        self.about_action = help_menu.addAction(self.tr("about"))
        self.about_action.triggered.connect(self.show_about)
    def load_orphans_from_cache_or_scan(self):
        """Loads orphaned files from cache or performs a synchronous scan with a progress dialog."""
        if os.path.exists(self.orphaned_cache_path):
            DEBUG.log(f"Loading orphaned files from cache: {self.orphaned_cache_path}")
            try:
                with open(self.orphaned_cache_path, 'r', encoding='utf-8') as f:
                    self.orphaned_files_cache = json.load(f)
                DEBUG.log(f"Loaded {len(self.orphaned_files_cache)} orphans from cache.")
                self.rebuild_file_list_with_orphans()
            except Exception as e:
                DEBUG.log(f"Error loading orphan cache: {e}. Starting a new scan.", "ERROR")
                self.perform_blocking_orphan_scan()
        else:
            DEBUG.log("Orphan cache not found. Starting initial scan.")
            self.perform_blocking_orphan_scan()
    def perform_blocking_orphan_scan(self):
        """Performs a synchronous scan of the Wems folder with a progress dialog, blocking the UI."""
        self.all_files = [f for f in self.all_files if f.get("Source") != "ScannedFromFileSystem"]
        self.orphaned_files_cache = []
        DEBUG.log("Cleared existing orphan files to perform a full rescan.")

        progress = ProgressDialog(self, self.tr("scan_progress_title"))
        progress.setWindowFlags(progress.windowFlags() | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
        progress.setWindowFlags(progress.windowFlags() & ~QtCore.Qt.WindowCloseButtonHint)
        progress.set_progress(0, "Preparing to scan...")
        progress.show()
        QtWidgets.QApplication.processEvents()

        known_ids = {entry.get("Id") for entry in self.load_all_soundbank_files(self.soundbanks_path) if entry.get("Id")}
        
        orphaned_entries = []
        if not os.path.exists(self.wem_root):
            progress.close()
            self.rebuild_file_list_with_orphans()
            return

        all_wem_paths = []
        for root, _, files in os.walk(self.wem_root):
            for file in files:
                if file.lower().endswith('.wem'):
                    all_wem_paths.append(os.path.join(root, file))

        wem_files_to_scan = [
            path for path in all_wem_paths 
            if os.path.splitext(os.path.basename(path))[0] not in known_ids
        ]
        
        total_files = len(wem_files_to_scan)
        if total_files == 0:
            DEBUG.log("No new orphan files found.")
            progress.close()
            self.rebuild_file_list_with_orphans() 
            self.status_bar.showMessage("No new audio files found during scan.", 5000)
            return
            
        progress.set_progress(5, f"Scanning {total_files} new files...")
        QtWidgets.QApplication.processEvents()

        for i, full_path in enumerate(wem_files_to_scan):
            if i % 20 == 0:
                QtWidgets.QApplication.processEvents()
                progress.set_progress(int((i / total_files) * 100), f"Scanning {os.path.basename(full_path)}")

            file_id = os.path.splitext(os.path.basename(full_path))[0]
      
            rel_path = os.path.relpath(os.path.dirname(full_path), self.wem_root)
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
                "Id": file_id, "Language": lang, "ShortName": short_name, 
                "Path": os.path.basename(full_path), "Source": "ScannedFromFileSystem"
            }
            orphaned_entries.append(new_entry)

        progress.set_progress(100, "Finalizing...")
        
        self.orphaned_files_cache = orphaned_entries
        try:
            with open(self.orphaned_cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.orphaned_files_cache, f, indent=2)
            DEBUG.log(f"Saved {len(orphaned_entries)} orphaned files to cache.")
        except Exception as e:
            DEBUG.log(f"Failed to save orphan cache: {e}", "ERROR")

        progress.close()
        self.rebuild_file_list_with_orphans()
        self.status_bar.showMessage(f"Rescan complete. Found and cached {len(orphaned_entries)} additional audio files.", 5000)
    def start_orphan_scan(self, force=False):
        """Starts the background thread to scan for orphaned WEM files."""
        if self.scanner_thread and self.scanner_thread.isRunning():
            DEBUG.log("Scan is already in progress.", "WARNING")
            if not force:
                return
            else:
                self.scanner_thread.stop()
                self.scanner_thread.wait()

        is_first_scan = not os.path.exists(self.orphaned_cache_path)
        if is_first_scan or force:
            if self.scan_message_box:
                self.scan_message_box.close()

            title = "Initial File Scan" if is_first_scan else "Rescanning Files"
            message = ("The app is scanning your 'Wems' folder to find all available audio files.\n\n"
                       "This may take a moment. You can continue using the main window while this is in progress.")

            self.scan_message_box = QtWidgets.QMessageBox(self)
            self.scan_message_box.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
            self.scan_message_box.setIcon(QtWidgets.QMessageBox.Information)
            self.scan_message_box.setWindowTitle(title)
            self.scan_message_box.setText("<b>Scanning in Background...</b>")
            self.scan_message_box.setInformativeText(message)
            
            hide_button = self.scan_message_box.addButton("Hide", QtWidgets.QMessageBox.ActionRole)
            hide_button.clicked.connect(self.hide_scan_notification)
            
            self.scan_message_box.setModal(False)
            self.scan_message_box.show()

        if force:
            self.all_files = [f for f in self.all_files if f.get("Source") != "ScannedFromFileSystem"]
            self.entries_by_lang = self.group_by_language()
            for lang in self.tab_widgets.keys():
                self.populate_tree(lang)
            self.status_bar.showMessage("Forced rescan started... You can continue working.", 0)
        else:
            self.status_bar.showMessage("Scanning for additional audio files... You can continue working.", 0)

        known_ids = {entry.get("Id") for entry in self.load_all_soundbank_files(self.soundbanks_path) if entry.get("Id")}
        self.scanner_thread = WemScannerThread(self.wem_root, known_ids, self)
        self.scanner_thread.scan_finished.connect(self._on_scan_finished)
        self.scanner_thread.start()
    def hide_scan_notification(self):
        """Safely closes the scanning notification message box if it exists."""
        if self.scan_message_box:
            self.scan_message_box.close()
            self.scan_message_box = None
    @QtCore.pyqtSlot(list)
    def _on_scan_finished(self, orphaned_files):
        """Handles the completion of the background WEM scan."""
        self.hide_scan_notification()

        count = len(orphaned_files)
        DEBUG.log(f"Orphan scan finished. Found {count} additional files.")
        
        self.orphaned_files_cache = orphaned_files
        try:
            with open(self.orphaned_cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.orphaned_files_cache, f, indent=2)
            DEBUG.log(f"Saved {count} orphaned files to cache.")
        except Exception as e:
            DEBUG.log(f"Failed to save orphan cache: {e}", "ERROR")

        self.rebuild_file_list_with_orphans()
        
        self.status_bar.showMessage(f"Scan complete. Found and cached {count} additional audio files.", 5000)

    def rebuild_file_list_with_orphans(self):
  
        base_files = self.load_all_soundbank_files(self.soundbanks_path)
        self._build_wem_index()

        filtered_base_files = []
        for entry in base_files:
            file_id = entry.get("Id")
      
            if file_id and file_id in self.wem_index:
                filtered_base_files.append(entry)
        
        DEBUG.log(f"Filtered SoundbanksInfo: {len(filtered_base_files)} entries have a physical .wem file (out of {len(base_files)} loaded from JSON).")

        show_orphans = self.settings.data.get("show_orphaned_files", False)
        
       
        if not filtered_base_files and self.orphaned_files_cache:
            DEBUG.log("Main database matched 0 files. Forcing display of scanned orphans.")
            self.all_files = self.orphaned_files_cache
        elif show_orphans and self.orphaned_files_cache:
         
            existing_ids = {entry["Id"] for entry in filtered_base_files}
            unique_orphans = [o for o in self.orphaned_files_cache if o["Id"] not in existing_ids]
            
            DEBUG.log(f"Adding {len(unique_orphans)} unique orphans to the main list.")
            self.all_files = filtered_base_files + unique_orphans
        else:
            self.all_files = filtered_base_files

        DEBUG.log(f"Total files to display: {len(self.all_files)}")

        self.entries_by_lang = self.group_by_language()
        
        active_tabs_to_update = list(self.populated_tabs) 
        for lang in active_tabs_to_update:
             if lang in self.tab_widgets:
                self.populate_tree(lang)
        
        for lang, widgets in self.tab_widgets.items():
            try:
                if widgets["tree"].parent() and widgets["tree"].parent().parent():
                    current_tab_index = self.tabs.indexOf(widgets["tree"].parent().parent())
                    if current_tab_index != -1:
                        total_count = len(self.entries_by_lang.get(lang, []))
                        self.tabs.setTabText(current_tab_index, f"{lang} ({total_count})")
            except:
                pass
        
        self.update_status()
    def show_debug_console(self):
        if self.debug_window is None:
            self.debug_window = DebugWindow(self)
        self.debug_window.show()
        self.debug_window.raise_()
    def get_mods_root_path(self, prompt_if_missing=False):

        mods_root = self.settings.data.get("mods_root_path", "")
        if (not mods_root or not os.path.isdir(mods_root)) and prompt_if_missing:
            QtWidgets.QMessageBox.information(self, "Setup Mods Folder", "Please select a folder where you want to store your mod profiles.")
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select a Folder to Store Your Mods")
            if folder:
                self.settings.data["mods_root_path"] = folder
                self.settings.save()
                return folder
            else:
                return None
        return mods_root

    def migrate_or_load_profiles(self):
        mods_root = self.get_mods_root_path()
        legacy_mod_p_path = os.path.join(self.base_path, "MOD_P")

        if not mods_root and os.path.exists(legacy_mod_p_path):
            DEBUG.log("Legacy MOD_P folder found. Initiating migration process.")
            self.handle_legacy_mod_p_migration(legacy_mod_p_path)
        
        self.load_profiles()

    def load_profiles(self):
        self.profiles = {}
        mods_root = self.get_mods_root_path()
        if not mods_root:
            self.update_profile_ui()
            self.set_active_profile(None)
            return

        for profile_name in os.listdir(mods_root):
            profile_path = os.path.join(mods_root, profile_name)
            profile_json_path = os.path.join(profile_path, "profile.json")
            mod_p_path = os.path.join(profile_path, f"{profile_name}_P")
            if os.path.isdir(profile_path) and os.path.exists(profile_json_path) and os.path.isdir(mod_p_path):
                try:
                    with open(profile_json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self.profiles[profile_name] = {
                        "path": profile_path,
                        "mod_p_path": mod_p_path,
                        "icon": os.path.join(profile_path, "icon.png"),
                        "data": data
                    }
                except Exception as e:
                    DEBUG.log(f"Failed to load profile '{profile_name}': {e}", "WARNING")

        last_active = self.settings.data.get("active_profile")
        if last_active and last_active in self.profiles:
            self.set_active_profile(last_active)
        elif self.profiles:
            first_profile = sorted(self.profiles.keys())[0]
            self.set_active_profile(first_profile)
        else:

            self.set_active_profile(None)

        self.update_profile_ui()
    def show_profile_manager(self):
        dialog = ProfileManagerDialog(self)
        dialog.profile_changed.connect(self.on_profile_system_changed)
        dialog.exec_()
    
    def on_profile_system_changed(self):

        self.load_profiles_from_settings()
        self.load_subtitles()

    def load_profiles_from_settings(self):
        profiles = self.settings.data.get("mod_profiles", {})
        active_name = self.settings.data.get("active_profile", "")

        if active_name and active_name in profiles:
            profile_path = profiles[active_name]
            mod_p_path = os.path.join(profile_path, f"{active_name}_P")
            
            if os.path.isdir(mod_p_path):
                self.active_profile_name = active_name
                self.mod_p_path = mod_p_path
                self.setWindowTitle(f"{self.tr('app_title')} - [{active_name}]")
                DEBUG.log(f"Loaded active profile: {active_name}")
            else:
                self.reset_active_profile()
        else:
            self.reset_active_profile()

        self.load_subtitles()

        current_lang = self.get_current_language()
        if current_lang:
            self.populate_tree(current_lang)

    def reset_active_profile(self):
        self.active_profile_name = None
        self.mod_p_path = None
        self.settings.data["active_profile"] = ""
        self.settings.save()
        self.setWindowTitle(self.tr("app_title"))
        DEBUG.log("Active profile was invalid or not set. Resetting.")
        
    def update_profile_ui(self):
        
        if not hasattr(self, 'profile_combo'):
            if self.active_profile_name:
                self.setWindowTitle(f"{self.tr('app_title')} - [{self.active_profile_name}]")
            else:
                self.setWindowTitle(self.tr("app_title"))
            return

        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        
        if not self.profiles:
            self.profile_combo.addItem("No Profiles Found")
            self.profile_combo.setEnabled(False)
            self.profile_combo.blockSignals(False)
            return

        self.profile_combo.setEnabled(True)
        for profile_name in sorted(self.profiles.keys()):
            icon_path = self.profiles[profile_name]["icon"]
            icon = QtGui.QIcon(icon_path) if os.path.exists(icon_path) else QtGui.QIcon()
            self.profile_combo.addItem(icon, profile_name)
        
        if self.active_profile_name:
            self.profile_combo.setCurrentText(self.active_profile_name)

        self.profile_combo.blockSignals(False)

    def set_active_profile(self, profile_name):
        if profile_name and profile_name in self.profiles:
            self.active_profile_name = profile_name
            self.mod_p_path = self.profiles[profile_name]["mod_p_path"]
            self.settings.data["active_profile"] = profile_name
            self.setWindowTitle(f"{self.tr('app_title')} - [{profile_name}]")
            DEBUG.log(f"Switched to profile: {profile_name}. MOD_P path: {self.mod_p_path}")
        else:
            self.active_profile_name = None
            self.mod_p_path = None
            self.settings.data["active_profile"] = ""
            self.setWindowTitle(self.tr("app_title"))
            DEBUG.log("No active profile.")
        
        self.settings.save()
        current_lang = self.get_current_language()
        if current_lang and current_lang in self.tab_widgets:
            if current_lang not in self.populated_tabs:
                 self.populated_tabs.add(current_lang)
            self.populate_tree(current_lang)

    def switch_profile_by_index(self, index):
        profile_name = self.profile_combo.itemText(index)
        if profile_name in self.profiles:
            self.set_active_profile(profile_name)
    
    def create_new_profile(self):
        mods_root = self.get_mods_root_path(prompt_if_missing=True)
        if not mods_root:
            return

        dialog = ProfileDialog(self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            profile_data = dialog.get_data()
            profile_name = profile_data["name"]
            
            if profile_name in self.profiles:
                QtWidgets.QMessageBox.warning(self, "Error", "A profile with this name already exists.")
                return

            profile_path = os.path.join(mods_root, profile_name)
            mod_p_path = os.path.join(profile_path, f"{profile_name}_P")
            os.makedirs(mod_p_path, exist_ok=True)
            
            if profile_data["icon_path"] and os.path.exists(profile_data["icon_path"]):
                shutil.copy(profile_data["icon_path"], os.path.join(profile_path, "icon.png"))

            profile_json_path = os.path.join(profile_path, "profile.json")
            with open(profile_json_path, 'w', encoding='utf-8') as f:
                json.dump(profile_data["info"], f, indent=2)

            self.load_profiles()
            self.set_active_profile(profile_name) 
            self.update_profile_ui()

    def edit_current_profile(self):
        if not self.active_profile_name or not self.mod_p_path:
            QtWidgets.QMessageBox.warning(self, "No Profile Selected", "Please select or create a profile to edit.")
            return

        current_profile = self.profiles[self.active_profile_name]
        dialog = ProfileDialog(self, existing_data=current_profile)
        
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            profile_data = dialog.get_data()
            
            profile_path = current_profile["path"]
            profile_json_path = os.path.join(profile_path, "profile.json")
            with open(profile_json_path, 'w', encoding='utf-8') as f:
                json.dump(profile_data["info"], f, indent=2)

            icon_dest_path = os.path.join(profile_path, "icon.png")
            if profile_data["icon_path"]:
                 if not os.path.exists(profile_data["icon_path"]):
                     if os.path.exists(icon_dest_path):
                         os.remove(icon_dest_path)
                 else:
                     shutil.copy(profile_data["icon_path"], icon_dest_path)
            
            self.load_profiles()
    def create_toolbar(self):
        toolbar = QtWidgets.QToolBar()
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        self.addToolBar(toolbar)
        self.profile_action = toolbar.addAction(f"👤 {self.tr('profiles')}")
        self.profile_action.setToolTip(self.tr("profile_manager_tooltip"))
        self.profile_action.triggered.connect(self.show_profile_manager)
        
        toolbar.addSeparator()
        self.edit_subtitle_action = toolbar.addAction(self.tr("edit_button"))
        self.edit_subtitle_action.setShortcut("F2")
        self.edit_subtitle_action.triggered.connect(self.edit_current_subtitle)
        
        self.save_wav_action = toolbar.addAction(self.tr("export_button"))
        self.save_wav_action.setShortcut("Ctrl+E")
        self.save_wav_action.triggered.connect(self.save_current_wav)
        self.volume_adjust_action = toolbar.addAction(self.tr("volume_toolbar_btn"))
        self.volume_adjust_action.setToolTip(self.tr("volume_adjust_tooltip_no_selection"))
        self.volume_adjust_action.triggered.connect(self.adjust_selected_volume)
        self.delete_mod_action = toolbar.addAction(self.tr("delete_mod_button"))
        self.delete_mod_action.setToolTip("Delete modified audio for selected file")
        self.delete_mod_action.triggered.connect(self.delete_current_mod_audio)
        toolbar.addSeparator()
        
        self.expand_all_action = toolbar.addAction(self.tr("expand_all"))
        self.expand_all_action.triggered.connect(self.expand_all_trees)
        
        self.collapse_all_action = toolbar.addAction(self.tr("collapse_all"))
        self.collapse_all_action.triggered.connect(self.collapse_all_trees)
    def adjust_selected_volume(self):
        """Adjust volume for selected file(s) - works for single or multiple selection"""
        current_lang = self.get_current_language()
        if not current_lang or current_lang not in self.tab_widgets:
            QtWidgets.QMessageBox.information(self, self.tr("no_language_selected"), self.tr("select_language_tab_first"))
            return
            
        widgets = self.tab_widgets[current_lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        file_items = [item for item in items if item.childCount() == 0 and item.data(0, QtCore.Qt.UserRole)]
        
        if not file_items:
            QtWidgets.QMessageBox.information(self, self.tr("no_files_selected"), self.tr("select_files_for_volume"))
            return
        
        if not hasattr(self, 'wav_converter'):
            self.wav_converter = WavToWemConverter(self)
        
        if len(file_items) == 1:
            entry = file_items[0].data(0, QtCore.Qt.UserRole)
            self.adjust_single_file_volume(entry, current_lang)
        else:
            self.adjust_multiple_files_volume(file_items, current_lang)

    def adjust_single_file_volume(self, entry, lang):
        """Adjust volume for single file"""
        msg = QtWidgets.QMessageBox()
        msg.setWindowTitle(self.tr("select_version_title"))
        msg.setText(self.tr("adjust_volume_for").format(filename=entry.get('ShortName', '')))
        original_btn = msg.addButton(self.tr("original"), QtWidgets.QMessageBox.ActionRole)
        
        file_id = entry.get("Id", "")
        
        mod_wem_path = self.get_mod_path(file_id, lang)
        
        mod_btn = None
        if os.path.exists(mod_wem_path):
            mod_btn = msg.addButton(self.tr("mod"), QtWidgets.QMessageBox.ActionRole)
        
        msg.addButton(QtWidgets.QMessageBox.Cancel)
        msg.exec_()
        
        if msg.clickedButton() == original_btn:
            dialog = WemVolumeEditDialog(self, entry, lang, False)
            dialog.exec_()
        elif mod_btn and msg.clickedButton() == mod_btn:
            dialog = WemVolumeEditDialog(self, entry, lang, True)
            dialog.exec_()

    def adjust_multiple_files_volume(self, file_items, lang):
        """Adjust volume for multiple files"""

        entries_and_lang = []
        for item in file_items:
            entry = item.data(0, QtCore.Qt.UserRole)
            if entry:
                entries_and_lang.append((entry, lang))
        
        if not entries_and_lang:
            return
        
        msg = QtWidgets.QMessageBox()
        msg.setWindowTitle(self.tr("select_version_title"))
        msg.setText(self.tr("batch_adjust_volume_for").format(count=len(entries_and_lang)))

        original_btn = msg.addButton(self.tr("original"), QtWidgets.QMessageBox.ActionRole)

        has_mod_files = False
        for entry, _ in entries_and_lang:
            file_id = entry.get("Id", "")
            if lang != "SFX":
                mod_wem_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", lang, f"{file_id}.wem")
            else:
                mod_wem_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", f"{file_id}.wem")
            
            if os.path.exists(mod_wem_path):
                has_mod_files = True
                break
        
        mod_btn = None
        if has_mod_files:
            mod_btn = msg.addButton("Mod", QtWidgets.QMessageBox.ActionRole)
        
        msg.addButton(QtWidgets.QMessageBox.Cancel)
        msg.exec_()
        
        if msg.clickedButton() == original_btn:
            dialog = BatchVolumeEditDialog(self, entries_and_lang, False)
            dialog.exec_()
        elif mod_btn and msg.clickedButton() == mod_btn:
            dialog = BatchVolumeEditDialog(self, entries_and_lang, True)
            dialog.exec_()    
    def delete_current_mod_audio(self):
        """Delete mod audio for currently selected file"""
        current_lang = self.get_current_language()
        if not current_lang or current_lang not in self.tab_widgets:
            return
            
        widgets = self.tab_widgets[current_lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        if not items or items[0].childCount() > 0:
            return
            
        item = items[0]
        entry = item.data(0, QtCore.Qt.UserRole)
        if not entry:
            return
            
        self.delete_mod_audio(entry, current_lang)

    def on_item_double_clicked(self, item, column):
        if item.childCount() > 0: 
            return
            
        if column == 2:  
            self.edit_current_subtitle()
        else:
            self.play_current()
    def get_backup_path(self, file_id, lang):
        backup_root = os.path.join(self.base_path, ".backups", "audio")
        
        if lang != "SFX":
            backup_dir = os.path.join(backup_root, lang)
        else:
            backup_dir = os.path.join(backup_root, "SFX")
        
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"{file_id}.wem")
        
        DEBUG.log(f"Backup path for {file_id} ({lang}): {backup_path}")
        return backup_path

    def create_backup_if_needed(self, file_id, lang):
        if lang != "SFX":
            mod_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", lang, f"{file_id}.wem")
        else:
            mod_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", f"{file_id}.wem")
        
        backup_path = self.get_backup_path(file_id, lang)
        
        if os.path.exists(mod_path) and not os.path.exists(backup_path):
            shutil.copy2(mod_path, backup_path)
            DEBUG.log(f"Created backup: {backup_path}")
            return True
        
        DEBUG.log(f"Backup not created: mod_exists={os.path.exists(mod_path)}, backup_exists={os.path.exists(backup_path)}")
        return False

    def restore_from_backup(self, file_id, lang):
        backup_path = self.get_backup_path(file_id, lang)
        
        if not os.path.exists(backup_path):
            return False, "No backup found"
        
        try:
            backup_wem_size = os.path.getsize(backup_path)
        except Exception as e:
            return False, f"Could not read backup file: {e}"

        if lang != "SFX":
            mod_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", lang, f"{file_id}.wem")
        else:
            mod_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", f"{file_id}.wem")
        
        try:
            os.makedirs(os.path.dirname(mod_path), exist_ok=True)
            shutil.copy2(backup_path, mod_path)
            DEBUG.log(f"Restored WEM: {mod_path} (Size: {backup_wem_size})")
        except Exception as e:
            return False, str(e)
            
        try:
            source_id = int(file_id)
            bnk_updated_count = 0
            
            bnk_files_info = self.find_relevant_bnk_files()

            for bnk_path, bnk_type in bnk_files_info:
    
                if bnk_type == 'sfx':
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems", "SFX"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                else:
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.base_path, "Wems"))
                    mod_bnk_path = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                
                if not os.path.exists(mod_bnk_path):
                    os.makedirs(os.path.dirname(mod_bnk_path), exist_ok=True)
                    shutil.copy2(bnk_path, mod_bnk_path)
                    DEBUG.log(f"Created new mod BNK for restoration: {os.path.basename(mod_bnk_path)}")

                if os.path.exists(mod_bnk_path):
                    mod_editor = BNKEditor(mod_bnk_path)

                    if mod_editor.modify_sound(source_id, new_size=backup_wem_size):
                        mod_editor.save_file()
                        self.invalidate_bnk_cache(source_id)
                        bnk_updated_count += 1

            return True, f"Restored WEM and updated {bnk_updated_count} BNK files."
        
        except Exception as e:
            return False, f"WEM restored but BNK update failed: {str(e)}"
    def has_backup(self, file_id, lang):
        backup_path = self.get_backup_path(file_id, lang)
        exists = os.path.exists(backup_path)
        DEBUG.log(f"Checking backup for {file_id} ({lang}): {backup_path} - exists: {exists}")
        return exists
    def get_dark_menu_style(self):
        return """
            QMenu {
                background-color: #3c3f41;
                color: #d4d4d4;
                border: 1px solid #555555;
                padding: 2px; 
            }
            QMenu::item {
                padding: 4px 20px 4px 20px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #007acc;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #555555;
                margin: 4px 0px 4px 0px;
            }
        """
    def show_context_menu(self, lang, pos):
        widgets = self.tab_widgets[lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        if not items:
            return
            
        menu = QtWidgets.QMenu()
        if self.settings.data["theme"] == "dark":
            menu.setStyleSheet(self.get_dark_menu_style())
            
        file_items = [item for item in items if item.childCount() == 0 and item.data(0, QtCore.Qt.UserRole)]
        
        if file_items:
            play_action = menu.addAction(self.tr("play_original"))
            play_action.triggered.connect(self.play_current)
            menu.addSeparator()
        
            entry = items[0].data(0, QtCore.Qt.UserRole)
            mod_wem_path = None 

            if entry:
                file_id = entry.get('Id', '')
  
                mod_wem_path = self.get_mod_path(file_id, lang)
                
                if mod_wem_path and os.path.exists(mod_wem_path):
                    play_mod_action = menu.addAction(self.tr("play_mod"))
                    play_mod_action.triggered.connect(lambda: self.play_current(play_mod=True))
                    
                    delete_mod_action = menu.addAction(f" {self.tr('delete_mod_audio')}")
                    delete_mod_action.triggered.connect(lambda: self.delete_mod_audio(entry, lang))
                    menu.addSeparator()
                    
                if len(items) == 1 and items[0].childCount() == 0:
                    entry = items[0].data(0, QtCore.Qt.UserRole)
                    if entry:
                        file_id = entry.get("Id", "")    
                        menu.addSeparator()
                        quick_load_action = menu.addAction(self.tr("quick_load_audio_title"))
                        quick_load_action.setToolTip(self.tr("quick_load_audio_tooltip"))
                        quick_load_action.triggered.connect(
                            lambda: self.quick_load_custom_audio(entry, lang)
                        )
                        if self.has_backup(file_id, lang):
                            menu.addSeparator()
                            restore_action = menu.addAction(self.tr("restore_from_backup_title"))
                            restore_action.setToolTip(self.tr("restore_from_backup_tooltip"))
                            restore_action.triggered.connect(
                                lambda: self.restore_audio_from_backup(entry, lang)
                            )
                volume_original_action = menu.addAction(self.tr("adjust_original_volume_title"))
                volume_original_action.triggered.connect(lambda: self.adjust_wem_volume(entry, lang, False))    
                trim_original_action = menu.addAction(self.tr("trim_original_audio_title"))
                trim_original_action.triggered.connect(lambda: self.trim_audio(entry, lang, False))
                if os.path.exists(mod_wem_path):             
                    if os.path.exists(mod_wem_path):
                        volume_mod_action = menu.addAction(self.tr("adjust_mod_volume_title"))
                        volume_mod_action.triggered.connect(lambda: self.adjust_wem_volume(entry, lang, True))
                        trim_mod_action = menu.addAction(self.tr("trim_mod_audio_title"))
                        trim_mod_action.triggered.connect(lambda: self.trim_audio(entry, lang, True))
                    menu.addSeparator()

            toggle_fx_action = menu.addAction(self.tr("toggle_ingame_effects_title"))
            toggle_fx_action.triggered.connect(self.toggle_ingame_effects)
            edit_action = menu.addAction(f"✏ {self.tr('edit_subtitle')}")
            edit_action.triggered.connect(self.edit_current_subtitle)

            shortname = entry.get("ShortName", "")
            key = os.path.splitext(shortname)[0]
            if key in self.modified_subtitles:
                revert_action = menu.addAction(f"↩ {self.tr('revert_to_original')}")
                revert_action.triggered.connect(self.revert_subtitle)
            
            menu.addSeparator()
            
            export_action = menu.addAction(self.tr("export_as_wav"))
            export_action.triggered.connect(self.save_current_wav)
            menu.addSeparator()
            marking_menu = menu.addMenu(self.tr("marking_menu_title"))
    
            color_menu = marking_menu.addMenu(self.tr("set_color_menu_title"))
            colors = {
                self.tr("color_green"): QtGui.QColor(200, 255, 200),
                self.tr("color_yellow"): QtGui.QColor(255, 255, 200),
                self.tr("color_red"): QtGui.QColor(255, 200, 200),
                self.tr("color_blue"): QtGui.QColor(200, 200, 255),
                self.tr("color_none"): None
            }
            for color_name, color in colors.items():
                action = color_menu.addAction(color_name)
                action.triggered.connect(lambda checked, c=color: self.set_item_color(items, c))
            
            tag_menu = marking_menu.addMenu(self.tr("set_tag_menu_title"))
            tags = [self.tr("tag_important"), self.tr("tag_check"), self.tr("tag_done"), self.tr("tag_review"), "None"]
            for tag in tags:
                action = tag_menu.addAction(tag)
                action.triggered.connect(lambda checked, t=tag: self.set_item_tag(items, t if t != "None" else ""))
            custom_action = tag_menu.addAction(self.tr("tag_custom"))
            custom_action.triggered.connect(lambda: self.set_custom_tag(items))
            
        menu.exec_(tree.viewport().mapToGlobal(pos))
    def trim_audio(self, entry, lang, is_mod=False):
        dialog = AudioTrimDialog(self, entry, lang, is_mod)
        dialog.exec_()    
    def set_custom_tag(self, items):
        tag, ok = QtWidgets.QInputDialog.getText(self, self.tr("custom_tag_title"), self.tr("custom_tag_prompt"))
        if ok and tag.strip():
            self.set_item_tag(items, tag.strip())
    def set_item_color(self, items, color):
        for item in items:
            if item.childCount() == 0:
                entry = item.data(0, QtCore.Qt.UserRole)
                if entry:
                    shortname = entry.get("ShortName", "")
                    key = os.path.splitext(shortname)[0]
                    
                    if color is None:
                        self.marked_items.pop(key, None)
                    else:
                        if key not in self.marked_items:
                            self.marked_items[key] = {}
                        self.marked_items[key]['color'] = color
                    
                    for col in range(5):
                        item.setBackground(col, color if color else QtGui.QColor(255, 255, 255))
        
        self.settings.save()

    def set_item_tag(self, items, tag):
        for item in items:
            if item.childCount() == 0: 
                entry = item.data(0, QtCore.Qt.UserRole)
                if entry:
                    shortname = entry.get("ShortName", "")
                    key = os.path.splitext(shortname)[0]
                    if tag == "":
                        if key in self.marked_items and 'tag' in self.marked_items[key]:
                            del self.marked_items[key]['tag']
                            if not self.marked_items[key]:
                                del self.marked_items[key]
                    else:
                        if key not in self.marked_items:
                            self.marked_items[key] = {}
                        self.marked_items[key]['tag'] = tag
                    item.setText(4, tag)
        current_lang = self.get_current_language()
        if current_lang:
            self.update_filter_combo(current_lang)
            self.populate_tree(current_lang)
    def restore_audio_from_backup(self, entry, lang):
        file_id = entry.get("Id", "")
        shortname = entry.get("ShortName", "")
        
        if not self.has_backup(file_id, lang):
            QtWidgets.QMessageBox.information(
                self, "No Backup",
                f"No backup found for {shortname}"
            )
            return
        
        reply = QtWidgets.QMessageBox.question(
            self, "Restore from Backup",
            f"Restore previous version of modified audio for:\n{shortname}\n\n"
            f"This will replace the current modified audio with the backup version.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            success, message = self.restore_from_backup(file_id, lang)
            
            if success:
                self.populate_tree(lang)
                self.status_bar.showMessage(f"Restored {shortname} from backup", 3000)
                QtWidgets.QMessageBox.information(
                    self, "Restored",
                    f"Successfully restored {shortname} from backup!"
                )
            else:
                QtWidgets.QMessageBox.warning(
                    self, "Restore Failed",
                    f"Failed to restore {shortname}:\n{message}"
                )
    def quick_load_custom_audio(self, entry, lang, custom_file=None):
        if custom_file:
            audio_file = custom_file
        else:
            audio_file, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, 
                "Select Audio File",
                "",
                "Audio Files (*.wav *.mp3 *.ogg *.flac *.m4a *.aac *.wma *.opus);;All Files (*.*)"
            )
        
        if not audio_file:
            return
        
        if not hasattr(self, 'wav_converter'):
            self.wav_converter = WavToWemConverter(self)
        
        wwise_path = None
        project_path = None
        
        if hasattr(self, 'wwise_path_edit') and hasattr(self, 'converter_project_path_edit'):
            wwise_path = self.wwise_path_edit.text()
            project_path = self.converter_project_path_edit.text()
        
        if not wwise_path or not project_path:
            wwise_path = self.settings.data.get("wav_wwise_path", "")
            project_path = self.settings.data.get("wav_project_path", "")
        
        if not wwise_path or not os.path.exists(wwise_path):
            QtWidgets.QMessageBox.warning(
                self, "Configuration Required",
                "Wwise path not found or invalid.\n\n"
                "Please go to Converter tab and set valid Wwise installation path."
            )
            return
        
        if not project_path:
            QtWidgets.QMessageBox.warning(
                self, "Configuration Required",
                "Project path not set.\n\n"
                "Please go to Converter tab and set project path."
            )
            return
        
        temp_output = tempfile.mkdtemp(prefix="quick_load_")
        
        self.wav_converter.set_paths(wwise_path, project_path, temp_output)
        
        progress = ProgressDialog(self, self.tr("quick_load_audio_title"))
        progress.setWindowFlags(progress.windowFlags() | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
        progress.setWindowFlags(progress.windowFlags() & ~QtCore.Qt.WindowCloseButtonHint)
        progress.show()
        
        thread = threading.Thread(
            target=self._quick_load_audio_thread,
            args=(audio_file, entry, lang, progress, temp_output)
        )
        thread.daemon = True
        thread.start()
    def batch_adjust_volume(self, lang, is_mod=False):
        """Batch adjust volume for multiple files"""
        if not hasattr(self, 'wav_converter'):
            self.wav_converter = WavToWemConverter(self)
        
        widgets = self.tab_widgets[lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        file_items = [item for item in items if item.childCount() == 0]
        
        if len(file_items) < 2:
            QtWidgets.QMessageBox.information(
                self, "Not Enough Files",
                "Please select at least 2 files for batch processing."
            )
            return
        
        entries_and_lang = []
        for item in file_items:
            entry = item.data(0, QtCore.Qt.UserRole)
            if entry:
                entries_and_lang.append((entry, lang))
        
        if not entries_and_lang:
            return
        
        dialog = BatchVolumeEditDialog(self, entries_and_lang, is_mod)
        dialog.exec_()    
    def adjust_wem_volume(self, entry, lang, is_mod=False):
        if not hasattr(self, 'wav_converter'):
            self.wav_converter = WavToWemConverter(self)
            
            if hasattr(self, 'wwise_path_edit') and hasattr(self, 'converter_project_path_edit'):
                wwise_path = self.wwise_path_edit.text()
                project_path = self.converter_project_path_edit.text()
                
                if wwise_path and project_path:
                    self.wav_converter.set_paths(wwise_path, project_path, tempfile.gettempdir())
        
        dialog = WemVolumeEditDialog(self, entry, lang, is_mod)
        dialog.exec_()
    def _quick_load_audio_thread(self, audio_file, entry, lang, progress, temp_output):
        try:
            file_id = entry.get("Id", "")
            shortname = entry.get("ShortName", "")
            original_filename = os.path.splitext(shortname)[0]
            
            audio_ext = os.path.splitext(audio_file)[1].lower()
            if audio_ext != '.wav':
                QtCore.QMetaObject.invokeMethod(
                    progress, "set_progress",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(int, 20),
                    QtCore.Q_ARG(str, "Converting to WAV...")
                )
                
                audio_converter = AudioToWavConverter()
                if not audio_converter.is_available():
                    raise Exception("FFmpeg not found. Please install FFmpeg for non-WAV file support.")
                
                temp_wav = os.path.join(temp_output, f"{original_filename}.wav")
                success, result = audio_converter.convert_to_wav(audio_file, temp_wav)
                
                if not success:
                    raise Exception(f"Audio conversion failed: {result}")
                    
                wav_file = temp_wav
                needs_cleanup = True
            else:
                wav_file = os.path.join(temp_output, f"{original_filename}.wav")
                shutil.copy2(audio_file, wav_file)
                needs_cleanup = True
            
            original_wem = os.path.join(self.wem_root, lang, f"{file_id}.wem")
            if not os.path.exists(original_wem):
                raise Exception(f"Original WEM not found: {original_wem}")
                
            target_size = os.path.getsize(original_wem)
            
            QtCore.QMetaObject.invokeMethod(
                progress, "set_progress",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(int, 50),
                QtCore.Q_ARG(str, "Converting to WEM...")
            )
            
            file_pair = {
                "wav_file": wav_file,
                "target_wem": original_wem,
                "wav_name": f"{original_filename}.wav",
                "target_name": f"{original_filename}.wem",
                "target_size": target_size,
                "language": lang,
                "file_id": file_id
            }
            
            quick_mode = self.settings.data.get("quick_load_mode", "strict")
            self.wav_converter.set_adaptive_mode(quick_mode == "adaptive")
            
            if not self.wav_converter.wwise_path:
                raise Exception("Wwise converter not properly configured")
            
            result = self.wav_converter.convert_single_file_main(file_pair, 1, 1)
            
            if not result.get('success'):
                raise Exception(result.get('error', 'Conversion failed'))
            
            QtCore.QMetaObject.invokeMethod(
                progress, "set_progress",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(int, 80),
                QtCore.Q_ARG(str, "Deploying to MOD_P...")
            )
            
            source_wem = result['output_path']
            
            if lang != "SFX":
                target_dir = os.path.join(
                    self.mod_p_path, "OPP", "Content", "WwiseAudio", 
                    "Windows", "Media", lang
                )
            else:
                target_dir = os.path.join(
                    self.mod_p_path, "OPP", "Content", "WwiseAudio", 
                    "Windows", "Media"
                )
            
            os.makedirs(target_dir, exist_ok=True)
            target_path = os.path.join(target_dir, f"{file_id}.wem")
            
            if os.path.exists(target_path):
                backup_path = self.get_backup_path(file_id, lang)

                if os.path.exists(backup_path):
                    os.remove(backup_path)
                    DEBUG.log(f"Removed old backup: {backup_path}")
                
                shutil.copy2(source_wem, backup_path)
                DEBUG.log(f"Created new backup from loaded audio: {backup_path}")
            
            shutil.copy2(source_wem, target_path)
            
            QtCore.QMetaObject.invokeMethod(
                progress, "set_progress",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(int, 100),
                QtCore.Q_ARG(str, "Complete!")
            )
            
            if needs_cleanup and os.path.exists(wav_file):
                try:
                    os.remove(wav_file)
                except:
                    pass
                    
            if os.path.exists(source_wem) and source_wem != target_path:
                try:
                    os.remove(source_wem)
                except:
                    pass
                    
            if temp_output and os.path.exists(temp_output):
                try:
                    shutil.rmtree(temp_output)
                except:
                    pass
            
            QtCore.QMetaObject.invokeMethod(
                progress, "close",
                QtCore.Qt.QueuedConnection
            )
            
            QtCore.QMetaObject.invokeMethod(
                self, "_quick_load_complete",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, lang),
                QtCore.Q_ARG(str, shortname)
            )
            
        except Exception as e:
  
            QtCore.QMetaObject.invokeMethod(
                progress, "close",
                QtCore.Qt.QueuedConnection
            )
            
            QtCore.QMetaObject.invokeMethod(
                self, "_quick_load_error",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, str(e))
            )
    @QtCore.pyqtSlot(str, str)
    def _quick_load_complete(self, lang, shortname):
        self.populate_tree(lang)
        self.status_bar.showMessage(f"Successfully imported custom audio for {shortname}", 3000)
        QtWidgets.QMessageBox.information(
            self, "Success",
            f"Custom audio imported successfully!\n\nFile: {shortname}\n\nThe mod audio is now in MOD_P"
        )

    @QtCore.pyqtSlot(str)
    def _quick_load_error(self, error):
        QtWidgets.QMessageBox.critical(
            self, "Import Error",
            f"Failed to import custom audio:\n\n{error}"
        )
    def batch_adjust_volume(self):
        """Batch adjust volume for multiple selected files"""
        current_lang = self.get_current_language()
        if not current_lang or current_lang not in self.tab_widgets:
            return
            
        widgets = self.tab_widgets[current_lang]
        tree = widgets["tree"]
        items = tree.selectedItems()
        
        file_items = [item for item in items if item.childCount() == 0]
        
        if not file_items:
            QtWidgets.QMessageBox.information(
                self, "No Files Selected",
                "Please select audio files to adjust volume."
            )
            return
        
        msg = QtWidgets.QMessageBox()
        msg.setWindowTitle(self.tr("select_version_title"))
        msg.setText("Which version would you like to adjust?")
        
        original_btn = msg.addButton("Original", QtWidgets.QMessageBox.ActionRole)
        mod_btn = msg.addButton("Mod", QtWidgets.QMessageBox.ActionRole)
        msg.addButton(QtWidgets.QMessageBox.Cancel)
        
        msg.exec_()
        
        is_mod = False
        if msg.clickedButton() == mod_btn:
            is_mod = True
        elif msg.clickedButton() != original_btn:
            return
    def _batch_export_wav_thread(self, file_items, lang, export_mod, directory, progress):
        errors = []
        successful_count = 0
        overwrite_all = False

        for i, item in enumerate(file_items):
            entry = item.data(0, QtCore.Qt.UserRole)
            if not entry:
                continue
                
            id_ = entry.get("Id", "")
            shortname = entry.get("ShortName", "")
            
            QtCore.QMetaObject.invokeMethod(progress, "set_progress", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(int, int((i / len(file_items)) * 100)),
                                            QtCore.Q_ARG(str, f"Converting {shortname}..."))
            
            wem_path = None
            if export_mod:
                mod_wem_path = self.get_mod_path(id_, lang)
                if mod_wem_path and os.path.exists(mod_wem_path):
                    wem_path = mod_wem_path
                else:
                    wem_path = self.get_original_path(id_, lang)
            else:
                wem_path = self.get_original_path(id_, lang)
            
            wav_path = os.path.join(directory, shortname)
            
            if os.path.exists(wav_path) and not overwrite_all:
     
                result = QtCore.QMetaObject.invokeMethod(self, "_ask_overwrite", QtCore.Qt.BlockingQueuedConnection,
                                                         QtCore.Q_ARG(str, shortname))
                
                if result == "No":
                    errors.append(f"{shortname}: Skipped by user")
                    continue
                elif result == "No to All":
                    errors.append(f"{shortname}: Skipped by user (cancelled all)")
                    break 
                elif result == "Yes to All":
                    overwrite_all = True
            
            if wem_path and os.path.exists(wem_path):
                ok, err = self.wem_to_wav_vgmstream(wem_path, wav_path)
                if not ok:
                    errors.append(f"{shortname}: {err}")
                    QtCore.QMetaObject.invokeMethod(progress, "append_details", QtCore.Qt.QueuedConnection,
                                                    QtCore.Q_ARG(str, f"Failed: {shortname}"))
                else:
                    successful_count += 1
            else:
                errors.append(f"{shortname}: Source WEM file not found")

        QtCore.QMetaObject.invokeMethod(self, "_on_batch_export_finished", QtCore.Qt.QueuedConnection,
                                        QtCore.Q_ARG(object, progress),
                                        QtCore.Q_ARG(int, successful_count),
                                        QtCore.Q_ARG(list, errors))
    @QtCore.pyqtSlot(str, result=str)
    def _ask_overwrite(self, shortname):
        reply_box = QtWidgets.QMessageBox(self)
        reply_box.setWindowTitle("File Exists")
        reply_box.setText(f"The file '{shortname}' already exists in the destination folder.")
        reply_box.setInformativeText("Do you want to overwrite it?")
        yes_btn = reply_box.addButton("Yes", QtWidgets.QMessageBox.YesRole)
        no_btn = reply_box.addButton("No", QtWidgets.QMessageBox.NoRole)
        yes_all_btn = reply_box.addButton("Yes to All", QtWidgets.QMessageBox.YesRole)
        no_all_btn = reply_box.addButton("No to All", QtWidgets.QMessageBox.NoRole)
        
        self.show_dialog(reply_box)
        clicked = reply_box.clickedButton()

        if clicked == yes_btn: return "Yes"
        if clicked == no_btn: return "No"
        if clicked == yes_all_btn: return "Yes to All"
        if clicked == no_all_btn: return "No to All"
        return "No"
    @QtCore.pyqtSlot(result=bool)
    def _ask_convert_old_mod_structure(self):
        """Asks the user if they want to convert old mod structure to new Media/ format."""
        title = self.translations.get(self.current_lang, {}).get(
            "outdated_mod_structure_title", "Outdated Mod Structure"
        )
        
        msg = self.translations.get(self.current_lang, {}).get(
            "outdated_mod_structure_msg", 
            "The mod you are importing uses the old file structure (pre-update).\n\n"
            "The game now requires audio files to be in a 'Media' subfolder.\n"
            "Do you want to automatically reorganize the files to the new format?"
        )

        reply = QtWidgets.QMessageBox.question(
            self,
            title,
            msg,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        return reply == QtWidgets.QMessageBox.Yes
    @QtCore.pyqtSlot(object, int, list)
    def _on_batch_export_finished(self, progress, successful_count, errors):
        progress.close()
        
        self.show_message_box(
            QtWidgets.QMessageBox.Information,
            self.tr("export_complete"),
            self.tr("export_results").format(
                successful=successful_count,
                errors=len(errors)
            ),
            informative_text="\n".join(errors) if errors else ""
        )
        
        if successful_count > 0:
            self.status_bar.showMessage(f"Exported {successful_count} files successfully", 3000)
    def batch_export_wav(self, items, lang):
        file_items = [item for item in items if item.childCount() == 0]
        
        if not file_items:
            return
            
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle(self.tr("batch_export"))
        msg.setText(self.tr("which_version_export") + f"\n\n({len(file_items)} files selected)")
        
        original_btn = msg.addButton(self.tr("original"), QtWidgets.QMessageBox.ActionRole)
        mod_btn = msg.addButton(self.tr("mod"), QtWidgets.QMessageBox.ActionRole)
        msg.addButton(QtWidgets.QMessageBox.Cancel)
        
        has_any_mod = False
        for item in file_items:
            entry = item.data(0, QtCore.Qt.UserRole)
            if entry:
                mod_path = self.get_mod_path(entry.get("Id", ""), lang)
                if mod_path and os.path.exists(mod_path):
                    has_any_mod = True
                    break
        
        if not has_any_mod:
            mod_btn.setEnabled(False)
            mod_btn.setToolTip("No modified audio files found in selection.")
        
        self.show_dialog(msg)
        
        clicked_button = msg.clickedButton()
        export_mod = False
        
        if clicked_button == original_btn:
            export_mod = False
        elif clicked_button == mod_btn:
            export_mod = True
        else:
            return
            
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, self.tr("select_output_directory"))
        if not directory:
            return
            
        progress = ProgressDialog(self, self.tr("exporting_files").format(count=len(file_items)))
        progress.show()
        progress.raise_()
        progress.activateWindow()

        thread = threading.Thread(target=self._batch_export_wav_thread, args=(file_items, lang, export_mod, directory, progress))
        thread.daemon = True
        thread.start()

    def on_global_search(self, text):
        self.search_timer.start()
    def perform_delayed_search(self):
        current_lang = self.get_current_language()
        if current_lang and current_lang in self.tab_widgets:
            self.populate_tree(current_lang)    

    def on_tab_changed(self, index):

        if index >= len(self.tab_widgets):
            return
            
        lang = self.get_current_language()
        if lang and lang in self.tab_widgets: 
            self.update_filter_combo(lang)
            if lang not in self.populated_tabs:
                self.populate_tree(lang)
                self.populated_tabs.add(lang)

    def expand_all_trees(self):
        current_lang = self.get_current_language()
        if current_lang and current_lang in self.tab_widgets:
            self.tab_widgets[current_lang]["tree"].expandAll()

    def collapse_all_trees(self):
        current_lang = self.get_current_language()
        if current_lang and current_lang in self.tab_widgets:
            self.tab_widgets[current_lang]["tree"].collapseAll()

    def apply_settings(self):

        theme = self.settings.data["theme"]
        if theme == "dark":
            self.setStyleSheet(self.get_dark_theme())
        else:
            self.setStyleSheet(self.get_light_theme())

    def get_dark_theme(self):
        return """
        QMainWindow, QDialog, QWidget {
            background-color: #2b2b2b;
            color: #d4d4d4;
            border: none;
        }

        QMenuBar {
            background-color: #3c3f41;
            border-bottom: 1px solid #4a4d4f;
        }
        QMenuBar::item:selected {
            background-color: #007acc;
            color: #ffffff;
        }
        QMenu {
            background-color: #2b2b2b;
            border: 1px solid #4a4d4f;
        }
        QMenu::item:selected {
            background-color: #007acc;
            color: #ffffff;
        }

        QToolBar {
            background-color: #3c3f41;
            spacing: 3px;
            padding: 3px;
        }
        QToolButton {
            background-color: transparent;
            padding: 4px;
            border-radius: 3px;
        }
        QToolButton:hover {
            background-color: #4a4d4f;
        }
        QToolButton:pressed, QToolButton:checked {
            background-color: #007acc;
        }

        QTabWidget::pane {
            border-top: 1px solid #4a4d4f;
        }
        QTabBar {
            qproperty-drawBase: 0;
            border: 0;
        }
        QTabBar::tab {
            background-color: #3c3f41;
            color: #d4d4d4;
            padding: 6px 12px;
            margin-right: 1px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }
        QTabBar::tab:hover {
            background-color: #4a4d4f;
        }
        QTabBar::tab:selected {
            background-color: #2b2b2b; 
            border-bottom: 2px solid #007acc;
        }

        QTreeWidget, QTableWidget {
            background-color: #2b2b2b;
            alternate-background-color: #3c3f41; 
            border: 1px solid #4a4d4f;
            selection-background-color: #007acc; 
            selection-color: #ffffff; 
            gridline-color: #4a4d4f; 
        }
        QTreeWidget::item:hover, QTableWidget::item:hover {
            background-color: #45494a;
        }
        QHeaderView::section {
            background-color: #3c3f41;
            color: #d4d4d4;
            border: none;
            border-right: 1px solid #4a4d4f;
            border-bottom: 1px solid #4a4d4f;
            padding: 4px;
        }

        QPushButton {
            background-color: #4a4d4f;
            color: #d4d4d4;
            border: 1px solid #5a5d5f;
            padding: 5px 12px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #5a5d5f;
            border-color: #6a6d6f;
        }
        QPushButton:pressed {
            background-color: #3c3f41;
        }
        QPushButton[primary="true"], QPushButton:default {
            background-color: #007acc;
            color: white;
            border: 1px solid #007acc;
        }
        QPushButton[primary="true"]:hover {
            background-color: #1185cf;
        }
        QLabel {
            background-color: transparent;
        }
        QLineEdit, QTextEdit, QComboBox, QSpinBox {
            background-color: #3c3f41;
            border: 1px solid #4a4d4f;
            padding: 4px;
            border-radius: 4px;
        }
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {
            border: 1px solid #007acc;
        }
        QComboBox::drop-down {
            border: none;
        }
        QComboBox::down-arrow {
            image: url(./path/to/your/dark-arrow.png); 
        }

        QGroupBox {
            border: 1px solid #4a4d4f;
            margin-top: 8px;
            padding: 8px;
            border-radius: 4px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding-left: 5px;
            padding-right: 5px;
        }

        QProgressBar {
            background-color: #3c3f41;
            border: 1px solid #4a4d4f;
            border-radius: 4px;
            text-align: center;
            color: #d4d4d4;
        }
        QProgressBar::chunk {
            background-color: #007acc;
            border-radius: 4px;
        }
        QStatusBar {
            background-color: #007acc;
            color: white;
        }
        QSplitter::handle {
            background: #3c3f41;
        }
        QScrollBar:vertical {
            border: none;
            background: #2b2b2b;
            width: 10px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:vertical {
            background: #4a4d4f;
            min-height: 20px;
            border-radius: 5px;
        }
        QScrollBar:horizontal {
            border: none;
            background: #2b2b2b;
            height: 10px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:horizontal {
            background: #4a4d4f;
            min-width: 20px;
            border-radius: 5px;
        }
        """
    def get_light_theme(self):
        return """
        QMainWindow, QWidget {
            background-color: #f3f3f3;
            color: #1e1e1e;
        }
        
        QMenuBar {
            background-color: #e7e7e7;
            border-bottom: 1px solid #cccccc;
        }
        
        QMenuBar::item:selected {
            background-color: #bee6fd;
        }
        
        QMenu {
            background-color: #f3f3f3;
            border: 1px solid #cccccc;
        }
        
        QMenu::item:selected {
            background-color: #bee6fd;
        }
        
        QToolBar {
            background-color: #e7e7e7;
            border: none;
            spacing: 5px;
            padding: 5px;
        }
        
        QToolButton {
            background-color: transparent;
            border: none;
            padding: 5px;
            border-radius: 3px;
        }
        
        QToolButton:hover {
            background-color: #dadada;
        }
        
        QTabWidget::pane {
            border: 1px solid #cccccc;
            background-color: #ffffff;
        }
        
        QTabBar::tab {
            background-color: #e7e7e7;
            color: #1e1e1e;
            padding: 8px 16px;
            margin-right: 2px;
        }
        
        QTabBar::tab:selected {
            background-color: #ffffff;
            border-bottom: 2px solid #0078d4;
        }
        
        QTreeWidget {
            background-color: #ffffff;
            alternate-background-color: #f9f9f9;
            border: 1px solid #cccccc;
            selection-background-color: #bee6fd;
        }
        
        QTreeWidget::item:hover {
            background-color: #e5f3ff;
        }
        
        QHeaderView::section {
            background-color: #e7e7e7;
            border: none;
            border-right: 1px solid #cccccc;
            padding: 5px;
        }
        
        QPushButton {
            background-color: #0078d4;
            color: white;
            border: none;
            padding: 6px 14px;
            border-radius: 3px;
        }
        
        QPushButton:hover {
            background-color: #106ebe;
        }
        
        QPushButton:pressed {
            background-color: #005a9e;
        }
        
        QPushButton[primary="true"] {
            background-color: #107c10;
        }
        
        QPushButton[primary="true"]:hover {
            background-color: #0e7b0e;
        }
        
        QLineEdit, QTextEdit, QComboBox {
            background-color: #ffffff;
            border: 1px solid #cccccc;
            padding: 5px;
            border-radius: 3px;
        }
        
        QLineEdit:focus, QTextEdit:focus {
            border: 1px solid #0078d4;
        }
        
        QGroupBox {
            border: 1px solid #cccccc;
            margin-top: 10px;
            padding-top: 10px;
            background-color: #ffffff;
        }
        
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
        }
        
        QProgressBar {
            background-color: #e7e7e7;
            border: 1px solid #cccccc;
            border-radius: 3px;
            text-align: center;
        }
        
        QProgressBar::chunk {
            background-color: #0078d4;
            border-radius: 3px;
        }
        
        QStatusBar {
            background-color: #0078d4;
            color: white;
        }
        """

    def compile_mod(self):
        if not os.path.exists(self.repak_path):
            QtWidgets.QMessageBox.warning(self, self.tr("error"), self.tr("repak_not_found"))
            return
        
        self.progress_dialog = ProgressDialog(self, self.tr("compiling_mod"))
        self.progress_dialog.progress.setRange(0, 0)
        self.progress_dialog.details.append(f"[{datetime.now().strftime('%H:%M:%S')}] {self.tr('running_repak')}")

        self.animation_timer = QtCore.QTimer()

        self.animation_texts = [
            self.tr("compiling_step_1"),
            self.tr("compiling_step_2"),
            self.tr("compiling_step_3"),
            self.tr("compiling_step_4"),
            self.tr("compiling_step_5"),
            self.tr("compiling_step_6"),
            self.tr("compiling_step_7"),
        ]

        import random
        random.shuffle(self.animation_texts) 
        self.animation_index = 0

        def update_animation():
            if hasattr(self, 'progress_dialog') and self.progress_dialog.isVisible():

                current_text = self.animation_texts[self.animation_index]
                self.progress_dialog.label.setText(current_text)

                self.progress_dialog.details.append(f"[{datetime.now().strftime('%H:%M:%S')}] {current_text}")

                self.animation_index = (self.animation_index + 1) % len(self.animation_texts)
            else:
                self.animation_timer.stop() 
                
        self.animation_timer.timeout.connect(update_animation)

        self.animation_timer.start(2500) 
        self.progress_dialog.label.setText(self.tr("running_repak"))


        self.progress_dialog.show()

        opp_path = os.path.join(self.mod_p_path, "OPP")
        os.makedirs(opp_path, exist_ok=True)
        watermark_path = os.path.join(opp_path, "CreatedByAudioEditor.txt")
        watermark_content = f"This mod was created using OutlastTrials AudioEditor {current_version}\nCreated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        try:
            with open(watermark_path, 'w', encoding='utf-8') as f:
                f.write(watermark_content)
        except Exception:
            pass
        
        self.compile_thread = CompileModThread(self.repak_path, self.mod_p_path)

        self.compile_thread.finished.connect(self.on_compilation_finished)
        self.compile_thread.start()

    def on_compilation_finished(self, success, output):

        if hasattr(self, 'animation_timer'):
            self.animation_timer.stop()

        watermark_path = os.path.join(self.mod_p_path, "OPP", "CreatedByAudioEditor.txt")
        if os.path.exists(watermark_path):
            try:
                os.remove(watermark_path)
            except Exception:
                pass
                
        if hasattr(self, 'progress_dialog'):
            self.progress_dialog.close()

        if success:
            QtWidgets.QMessageBox.information(
                self, 
                self.tr("success"), 
                self.tr("mod_compiled_successfully")
            )
            DEBUG.log(f"Mod compilation successful:\n{output}")
        else:
            error_msg = QtWidgets.QMessageBox(self)
            error_msg.setIcon(QtWidgets.QMessageBox.Warning)
            error_msg.setWindowTitle(self.tr("error"))
            error_msg.setText(self.tr("compilation_failed"))
            error_msg.setInformativeText("See details for the output from repak.exe.")
            error_msg.setDetailedText(output)
            error_msg.exec_()
            DEBUG.log(f"Mod compilation failed:\n{output}", "ERROR")
    def select_wwise_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select WWISE Folder", 
            self.settings.data.get("last_directory", "")
        )
        
        if folder:
            self.wwise_path_edit.setText(folder)
            self.settings.data["last_directory"] = folder
            self.settings.save()

    def open_target_folder(self):
        voice_dir = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", "English(US)")
        sfx_dir = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media")
        loc_dir = os.path.join(self.mod_p_path, "OPP", "Content", "Localization")
        
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self.tr("select_folder_to_open_title"))
        dialog.setMinimumWidth(400)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        label = QtWidgets.QLabel(self.tr("which_folder_to_open"))
        layout.addWidget(label)
        
        btn_layout = QtWidgets.QVBoxLayout()
        
        if os.path.exists(voice_dir):
            voice_btn = QtWidgets.QPushButton(self.tr("voice_files_folder").format(path=voice_dir))
            voice_btn.clicked.connect(lambda: (os.startfile(voice_dir), dialog.accept()))
            btn_layout.addWidget(voice_btn)
        
        if os.path.exists(sfx_dir) and sfx_dir != voice_dir:
            sfx_btn = QtWidgets.QPushButton(self.tr("sfx_files_folder").format(path=sfx_dir))
            sfx_btn.clicked.connect(lambda: (os.startfile(sfx_dir), dialog.accept()))
            btn_layout.addWidget(sfx_btn)
        
        if os.path.exists(loc_dir):
            loc_btn = QtWidgets.QPushButton(self.tr("subtitles_folder").format(path=loc_dir))
            loc_btn.clicked.connect(lambda: (os.startfile(loc_dir), dialog.accept()))
            btn_layout.addWidget(loc_btn)
        
        layout.addLayout(btn_layout)
        
        cancel_btn = QtWidgets.QPushButton(self.tr("cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        layout.addWidget(cancel_btn)
        
        if not any(os.path.exists(d) for d in [voice_dir, sfx_dir, loc_dir]):
            QtWidgets.QMessageBox.warning(self, self.tr("error"), self.tr("no_target_folders_found"))
            return
        
        dialog.exec_()

    def create_wav_to_wem_tab(self):
        """Create simplified WAV to WEM converter tab with logs"""
        main_tab = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(main_tab)
        main_layout.setSpacing(5)
        
        self.wav_converter_tabs = QtWidgets.QTabWidget()
        
        converter_tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(converter_tab)
        layout.setSpacing(5)
        
        instructions = QtWidgets.QLabel(f"""
        <p><b>{self.tr("wav_to_wem_converter")}:</b> {self.tr("converter_instructions")}</p>
        """)
        instructions.setWordWrap(True)
        instructions.setMaximumHeight(40)
        layout.addWidget(instructions)
        
        top_section = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout(top_section)
        top_layout.setSpacing(10)
        
        mode_group = QtWidgets.QGroupBox(self.tr("conversion_mode"))
        mode_group.setMaximumHeight(120)
        mode_group.setMinimumWidth(240)
        mode_layout = QtWidgets.QVBoxLayout(mode_group)
        mode_layout.setSpacing(2)
        
        self.strict_mode_radio = QtWidgets.QRadioButton(self.tr("strict_mode"))
        self.strict_mode_radio.setChecked(True)
        self.strict_mode_radio.setToolTip(
            "Standard conversion mode. If the file is too large, an error will be thrown.\n"
            "Use this mode when you want full control over your audio files."
        )
        
        self.adaptive_mode_radio = QtWidgets.QRadioButton(self.tr("adaptive_mode"))
        self.adaptive_mode_radio.setToolTip(
            "Automatically resamples audio to lower sample rates if the file is too large.\n"
            "The system will find the optimal sample rate to match the target file size.\n"
            "Useful for batch processing when exact audio quality is less critical."
        )
        
        strict_desc = QtWidgets.QLabel(f"<small>{self.tr('strict_mode_desc')}</small>")
        strict_desc.setStyleSheet("padding-left: 20px; color: #666;")
        
        adaptive_desc = QtWidgets.QLabel(f"<small>{self.tr('adaptive_mode_desc')}</small>")
        adaptive_desc.setStyleSheet("padding-left: 20px; color: #666;")
        
        mode_layout.addWidget(self.strict_mode_radio)
        mode_layout.addWidget(strict_desc)
        mode_layout.addWidget(self.adaptive_mode_radio)
        mode_layout.addWidget(adaptive_desc)
        mode_layout.addStretch()
        
        top_layout.addWidget(mode_group)
        
        paths_group = QtWidgets.QGroupBox(self.tr("path_configuration"))
        paths_group.setMaximumHeight(120)
        paths_layout = QtWidgets.QFormLayout(paths_group)
        paths_layout.setSpacing(5)
        paths_layout.setContentsMargins(5, 5, 5, 5)
        
        wwise_widget = QtWidgets.QWidget()
        wwise_layout = QtWidgets.QHBoxLayout(wwise_widget)
        wwise_layout.setContentsMargins(0, 0, 0, 0)
        
        self.wwise_path_edit = QtWidgets.QLineEdit()
        self.wwise_path_edit.setPlaceholderText(self.tr("wwise_path_placeholder"))
        self.wwise_path_edit.setText(self.settings.data.get("wav_wwise_path", ""))
        self.wwise_path_edit.editingFinished.connect(lambda: self.settings.data.update({"wav_wwise_path": self.wwise_path_edit.text()}))
        wwise_browse_btn = QtWidgets.QPushButton("...")
        wwise_browse_btn.setMaximumWidth(30)
        wwise_browse_btn.clicked.connect(self.browse_wwise_path)
        
        wwise_layout.addWidget(self.wwise_path_edit)
        wwise_layout.addWidget(wwise_browse_btn)
        paths_layout.addRow(f"{self.tr('wwise_path')}", wwise_widget)
        
        project_widget = QtWidgets.QWidget()
        project_layout = QtWidgets.QHBoxLayout(project_widget)
        project_layout.setContentsMargins(0, 0, 0, 0)
        
        self.converter_project_path_edit = QtWidgets.QLineEdit()
        self.converter_project_path_edit.setPlaceholderText(self.tr("project_path_placeholder"))
        self.converter_project_path_edit.setText(self.settings.data.get("wav_project_path", ""))
        self.converter_project_path_edit.editingFinished.connect(lambda: self.settings.data.update({"wav_project_path": self.converter_project_path_edit.text()}))
        project_browse_btn = QtWidgets.QPushButton("...")
        project_browse_btn.setMaximumWidth(30)
        project_browse_btn.clicked.connect(self.browse_converter_project_path)
        
        project_layout.addWidget(self.converter_project_path_edit)
        project_layout.addWidget(project_browse_btn)
        paths_layout.addRow(f"{self.tr('project_path')}", project_widget)
        
        wav_widget = QtWidgets.QWidget()
        wav_layout = QtWidgets.QHBoxLayout(wav_widget)
        wav_layout.setContentsMargins(0, 0, 0, 0)
        
        self.wav_folder_edit = QtWidgets.QLineEdit()
        self.wav_folder_edit.setPlaceholderText(self.tr("wav_folder_placeholder"))
        self.wav_folder_edit.setText(self.settings.data.get("wav_folder_path", ""))
        self.wav_folder_edit.editingFinished.connect(lambda: self.settings.data.update({"wav_folder_path": self.wav_folder_edit.text()})) 
        wav_browse_btn = QtWidgets.QPushButton("...")
        wav_browse_btn.setMaximumWidth(30)
        wav_browse_btn.clicked.connect(self.browse_wav_folder)
        
        wav_layout.addWidget(self.wav_folder_edit)
        wav_layout.addWidget(wav_browse_btn)
        paths_layout.addRow(f"{self.tr('wav_path')}", wav_widget)
        
        top_layout.addWidget(paths_group)
        
        layout.addWidget(top_section)
        
        files_group = QtWidgets.QGroupBox(self.tr("files_for_conversion"))
        files_layout = QtWidgets.QVBoxLayout(files_group)
        files_layout.setSpacing(5)
        
        controls_widget = QtWidgets.QWidget()
        controls_widget.setMaximumHeight(35)
        controls_layout = QtWidgets.QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        
        add_all_wav_btn = QtWidgets.QPushButton(self.tr("add_all_wav"))
        add_all_wav_btn.clicked.connect(self.add_all_audio_files_auto)
        
        clear_files_btn = QtWidgets.QPushButton(self.tr("clear"))
        clear_files_btn.clicked.connect(self.clear_conversion_files)
        
        self.convert_btn = QtWidgets.QPushButton(self.tr("convert"))
        self.convert_btn.setMaximumWidth(150)
        self.convert_btn.setMaximumHeight(30)
        self.convert_btn.setStyleSheet("""
            QPushButton { 
                background-color: #4CAF50; 
                color: white; 
                font-weight: bold; 
                padding: 5px 15px; 
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        
        self.convert_btn.clicked.connect(self.toggle_conversion)
        
        self.is_converting = False
        self.conversion_thread = None
        
        self.files_count_label = QtWidgets.QLabel(self.tr("files_ready_count").format(count=0))
        self.files_count_label.setStyleSheet("font-weight: bold; color: #666;")
        
        controls_layout.addWidget(add_all_wav_btn)
        add_single_file_btn = QtWidgets.QPushButton(self.tr("add_file_btn"))
        add_single_file_btn.clicked.connect(self.add_single_audio_file)
        
        controls_layout.addWidget(add_all_wav_btn)
        controls_layout.addWidget(add_single_file_btn) 
        controls_layout.addWidget(clear_files_btn)
        controls_layout.addWidget(clear_files_btn)
        controls_layout.addWidget(self.convert_btn)
        controls_layout.addStretch()
        controls_layout.addWidget(self.files_count_label)
        
        files_layout.addWidget(controls_widget)
        
        self.conversion_files_table = QtWidgets.QTableWidget()
        self.conversion_files_table.setColumnCount(5)
        self.conversion_files_table.setHorizontalHeaderLabels([
            self.tr("wav_file"), self.tr("target_wem"), self.tr("language"), 
            self.tr("target_size"), self.tr("status")
        ])
        self.conversion_files_table.setAcceptDrops(True)
        self.conversion_files_table.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        self.conversion_files_table.viewport().setAcceptDrops(True)

        self.conversion_files_table.dragEnterEvent = self.table_dragEnterEvent
        self.conversion_files_table.dragMoveEvent = self.table_dragMoveEvent
        self.conversion_files_table.dropEvent = self.table_dropEvent
        self.conversion_files_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.conversion_files_table.customContextMenuRequested.connect(self.show_conversion_context_menu)
        
        header = self.conversion_files_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch) 
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        
        self.conversion_files_table.setAlternatingRowColors(True)
        self.conversion_files_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        files_layout.addWidget(self.conversion_files_table, 1)
        
        layout.addWidget(files_group, 1)
        
        bottom_widget = QtWidgets.QWidget()
        bottom_widget.setMaximumHeight(60)
        bottom_layout = QtWidgets.QVBoxLayout(bottom_widget)
        bottom_layout.setSpacing(2)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        
        progress_widget = QtWidgets.QWidget()
        progress_layout = QtWidgets.QHBoxLayout(progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(10)
        
        self.conversion_progress = QtWidgets.QProgressBar()
        self.conversion_progress.setMaximumHeight(15)
        
        self.conversion_status = QtWidgets.QLabel(self.tr("ready"))
        self.conversion_status.setStyleSheet("color: #666; font-size: 11px;")
        self.conversion_status.setMinimumWidth(200)
        
        progress_layout.addWidget(self.conversion_progress)
        progress_layout.addWidget(self.conversion_status)
        
        bottom_layout.addWidget(progress_widget)
        
        layout.addWidget(bottom_widget)
        
        self.wav_converter_tabs.addTab(converter_tab, self.tr("convert"))
        
        self.create_conversion_logs_tab()
        
        main_layout.addWidget(self.wav_converter_tabs)
        
        self.wav_converter = WavToWemConverter(self)
        self.wav_converter.progress_updated.connect(self.conversion_progress.setValue)
        self.wav_converter.status_updated.connect(self.update_conversion_status)
        self.wav_converter.conversion_finished.connect(self.on_conversion_finished)
        
        self.converter_tabs.addTab(main_tab, self.tr("wav_to_wem_converter"))
    def table_dragEnterEvent(self, event):
        """Handle drag enter event for conversion table"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def table_dragMoveEvent(self, event):
        """Handle drag move event for conversion table"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def add_single_audio_file(self):
        if hasattr(self, 'add_single_thread') and self.add_single_thread.isRunning():
            QtWidgets.QMessageBox.information(self, "In Progress", "Already processing a file. Please wait.")
            return
            
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Audio File",
            self.settings.data.get("last_audio_dir", ""),
            "Audio Files (*.wav *.mp3 *.ogg *.flac *.m4a *.aac *.wma *.opus *.webm);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        self.settings.data["last_audio_dir"] = os.path.dirname(file_path)
        self.settings.save()
        
        progress = ProgressDialog(self, self.tr("add_single_file_title"))
        progress.setWindowFlags(progress.windowFlags() | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
        progress.setWindowFlags(progress.windowFlags() & ~QtCore.Qt.WindowCloseButtonHint)
        progress.show()
        
        self.add_single_thread = AddSingleFileThread(self, file_path)
        self.add_single_thread.progress_updated.connect(progress.set_progress)
        self.add_single_thread.details_updated.connect(progress.append_details)
        self.add_single_thread.finished.connect(lambda success: self.on_add_single_finished(progress, success, file_path))
        self.add_single_thread.error_occurred.connect(lambda e: self.on_add_single_error(progress, e))
        
        self.add_single_thread.start()
    def on_add_single_finished(self, progress, success, file_path):
        progress.close()
        
        self.update_conversion_files_table()
        
        filename = os.path.basename(file_path)
        
        if success:
            self.status_bar.showMessage(f"Added: {filename}", 3000)
            self.append_conversion_log(f"✓ Added {filename}")
        else:
            self.status_bar.showMessage(f"File not added: {filename}", 3000)
            self.append_conversion_log(f"✗ Not added: {filename}")

    def on_add_single_error(self, progress, error):
        progress.close()
        
        QtWidgets.QMessageBox.warning(
            self, "Error",
            f"Error adding file:\n\n{error}"
        )
        
        self.append_conversion_log(f"✗ Error: {error}")
    def find_matching_wem_for_audio(self, audio_path, auto_mode=False, replace_all=False, skip_all=False):
        """Find matching WEM for audio file and add to conversion list"""
        audio_name = os.path.splitext(os.path.basename(audio_path))[0]
        audio_ext = os.path.splitext(audio_path)[1].lower()
        
        selected_language = self.settings.data.get("wem_process_language", "english")
        DEBUG.log(f"Using language from settings: {selected_language}")
        
        if selected_language == "english":
            target_dir_voice = "English(US)"
            voice_lang_filter = ["English(US)"]
        elif selected_language == "french":
            target_dir_voice = "Francais"
            voice_lang_filter = ["French(France)", "Francais"]
        else:
            target_dir_voice = "English(US)"
            voice_lang_filter = ["English(US)"]
        
        existing_file_index = None

        file_pairs_copy = list(self.wav_converter.file_pairs)
        for i, pair in enumerate(file_pairs_copy):
            if pair.get('audio_file') == audio_path:
                existing_file_index = i
                break
        
        if existing_file_index is not None:
            if skip_all:
                self.append_conversion_log(f"✗ Skipped {os.path.basename(audio_path)}: Already in list")
                return False
            
            if replace_all:
                self.append_conversion_log(f"ℹ {os.path.basename(audio_path)}: Already in list (no changes)")
                return False
            
            response = QtCore.QMetaObject.invokeMethod(
                self, "_ask_for_update", QtCore.Qt.BlockingQueuedConnection,
                QtCore.Q_ARG(str, os.path.basename(audio_path))
            )

            if response == "Skip":
                self.append_conversion_log(f"✗ Skipped {os.path.basename(audio_path)}: Already in list")
                return False

        self._build_wem_index()
        
        found_entry = None
        file_id = None
        
        if audio_name.isdigit():
            file_id = audio_name

            if file_id in self.wem_index:

                for entry in self.all_files:
                    if entry.get("Id", "") == file_id:
                        found_entry = entry
                        break
                
                if not found_entry and file_id in self.wem_index:

                    available_langs = list(self.wem_index[file_id].keys())
                    language = available_langs[0] if available_langs else "SFX"
                    
                    found_entry = {
                        "Id": file_id,
                        "Language": language,
                        "ShortName": f"{file_id}.wav" 
                    }
            else:
                self.append_conversion_log(f"✗ {audio_name}: ID not found in WEM files")
                return None
        else:

            if audio_name.startswith("VO_"):
                for entry in self.all_files:
                    shortname = entry.get("ShortName", "")
                    base_shortname = os.path.splitext(shortname)[0]
                    language = entry.get("Language", "")
                    
                    if base_shortname == audio_name and language in voice_lang_filter:
                        found_entry = entry
                        file_id = entry.get("Id", "")
                        break
                
                if not found_entry and '_' in audio_name:
                    parts = audio_name.split('_')
                    if len(parts) > 1 and len(parts[-1]) == 8:
                        try:
                            int(parts[-1], 16)
                            audio_name_no_hex = '_'.join(parts[:-1])
                            for entry in self.all_files:
                                shortname = entry.get("ShortName", "")
                                base_shortname = os.path.splitext(shortname)[0]
                                language = entry.get("Language", "")
                                
                                if base_shortname == audio_name_no_hex and language in voice_lang_filter:
                                    found_entry = entry
                                    file_id = entry.get("Id", "")
                                    break
                        except ValueError:
                            pass
                
                if not found_entry:
                    self.append_conversion_log(f"✗ {audio_name}: Not found in SoundbanksInfo for language {selected_language}")
                    return None
            else:
 
                for entry in self.all_files:
                    shortname = entry.get("ShortName", "")
                    base_shortname = os.path.splitext(shortname)[0]
                    language = entry.get("Language", "")
                    
                    if base_shortname == audio_name and language == "SFX":
                        found_entry = entry
                        file_id = entry.get("Id", "")
                        break
                
                if not found_entry:
                    self.append_conversion_log(f"✗ {audio_name}: Not found in SoundbanksInfo (SFX)")
                    return None
        
        if not found_entry or not file_id:
            self.append_conversion_log(f"✗ {audio_name}: Not found in database")
            return None
        
        if file_id not in self.wem_index:
            self.append_conversion_log(f"✗ {audio_name}: WEM file for ID {file_id} not found in Wems folder")
            return None
        language_from_db = found_entry.get("Language", "SFX")
        if language_from_db in voice_lang_filter:
            language = target_dir_voice
            if target_dir_voice in self.wem_index[file_id]:
                wem_path = self.wem_index[file_id][target_dir_voice]['path']
            else:
                available_langs = list(self.wem_index[file_id].keys())
                self.append_conversion_log(f"✗ {audio_name}: WEM for voice file not found in {target_dir_voice} (available: {', '.join(available_langs)})")
                return None
        else:
            language = "SFX"
            if "SFX" in self.wem_index[file_id]:
                wem_path = self.wem_index[file_id]["SFX"]['path']
            else:
                available_langs = list(self.wem_index[file_id].keys())
                if available_langs:
                    self.append_conversion_log(f"⚠ {audio_name}: WEM for SFX not found in SFX folder, using backup from '{available_langs[0]}'")
                else:
                    self.append_conversion_log(f"✗ {audio_name}: WEM for SFX file not found in any folder")
                    return None
        
        if not wem_path or not os.path.exists(wem_path):
            self.append_conversion_log(f"✗ {audio_name}: WEM file path not valid")
            return None
        
        existing_pair_index = None
        file_pairs_copy = list(self.wav_converter.file_pairs)
        for i, pair in enumerate(file_pairs_copy):
            if pair.get('target_wem') == wem_path and i != existing_file_index:
                existing_pair_index = i
                break
        
        if existing_pair_index is not None:
            existing_pair = self.wav_converter.file_pairs[existing_pair_index]
            
            if skip_all:
                self.append_conversion_log(
                    f"✗ Skipped {os.path.basename(audio_path)}: "
                    f"Target WEM already used by {existing_pair['audio_name']}"
                )
                return False
            
            if replace_all:
                self.wav_converter.file_pairs[existing_pair_index] = {
                    "audio_file": audio_path,
                    "original_format": audio_ext,
                    "needs_conversion": audio_ext != '.wav',
                    "target_wem": wem_path,
                    "audio_name": os.path.basename(audio_path),
                    "wav_name": os.path.basename(audio_path),
                    "target_name": f"{file_id}.wem",
                    "target_size": os.path.getsize(wem_path),
                    "language": language,
                    "file_id": file_id
                }
                if existing_file_index is not None and existing_file_index != existing_pair_index:
                    del self.wav_converter.file_pairs[existing_file_index]
                self.append_conversion_log(
                    f"✓ Replaced {existing_pair['audio_name']} with {os.path.basename(audio_path)} -> {file_id}.wem"
                )
                return True
            
            response = QtCore.QMetaObject.invokeMethod(
                self, "_ask_for_replace", QtCore.Qt.BlockingQueuedConnection,
                QtCore.Q_ARG(str, file_id),
                QtCore.Q_ARG(str, existing_pair['audio_name']),
                QtCore.Q_ARG(str, os.path.basename(audio_path)),
                QtCore.Q_ARG(bool, auto_mode)
            )

            if response == "Replace":
                self.wav_converter.file_pairs[existing_pair_index] = {
                    "audio_file": audio_path,
                    "original_format": audio_ext,
                    "needs_conversion": audio_ext != '.wav',
                    "target_wem": wem_path,
                    "audio_name": os.path.basename(audio_path),
                    "wav_name": os.path.basename(audio_path),
                    "target_name": f"{file_id}.wem",
                    "target_size": os.path.getsize(wem_path),
                    "language": language,
                    "file_id": file_id
                }
                if existing_file_index is not None and existing_file_index != existing_pair_index:
                    del self.wav_converter.file_pairs[existing_file_index]
                self.append_conversion_log(
                    f"✓ Replaced {existing_pair['audio_name']} with {os.path.basename(audio_path)} -> {file_id}.wem"
                )
                return True
            elif response == "Replace All":
                return 'replace_all'
            elif response == "Skip All":
                return 'skip_all'
            else:  # Skip
                self.append_conversion_log(
                    f"✗ Skipped {os.path.basename(audio_path)}: User chose to keep {existing_pair['audio_name']}"
                )
                return False
        
        new_file_pair = {
            "audio_file": audio_path,
            "original_format": audio_ext,
            "needs_conversion": audio_ext != '.wav',
            "target_wem": wem_path,
            "audio_name": os.path.basename(audio_path),
            "wav_name": os.path.basename(audio_path),
            "target_name": f"{file_id}.wem",
            "target_size": os.path.getsize(wem_path),
            "language": language,
            "file_id": file_id
        }

        if existing_file_index is not None:
            self.wav_converter.file_pairs[existing_file_index] = new_file_pair
            self.append_conversion_log(f"✓ Updated {os.path.basename(audio_path)} -> {file_id}.wem ({language})")
        else:
            self.wav_converter.file_pairs.append(new_file_pair)
            self.append_conversion_log(f"✓ Added {os.path.basename(audio_path)} -> {file_id}.wem ({language})")
        
        return True    
    def toggle_conversion(self):
        """Toggle between start and stop conversion"""
        self.settings.save()
        if not self.is_converting:
            self.start_wav_conversion()
        else:
            self.stop_wav_conversion()
    def load_converter_file_list(self):
        path = os.path.join(self.base_path, "converter_file_list.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                file_list = json.load(f)
            self.wav_converter.file_pairs.clear()
            for pair in file_list:

                audio_name = pair.get("audio_name") or pair.get("wav_name") or pair.get("target_name") or ""
                wav_name = pair.get("wav_name") or pair.get("audio_name") or pair.get("target_name") or ""
                new_pair = dict(pair)
                new_pair["audio_name"] = audio_name
                new_pair["wav_name"] = wav_name

                if new_pair.get("audio_file") and new_pair.get("target_wem"):
                    self.wav_converter.file_pairs.append(new_pair)
            self.update_conversion_files_table()
        except Exception as e:
            DEBUG.log(f"Failed to load converter file list: {e}", "ERROR")
    def create_converter_tab(self):
        """Create updated converter tab"""
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5) 
        layout.setSpacing(5)
        
        header = QtWidgets.QLabel("Audio Converter & Processor")
        header.setStyleSheet("font-size: 16px; font-weight: bold; padding: 5px;")
        layout.addWidget(header)
        
        self.converter_tabs = QtWidgets.QTabWidget()
        
     
        self.create_wav_to_wem_tab()
        self.create_localization_exporter_simple_tab()
 
        self.create_wem_processor_main_tab()
        
        layout.addWidget(self.converter_tabs)
        
        self.tabs.addTab(tab, "Converter")
    def show_conversion_context_menu(self, pos):
        """Show context menu for conversion table"""
        item = self.conversion_files_table.itemAt(pos)
        if not item:
            return
        
        selected_rows = set()
        for selected_item in self.conversion_files_table.selectedItems():
            selected_rows.add(selected_item.row())
        
        menu = QtWidgets.QMenu()

        if len(selected_rows) == 1:
            row = item.row()
            if row >= 0 and row < len(self.wav_converter.file_pairs):
                change_target_action = menu.addAction("📁 Browse for Target WEM...")
                change_target_action.triggered.connect(lambda: self.select_custom_target_wem(row))
                
                wems_folder = os.path.join(self.base_path, "Wems")
                available_folders = []
                
                if os.path.exists(wems_folder):
                    for folder in os.listdir(wems_folder):
                        folder_path = os.path.join(wems_folder, folder)
                        if os.path.isdir(folder_path):
                            wem_count = sum(1 for f in os.listdir(folder_path) if f.endswith('.wem'))
                            if wem_count > 0:
                                available_folders.append((folder, folder_path, wem_count))
                
                if available_folders:
                    menu.addSeparator()
                    quick_menu = menu.addMenu("⚡ Quick Select")
                    
                    available_folders.sort(key=lambda x: x[2], reverse=True)
                    
                    for folder_name, folder_path, file_count in available_folders:
                        folder_action = quick_menu.addAction(f"📁 {folder_name} ({file_count} files)")
                        folder_action.triggered.connect(
                            lambda checked, p=folder_path, r=row: self.quick_select_from_folder(p, r)
                        )
                
                menu.addSeparator()
        
        if len(selected_rows) > 1:
            remove_action = menu.addAction(f"❌ Remove {len(selected_rows)} Files")
        else:
            remove_action = menu.addAction("❌ Remove")
        
        remove_action.triggered.connect(lambda: self.remove_conversion_file())
        
        menu.exec_(self.conversion_files_table.mapToGlobal(pos))
    def quick_select_from_folder(self, folder_path, row):
        """Quick select WEM from specific folder"""
        file_pair = self.wav_converter.file_pairs[row]
        wav_name = file_pair['wav_name']
        
        wem_file, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 
            f"Select Target WEM for {wav_name} from {os.path.basename(folder_path)}",
            folder_path,
            "WEM Audio Files (*.wem);;All Files (*.*)"
        )
        
        if not wem_file:
            return
        
        self.process_selected_wem_file(wem_file, row)
    def process_selected_wem_file(self, wem_file, row):
        """Process selected WEM file and update conversion table"""
        file_pair = self.wav_converter.file_pairs[row]
        wav_name = file_pair['wav_name']
        
        try:
           
            file_size = os.path.getsize(wem_file)
            file_name = os.path.basename(wem_file)
            file_id = os.path.splitext(file_name)[0]
           
            parent_folder = os.path.basename(os.path.dirname(wem_file))
            
            file_info = None
            for entry in self.all_files:
                if entry.get("Id", "") == file_id:
                    file_info = entry
                    break
            
            if file_info:
                language = file_info.get("Language", parent_folder)
                original_name = file_info.get("ShortName", file_name)
                self.append_conversion_log(f"Found {file_id} in database: {original_name}")
            else:
                
                language = parent_folder
                original_name = file_name
                self.append_conversion_log(f"File {file_id} not found in database, using folder name as language")
            
            self.wav_converter.file_pairs[row] = {
                "wav_file": file_pair['wav_file'],
                "target_wem": wem_file,
                "wav_name": file_pair['wav_name'],
                "target_name": file_name,
                "target_size": file_size,
                "language": language,
                "file_id": file_id
            }
            
            self.update_conversion_files_table()
            
            size_kb = file_size / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            
            self.append_conversion_log(
                f"✓ Changed target for {wav_name}:\n"
                f"  → {file_name} (ID: {file_id})\n"
                f"  → Language: {language}\n"
                f"  → Size: {size_str}\n"
                f"  → Path: {wem_file}"
            )
            
            self.status_bar.showMessage(f"Target updated: {wav_name} → {file_name}", 3000)
            
        except Exception as e:
            self.append_conversion_log(f"✗ Error processing {wem_file}: {str(e)}")
            QtWidgets.QMessageBox.warning(
                self, "Error", 
                f"Error processing selected file:\n{str(e)}"
            )
    def select_custom_target_wem(self, row):
        """Select custom target WEM file from file system"""
        file_pair = self.wav_converter.file_pairs[row]
        wav_name = file_pair['wav_name']
        
        wems_folder = os.path.join(self.base_path, "Wems")
        if not os.path.exists(wems_folder):
            wems_folder = self.base_path
        
        wem_file, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 
            f"Select Target WEM for {wav_name}",
            wems_folder,
            "WEM Audio Files (*.wem);;All Files (*.*)"
        )
        
        if not wem_file:
            return
      
        self.process_selected_wem_file(wem_file, row)

    def remove_conversion_file(self, row=None):
        """Remove file(s) from conversion list"""
        if row is None:
            selected_rows = set()
            for item in self.conversion_files_table.selectedItems():
                selected_rows.add(item.row())
            
            if not selected_rows:
                return
            
            selected_rows = sorted(selected_rows, reverse=True)
            
            if len(selected_rows) > 1:
                reply = QtWidgets.QMessageBox.question(
                    self, "Confirm Removal",
                    f"Remove {len(selected_rows)} selected files?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                )
                if reply != QtWidgets.QMessageBox.Yes:
                    return
            
            removed_names = []
            for row_idx in selected_rows:
                if row_idx < len(self.wav_converter.file_pairs):
                    removed_names.append(self.wav_converter.file_pairs[row_idx]['audio_name'])
                    del self.wav_converter.file_pairs[row_idx]
            
            self.update_conversion_files_table()
            
            if len(removed_names) == 1:
                self.append_conversion_log(f"Removed {removed_names[0]} from conversion list")
            else:
                self.append_conversion_log(f"Removed {len(removed_names)} files from conversion list")
                
        else:
            if row < 0 or row >= len(self.wav_converter.file_pairs):
                return
            
            file_pair = self.wav_converter.file_pairs[row]
            wav_name = file_pair['audio_name']
            
            del self.wav_converter.file_pairs[row]
            self.update_conversion_files_table()
            self.append_conversion_log(f"Removed {wav_name} from conversion list")
        
    def create_conversion_logs_tab(self):
        """Create logs tab for conversion results"""
        logs_tab = QtWidgets.QWidget()
        logs_layout = QtWidgets.QVBoxLayout(logs_tab)
        
       
        header_widget = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(header_widget)
        
        header_label = QtWidgets.QLabel(self.tr("conversion_logs"))
        header_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        clear_logs_btn = QtWidgets.QPushButton(self.tr("clear_logs"))
        clear_logs_btn.setMaximumWidth(120)
        clear_logs_btn.clicked.connect(self.clear_conversion_logs)
        
        save_logs_btn = QtWidgets.QPushButton(self.tr("save_logs"))
        save_logs_btn.setMaximumWidth(120)
        save_logs_btn.clicked.connect(self.save_conversion_logs)
        
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        header_layout.addWidget(clear_logs_btn)
        header_layout.addWidget(save_logs_btn)
        
        logs_layout.addWidget(header_widget)
        
    
        self.conversion_logs = QtWidgets.QTextEdit()
        self.conversion_logs.setReadOnly(True)
        self.conversion_logs.setFont(QtGui.QFont("Consolas", 9))
        self.conversion_logs.setPlainText(self.tr("subtitle_export_ready"))
        
        logs_layout.addWidget(self.conversion_logs)
        
        self.wav_converter_tabs.addTab(logs_tab, self.tr("conversion_logs"))
    def clear_conversion_logs(self):
        """Clear conversion logs"""
        self.conversion_logs.clear()
        self.conversion_logs.setPlainText(self.tr("logs_cleared"))

    def save_conversion_logs(self):
        """Save conversion logs to file"""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, self.tr("save_logs"),
            f"conversion_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt)"
        )
        
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(self.conversion_logs.toPlainText())
                self.update_conversion_status(self.tr("logs_saved"), "green")
            except Exception as e:
                QtWidgets.QMessageBox.warning(
                    self, self.tr("error"), 
                    f"{self.tr('error_saving_logs')}: {str(e)}"
                )

    def append_conversion_log(self, message, level="INFO"):
        self.log_signal.emit(message, level)
    @QtCore.pyqtSlot(str, str)
    def append_to_log_widget(self, message, level):

        if hasattr(self, 'conversion_logs'):
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] [{level}] {message}"
            color_map = {
                "INFO": "#d4d4d4" if self.settings.data["theme"] == "dark" else "#1e1e1e",
                "WARNING": "#FFC107",
                "ERROR": "#F44336",
                "SUCCESS": "#4CAF50"
            }
            color = color_map.get(level.upper(), color_map["INFO"])
            html_entry = f"<span style='color:{color};'>{log_entry}</span>"
            self.conversion_logs.append(html_entry)
            scrollbar = self.conversion_logs.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    @QtCore.pyqtSlot(str, result=str)
    def _ask_for_update(self, filename):

        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("File Already Added")
        msg.setText(f"File '{filename}' is already in the conversion list.\n\nDo you want to update its settings?")
        update_btn = msg.addButton("Update", QtWidgets.QMessageBox.YesRole)
        skip_btn = msg.addButton("Skip", QtWidgets.QMessageBox.NoRole)
        msg.setDefaultButton(skip_btn)
        self.show_dialog(msg)
        return "Update" if msg.clickedButton() == update_btn else "Skip"

    @QtCore.pyqtSlot(str, str, str, bool, result=str)
    def _ask_for_replace(self, file_id, existing_name, new_name, auto_mode):

        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Duplicate Target WEM")
        msg.setText(f"Target WEM '{file_id}.wem' is already assigned to:\n\nCurrent: {existing_name}\nNew: {new_name}\n\nDo you want to replace it?")
        replace_btn = msg.addButton("Replace", QtWidgets.QMessageBox.YesRole)
        skip_btn = msg.addButton("Skip", QtWidgets.QMessageBox.NoRole)
        if auto_mode:
            msg.addButton("Replace All", QtWidgets.QMessageBox.YesRole)
            msg.addButton("Skip All", QtWidgets.QMessageBox.NoRole)
        msg.setDefaultButton(skip_btn)
        self.show_dialog(msg)
        return msg.clickedButton().text()
    def add_all_audio_files_auto(self):
        if hasattr(self, 'add_files_thread') and self.add_files_thread.isRunning():
            QtWidgets.QMessageBox.information(self, "In Progress", "A file search is already in progress. Please wait.")
            return

        audio_folder = self.wav_folder_edit.text()

        if not audio_folder or not os.path.exists(audio_folder):
            QtWidgets.QMessageBox.warning(
                self, self.tr("error"), 
                "Please select folder with audio files"
            )
            return
        self.settings.save()
        progress = ProgressDialog(self, "Adding Files")
        progress.show()
        
        self.add_files_thread = AddFilesThread(self, audio_folder)
        self.add_files_thread.progress_updated.connect(progress.set_progress)
        self.add_files_thread.details_updated.connect(progress.append_details)
        self.add_files_thread.finished.connect(lambda a, r, s, n: self.on_add_files_finished(progress, a, r, s, n))
        self.add_files_thread.error_occurred.connect(lambda e: self.on_add_files_error(progress, e))
        
        self.add_files_thread.start()

    def table_dropEvent(self, event):

        if not event.mimeData().hasUrls():
            event.ignore()
            return
        
        file_paths = []
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                file_paths.append(file_path)
        
        if not file_paths:
            event.ignore()
            return
        
        progress = ProgressDialog(self, self.tr("drop_audio_title"))
        progress.setWindowFlags(progress.windowFlags() | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
        progress.setWindowFlags(progress.windowFlags() & ~QtCore.Qt.WindowCloseButtonHint)
        progress.show()
        
        self.drop_files_thread = DropFilesThread(self, file_paths)
        self.drop_files_thread.progress_updated.connect(progress.set_progress)
        self.drop_files_thread.details_updated.connect(progress.append_details)
        self.drop_files_thread.finished.connect(lambda a, r, s, n: self.on_drop_files_finished(progress, a, r, s, n))
        self.drop_files_thread.error_occurred.connect(lambda e: self.on_drop_files_error(progress, e))
        
        self.drop_files_thread.start()
        
        event.acceptProposedAction()

    def on_drop_files_error(self, progress, error):
        progress.close()
        
        QtWidgets.QMessageBox.warning(
            self, "Error",
            f"Error during file drop:\n\n{error}"
        )
        
        self.append_conversion_log(f"✗ Error: {error}")    
    def save_converter_file_list(self):
        file_list = []
        for pair in self.wav_converter.file_pairs:
            audio_name = pair.get("audio_name") or pair.get("wav_name") or pair.get("target_name") or ""
            wav_name = pair.get("wav_name") or pair.get("audio_name") or pair.get("target_name") or ""
            file_list.append({
                "audio_file": pair.get("audio_file") or pair.get("wav_file"),
                "target_wem": pair.get("target_wem"),
                "audio_name": audio_name,
                "wav_name": wav_name,
                "target_name": pair.get("target_name"),
                "target_size": pair.get("target_size"),
                "language": pair.get("language"),
                "file_id": pair.get("file_id")
            })
        try:
            with open(os.path.join(self.base_path, "converter_file_list.json"), "w", encoding="utf-8") as f:
                json.dump(file_list, f, ensure_ascii=False, indent=2)
        except Exception as e:
            DEBUG.log(f"Failed to save converter file list: {e}", "ERROR")  
    def determine_language(self, language_from_soundbank):
        lang_map = {
            'English(US)': 'English(US)',
            'French(France)': 'French(France)', 
            'Francais': 'French(France)',
            'SFX': 'SFX'
        }
        
        return lang_map.get(language_from_soundbank, 'SFX')

    def update_conversion_files_table(self):
        """Update conversion files table with tooltips"""
        self.conversion_files_table.setRowCount(len(self.wav_converter.file_pairs))
        
        for i, pair in enumerate(self.wav_converter.file_pairs):
            audio_name = pair.get('audio_name') or pair.get('wav_name', 'Unknown')
            audio_file = pair.get('audio_file') or pair.get('wav_file', '')
            
            format_info = ""
            if pair.get('original_format') and pair['original_format'] != '.wav':
                format_info = f" [{pair['original_format']}]"
            
            audio_item = QtWidgets.QTableWidgetItem(audio_name + format_info)
            audio_item.setFlags(audio_item.flags() & ~QtCore.Qt.ItemIsEditable)
            audio_item.setToolTip(f"Path: {audio_file}")
            
            if pair.get('needs_conversion', False):
                audio_item.setBackground(QtGui.QColor(255, 245, 220))
            
            self.conversion_files_table.setItem(i, 0, audio_item)
            
            wem_display = f"{pair['file_id']}.wem"
            wem_item = QtWidgets.QTableWidgetItem(wem_display)
            wem_item.setFlags(wem_item.flags() & ~QtCore.Qt.ItemIsEditable)
            wem_item.setToolTip(f"Source: {pair['target_wem']}")
            self.conversion_files_table.setItem(i, 1, wem_item)

            lang_item = QtWidgets.QTableWidgetItem(pair['language'])
            lang_item.setFlags(lang_item.flags() & ~QtCore.Qt.ItemIsEditable)
            
            if self.settings.data["theme"] == "dark":
                if pair['language'] == 'English(US)':
                    lang_item.setBackground(QtGui.QColor(30, 60, 30)) 
                elif pair['language'] == 'Francais':
                    lang_item.setBackground(QtGui.QColor(30, 30, 60))
            else:
                if pair['language'] == 'English(US)':
                    lang_item.setBackground(QtGui.QColor(230, 255, 230)) 
                elif pair['language'] == 'Francais':
                    lang_item.setBackground(QtGui.QColor(230, 230, 255)) 
                
            self.conversion_files_table.setItem(i, 2, lang_item)
            
            size_kb = pair['target_size'] / 1024
            size_item = QtWidgets.QTableWidgetItem(f"{size_kb:.1f} KB")
            size_item.setFlags(size_item.flags() & ~QtCore.Qt.ItemIsEditable)
            size_item.setToolTip(f"Exact size: {pair['target_size']:,} bytes")
            self.conversion_files_table.setItem(i, 3, size_item)
            
            status_text = self.tr("ready")
            if pair.get('needs_conversion', False):
                status_text += " (conversion needed)"
            
            status_item = QtWidgets.QTableWidgetItem(status_text)
            status_item.setFlags(status_item.flags() & ~QtCore.Qt.ItemIsEditable)
            status_item.setToolTip("File ready for conversion")
            self.conversion_files_table.setItem(i, 4, status_item)
        
        count = len(self.wav_converter.file_pairs)
        self.files_count_label.setText(self.tr("files_ready_count").format(count=count))
        
        if count > 0:
            self.files_count_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        else:
            self.files_count_label.setStyleSheet("font-weight: bold; color: #666;")

    def update_conversion_status(self, message, color="green"):
      
        color_map = {
            "green": "#4CAF50",
            "blue": "#2196F3", 
            "red": "#F44336",
            "orange": "#FF9800"
        }
        self.conversion_status.setText(message)
        self.conversion_status.setStyleSheet(f"color: {color_map.get(color, color)}; font-size: 12px;")

    def start_wav_conversion(self):
        """Start WAV file conversion"""
        if not self.wav_converter.file_pairs:
            QtWidgets.QMessageBox.warning(
                self, self.tr("warning"), 
                self.tr("add_files_warning")
            )
            return
        
        if not all([self.wwise_path_edit.text(), self.converter_project_path_edit.text()]):
            QtWidgets.QMessageBox.warning(
                self, self.tr("error"), 
                "Please specify Wwise and project paths!"
            )
            return
        
        self.append_conversion_log("=== CONVERSION DIAGNOSTICS ===")
        self.append_conversion_log(f"Wwise path: {self.wwise_path_edit.text()}")
        self.append_conversion_log(f"Project path: {self.converter_project_path_edit.text()}")
        self.append_conversion_log(f"Files to convert: {len(self.wav_converter.file_pairs)}")
        self.append_conversion_log(f"Adaptive mode: {self.adaptive_mode_radio.isChecked()}")
        
        wwise_path = self.wwise_path_edit.text()
        project_path = self.converter_project_path_edit.text()
        
        if not os.path.exists(wwise_path):
            self.append_conversion_log(f"ERROR: Wwise path does not exist: {wwise_path}")
            QtWidgets.QMessageBox.warning(self, "Error", f"Wwise path does not exist:\n{wwise_path}")
            return
        
        if not os.path.exists(project_path):
            os.makedirs(project_path, exist_ok=True)
            
        self.set_conversion_state(True)
        
        self.wav_converter.set_adaptive_mode(self.adaptive_mode_radio.isChecked())
        
        temp_output = os.path.join(self.base_path, "temp_wem_output")
        os.makedirs(temp_output, exist_ok=True)
        
        self.wav_converter.set_paths(wwise_path, project_path, temp_output)
        
        for i in range(self.conversion_files_table.rowCount()):
            status_item = self.conversion_files_table.item(i, 4)
            status_item.setText(self.tr("waiting"))
            status_item.setBackground(QtGui.QColor(255, 255, 200))
        
        self.conversion_progress.setValue(0)
        
        mode_text = self.tr("adaptive_mode") if self.adaptive_mode_radio.isChecked() else self.tr("strict_mode")
        self.update_conversion_status(
            self.tr("starting_conversion").format(mode=mode_text), 
            "blue"
        )
        self.append_conversion_log(f"=== {self.tr('starting_conversion').format(mode=mode_text.upper())} ===")
        
        self.conversion_thread = threading.Thread(target=self.wav_converter.convert_all_files)
        self.conversion_thread.daemon = True  
        self.conversion_thread.start()
    
    def set_conversion_state(self, converting):
        """Set the conversion state and update UI accordingly"""
        self.is_converting = converting
        
        if converting:

            self.convert_btn.setText("Stop")
            self.convert_btn.setStyleSheet("""
                QPushButton { 
                    background-color: #F44336; 
                    color: white; 
                    font-weight: bold; 
                    padding: 5px 15px; 
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #D32F2F;
                }
            """)
            
            self.strict_mode_radio.setEnabled(False)
            self.adaptive_mode_radio.setEnabled(False)
            self.wwise_path_edit.setEnabled(False)
            self.converter_project_path_edit.setEnabled(False)
            self.wav_folder_edit.setEnabled(False)
            
        else:

            self.convert_btn.setText(self.tr("convert"))
            self.convert_btn.setStyleSheet("""
                QPushButton { 
                    background-color: #4CAF50; 
                    color: white; 
                    font-weight: bold; 
                    padding: 5px 15px; 
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
            """)
            
            self.strict_mode_radio.setEnabled(True)
            self.adaptive_mode_radio.setEnabled(True)
            self.wwise_path_edit.setEnabled(True)
            self.converter_project_path_edit.setEnabled(True)
            self.wav_folder_edit.setEnabled(True)
            
            self.wav_converter.reset_state()
    
    def stop_wav_conversion(self):
        """Stop the current conversion process"""
        if self.is_converting:
      
            self.wav_converter.stop_conversion()
            
            self.update_conversion_status("Stopping conversion...", "orange")
            self.append_conversion_log("User requested conversion stop")
            
            if hasattr(self, 'conversion_thread') and self.conversion_thread and self.conversion_thread.is_alive():
                self.conversion_thread.join(timeout=3.0)
                
                if self.conversion_thread.is_alive():
                    self.append_conversion_log("Warning: Conversion thread did not stop gracefully")
            
            self.set_conversion_state(False)
            self.update_conversion_status("Conversion stopped by user", "red")
            self.append_conversion_log("Conversion stopped")
            
            self.conversion_progress.setValue(0)
    def on_add_files_finished(self, progress, added, replaced, skipped, not_found):
        progress.close()
        
        self.update_conversion_files_table()
        
        message = f"Added {added} files"
        if replaced > 0:
            message += f"\nReplaced {replaced} files"
        if skipped > 0:
            message += f"\nSkipped {skipped} files"
        if not_found > 0:
            message += f"\n{not_found} files not found in database"
        
        self.append_conversion_log(f"\nResults:\n{message}")
        
        if skipped > 0 or not_found > 0:
            message += "\n\nDetails (see Logs tab for full report):"
            message += "\n- Skipped files: Check Logs for reasons (duplicates, user choice, etc.)"
            message += "\n- Not found: Files without matching WEM/ID in database"
        
        self.save_converter_file_list()
        QtWidgets.QMessageBox.information(self, self.tr("search_complete"), message)

    def on_drop_files_finished(self, progress, added, replaced, skipped, not_found):
        progress.close()
        
        self.update_conversion_files_table()
        
        message = f"Added {added} files"
        if replaced > 0:
            message += f"\nReplaced {replaced} files"
        if skipped > 0:
            message += f"\nSkipped {skipped} files"
        if not_found > 0:
            message += f"\n{not_found} files not found in database"
        
        self.append_conversion_log(f"\nDrop Results:\n{message}")
        
        if skipped > 0 or not_found > 0:
            message += "\n\nDetails (see Logs tab for full report):"
            message += "\n- Skipped files: Check Logs for reasons (duplicates, user choice, etc.)"
            message += "\n- Not found: Files without matching WEM/ID in database"
        
        self.save_converter_file_list()
        QtWidgets.QMessageBox.information(self, self.tr("search_complete"), message)

    def on_add_files_error(self, progress, error):
        progress.close()
        
        QtWidgets.QMessageBox.warning(
            self, "Error",
            f"Error during file addition:\n\n{error}"
        )
        
        self.append_conversion_log(f"✗ Error: {error}")
    def on_conversion_finished(self, results):
        """Handle conversion completion with logging"""
        try:
            successful = [r for r in results if r['result'].get('success', False)]
            failed = [r for r in results if not r['result'].get('success', False)]
            size_warnings = [r for r in results if r['result'].get('size_warning', False)]
            resampled = [r for r in successful if r['result'].get('resampled', False)]
            stopped = [r for r in results if r['result'].get('stopped', False)]
            
            self.conversion_progress.setValue(100)
        
            self.append_conversion_log("=" * 50)
            
            if stopped:
                self.append_conversion_log("CONVERSION STOPPED BY USER")
                self.update_conversion_status("Conversion stopped", "orange")
            else:
                self.append_conversion_log("CONVERSION RESULTS")
            
            self.append_conversion_log("=" * 50)
            self.append_conversion_log(f"Successful: {len(successful)}")
            if resampled:
                self.append_conversion_log(f"Resampled: {len(resampled)}")
            self.append_conversion_log(f"Failed: {len(failed)}")
            if size_warnings:
                self.append_conversion_log(f"Size warnings: {len(size_warnings)}")
            if stopped:
                self.append_conversion_log(f"Stopped: {len(stopped)}")
        
            for i, result_item in enumerate(results):
                if i < self.conversion_files_table.rowCount():
                    status_item = self.conversion_files_table.item(i, 4)
                    result = result_item['result']
                    wav_name = result_item['file_pair']['audio_name']
                    
                    if result.get('stopped', False):
                        status_item.setText("⏹ Stopped")
                        status_item.setBackground(QtGui.QColor(255, 200, 100))
                        status_item.setToolTip("Conversion stopped by user")
                        self.append_conversion_log(f"⏹ {wav_name}: Stopped by user")
                        
                    elif result.get('success', False):
                        size_diff = result.get('size_diff_percent', 0)
                        status_text = "✓ Done"
                        tooltip_text = "Converted successfully"
                        
                        if result.get('resampled', False):
                            sample_rate = result.get('sample_rate', 'unknown')
                            status_text = f"✓ Done ({sample_rate}Hz)"
                            tooltip_text = f"Converted with resampling to {sample_rate}Hz"
                        
                        if size_diff > 2:
                            status_text += f" (~{size_diff:.1f}%)"
                            status_item.setBackground(QtGui.QColor(255, 255, 200))
                        else:
                            status_item.setBackground(QtGui.QColor(230, 255, 230))
                        
                        status_item.setText(status_text)
                        status_item.setToolTip(tooltip_text)
                
                        final_size = result.get('final_size', 0)
                        attempts = result.get('attempts', 0)
                        conversion = result.get('conversion', 'N/A')
                        language = result_item['file_pair']['language']
                        
                        log_msg = f"✓ {wav_name} -> {language} ({final_size:,} bytes, attempts: {attempts}, Conversion: {conversion})"
                        if result.get('resampled', False):
                            log_msg += f" [Resampled to {result.get('sample_rate')}Hz]"
                        
                        self.append_conversion_log(log_msg)
                        
                    else:
                        if result.get('size_warning', False):
                            status_item.setText("⚠ Size")
                            status_item.setBackground(QtGui.QColor(255, 200, 200))
                        else:
                            status_item.setText("✗ Error")
                            status_item.setBackground(QtGui.QColor(255, 230, 230))
                        
                        status_item.setToolTip(result['error'])
                        self.append_conversion_log(f"✗ {wav_name}: {result['error']}")
            
            if successful and not stopped:
                self.update_conversion_status("Deploying files...", "blue")
                self.append_conversion_log("Deploying files...")
                
                try:
                    deployed_count = self.auto_deploy_converted_files_by_language(successful)
                    
                    self.update_conversion_status(
                        f"Done! Converted: {len(successful)}, deployed: {deployed_count}", 
                        "green"
                    )
                    
                    self.append_conversion_log(f"Files deployed to MOD_P: {deployed_count}")
                    self.append_conversion_log("Conversion completed successfully!")

                    message = f"Conversion completed!\n\nSuccessful: {len(successful)}\nFailed: {len(failed)}"
                    if size_warnings:
                        message += f"\nSize warnings: {len(size_warnings)}"
                    
                    QtWidgets.QMessageBox.information(
                        self, "Conversion Complete", message
                    )
                    
                except Exception as e:
                    self.update_conversion_status("Deployment error", "red")
                    self.append_conversion_log(f"DEPLOYMENT ERROR: {str(e)}")
                    QtWidgets.QMessageBox.warning(
                        self, "Error", 
                        f"Conversion complete, but deployment error:\n{str(e)}"
                    )
            elif stopped:
                self.update_conversion_status("Conversion stopped by user", "orange")
                QtWidgets.QMessageBox.information(
                    self, "Conversion Stopped", 
                    f"Conversion was stopped by user.\n\nCompleted: {len(successful)}\nRemaining: {len(stopped)}"
                )
            else:
                self.update_conversion_status("Conversion failed", "red")
                self.append_conversion_log("All files failed to convert")

                self.wav_converter_tabs.setCurrentIndex(1)
                
                QtWidgets.QMessageBox.warning(
                    self, "Error", 
                    f"All files failed to convert: {len(failed)} files.\n"
                    f"See logs for details."
                )
        finally:
            self.set_conversion_state(False)
    def auto_deploy_converted_files_by_language(self, successful_conversions):
        deployed_count = 0
        
        for conversion in successful_conversions:
            try:
                source_path = conversion['result']['output_path']
                file_pair = conversion['file_pair']
                language = file_pair['language']
                file_id = file_pair['file_id']
                
                # UPDATE: Deploy to 'Media' subfolder
                if language == "SFX":
                    target_dir = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media")
                else:
                    target_dir = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", "Media", language)
                
                os.makedirs(target_dir, exist_ok=True)
                
                dest_filename = f"{file_id}.wem"
                dest_path = os.path.join(target_dir, dest_filename)
                
                shutil.copy2(source_path, dest_path)
                deployed_count += 1
                
                DEBUG.log(f"Deployed: {file_pair['audio_name']} -> {dest_filename} in {language} (Media folder)")
                
            except Exception as e:
                DEBUG.log(f"Error deploying {file_pair['audio_name']}: {e}", "ERROR")
                raise e
        
        return deployed_count

    def auto_deploy_converted_files(self, successful_conversions):
       
        language = self.target_language_combo.currentText()
        
        if language == "SFX":
            target_dir = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows")
        else:
            target_dir = os.path.join(self.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", language)
        
        os.makedirs(target_dir, exist_ok=True)
        
        copied_count = 0
        for conversion in successful_conversions:
            try:
                source_path = conversion['result']['output_path']
                filename = os.path.basename(source_path)
                dest_path = os.path.join(target_dir, filename)
                
                shutil.copy2(source_path, dest_path)
                copied_count += 1
                
                DEBUG.log(f"Deployed: {filename} to {language}")
                
            except Exception as e:
                DEBUG.log(f"Error deploying {filename}: {e}", "ERROR")
                raise e
        
        DEBUG.log(f"Auto-deployed {copied_count} files to {target_dir}")
    def create_wem_processor_main_tab(self):
        """Create WEM processor with subtabs"""
   
        wem_tab = QtWidgets.QWidget()
        wem_layout = QtWidgets.QVBoxLayout(wem_tab)
        
  
        warning_label = QtWidgets.QLabel(f"""
        <div style="background-color: #ffebcc; border: 2px solid #ff9800; padding: 10px; border-radius: 5px;">
        <h3 style="color: #e65100; margin: 0;">{self.tr("wem_processor_warning")}</h3>
        <p style="margin: 5px 0;"><b>{self.tr("wem_processor_desc")}</b></p>
        <p style="margin: 5px 0;">{self.tr("wem_processor_recommendation")}</p>
        </div>
        """)
        wem_layout.addWidget(warning_label)
   
        self.wem_processor_tabs = QtWidgets.QTabWidget()

        self.create_wem_processing_tab()
        
        wem_layout.addWidget(self.wem_processor_tabs)
        
        self.converter_tabs.addTab(wem_tab, self.tr("wem_processor_tab_title"))
    def show_cleanup_dialog(self, subtitle_files, localization_path):
        
        if subtitle_files:
            DEBUG.log(f"First subtitle file keys: {list(subtitle_files[0].keys())}")
            DEBUG.log(f"First subtitle file: {subtitle_files[0]}")
        
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self.tr("cleanup_mod_subtitles"))
        dialog.setMinimumSize(800, 600)
        
        layout = QtWidgets.QVBoxLayout(dialog)

        header_label = QtWidgets.QLabel(self.tr("cleanup_subtitles_found").format(count=len(subtitle_files)))
        header_label.setStyleSheet("font-weight: bold; font-size: 14px; padding: 10px;")
        layout.addWidget(header_label)
        
        info_label = QtWidgets.QLabel(f"Location: {localization_path}")
        info_label.setStyleSheet("color: #666; padding-bottom: 10px;")
        layout.addWidget(info_label)

        controls_widget = QtWidgets.QWidget()
        controls_layout = QtWidgets.QHBoxLayout(controls_widget)
        
        select_all_btn = QtWidgets.QPushButton(self.tr("select_all"))
        select_none_btn = QtWidgets.QPushButton(self.tr("select_none"))
        
        controls_layout.addWidget(select_all_btn)
        controls_layout.addWidget(select_none_btn)
        controls_layout.addStretch()

        group_label = QtWidgets.QLabel(self.tr("quick_select"))
        controls_layout.addWidget(group_label)

        languages = set()
        for f in subtitle_files:
            if 'language' in f:
                languages.add(f['language'])
            elif 'lang' in f:
                languages.add(f['lang'])
        
        lang_combo = None
        if len(languages) > 1:
            lang_combo = QtWidgets.QComboBox()
            lang_combo.addItem(self.tr("select_by_language"))
            for lang in sorted(languages):
                count = sum(1 for f in subtitle_files if f.get('language', f.get('lang', '')) == lang)
                lang_combo.addItem(f"{lang} ({count} files)")
            controls_layout.addWidget(lang_combo)
        
        layout.addWidget(controls_widget)
        
        list_widget = QtWidgets.QListWidget()
        checkboxes = []
        
        for file_info in subtitle_files:
            item_widget = QtWidgets.QWidget()
            item_layout = QtWidgets.QHBoxLayout(item_widget)
            item_layout.setContentsMargins(5, 2, 5, 2)
            
            checkbox = QtWidgets.QCheckBox()
            checkbox.setChecked(True) 
            checkboxes.append(checkbox)
            
            filename = file_info.get('file') or file_info.get('filename') or file_info.get('path') or str(file_info)
            language = file_info.get('language') or file_info.get('lang') or 'Unknown'
            
            if isinstance(filename, str) and ('/' in filename or '\\' in filename):
                filename = os.path.basename(filename)
            
            file_label = QtWidgets.QLabel(f"{filename} ({language})")
            
            item_layout.addWidget(checkbox)
            item_layout.addWidget(file_label)
            item_layout.addStretch()
            
            list_item = QtWidgets.QListWidgetItem()
            list_item.setSizeHint(item_widget.sizeHint())
            list_widget.addItem(list_item)
            list_widget.setItemWidget(list_item, item_widget)
        
        layout.addWidget(list_widget)
        
        def select_all():
            for checkbox in checkboxes:
                checkbox.setChecked(True)
        
        def select_none():
            for checkbox in checkboxes:
                checkbox.setChecked(False)
        
        def select_by_language(index):
            if lang_combo and index > 0:
                selected_lang = lang_combo.itemText(index).split(' (')[0]
                for i, file_info in enumerate(subtitle_files):
                    file_lang = file_info.get('language') or file_info.get('lang', '')
                    checkboxes[i].setChecked(file_lang == selected_lang)
        
        select_all_btn.clicked.connect(select_all)
        select_none_btn.clicked.connect(select_none)
        if lang_combo:
            lang_combo.currentIndexChanged.connect(select_by_language)
        
        button_box = QtWidgets.QDialogButtonBox()
        delete_btn = button_box.addButton(self.tr("delete_selected"), QtWidgets.QDialogButtonBox.ActionRole)
        delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
        """)
        
        cancel_btn = button_box.addButton(QtWidgets.QDialogButtonBox.Cancel)
        
        layout.addWidget(button_box)

        def delete_selected():
            selected_files = []
            for i, checkbox in enumerate(checkboxes):
                if checkbox.isChecked():
                    selected_files.append(subtitle_files[i])
            
            if not selected_files:
                QtWidgets.QMessageBox.warning(
                    dialog, self.tr("no_selection"), 
                    self.tr("select_files_to_delete")
                )
                return

            reply = QtWidgets.QMessageBox.question(
                dialog, self.tr("confirm_deletion"),
                self.tr("delete_files_warning").format(count=len(selected_files)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            
            if reply == QtWidgets.QMessageBox.Yes:
                self.delete_subtitle_files(selected_files)
                dialog.accept()
        
        delete_btn.clicked.connect(delete_selected)
        cancel_btn.clicked.connect(dialog.reject)
        
        dialog.exec_()
    def delete_subtitle_files(self, files_to_delete):
        """Delete selected subtitle files"""
        DEBUG.log(f"Deleting {len(files_to_delete)} subtitle files")
        
        progress = ProgressDialog(self, "Deleting Subtitle Files")
        progress.show()
        
        deleted_count = 0
        error_count = 0

        self.subtitle_export_status.clear()
        self.subtitle_export_status.append("=== Cleaning Up MOD_P Subtitles ===")
        self.subtitle_export_status.append(f"Deleting {len(files_to_delete)} files...")
        self.subtitle_export_status.append("")
        
        for i, file_info in enumerate(files_to_delete):
            progress.set_progress(
                int((i / len(files_to_delete)) * 100),
                f"Deleting {file_info['filename']}..."
            )
            
            try:
                if os.path.exists(file_info['path']):
                    os.remove(file_info['path'])
                    deleted_count += 1
                    self.subtitle_export_status.append(f"✓ Deleted: {file_info['relative_path']}")
                    DEBUG.log(f"Deleted: {file_info['path']}")
 
                    dir_path = os.path.dirname(file_info['path'])
                    try:
                        if os.path.exists(dir_path) and not os.listdir(dir_path):
                            os.rmdir(dir_path)
                            self.subtitle_export_status.append(f"✓ Removed empty directory: {os.path.basename(dir_path)}")
                            
              
                            parent_dir = os.path.dirname(dir_path)
                            if os.path.exists(parent_dir) and not os.listdir(parent_dir):
                                os.rmdir(parent_dir)
                                self.subtitle_export_status.append(f"✓ Removed empty directory: {os.path.basename(parent_dir)}")
                    except OSError:
                        pass 
                        
                else:
                    self.subtitle_export_status.append(f"⚠ File already deleted: {file_info['relative_path']}")
                    
            except Exception as e:
                error_count += 1
                self.subtitle_export_status.append(f"✗ Error deleting {file_info['relative_path']}: {str(e)}")
                DEBUG.log(f"Error deleting {file_info['path']}: {e}", "ERROR")
        
        progress.close()
        
        self.subtitle_export_status.append("")
        self.subtitle_export_status.append("=== Cleanup Complete ===")
        self.subtitle_export_status.append(f"Files deleted: {deleted_count}")
        if error_count > 0:
            self.subtitle_export_status.append(f"Errors: {error_count}")
        
     
        if error_count == 0:
            QtWidgets.QMessageBox.information(
                self, self.tr("cleanup_complete"),
                self.tr("files_deleted_successfully").format(count=deleted_count)
            )
        else:
            QtWidgets.QMessageBox.warning(
                self, self.tr("cleanup_with_errors"),
                self.tr("files_deleted_with_errors").format(count=deleted_count, errors=error_count)
            )
        
        DEBUG.log(f"Cleanup complete: {deleted_count} deleted, {error_count} errors")
    def cleanup_mod_p_subtitles(self):
        """Clean up subtitle files from MOD_P folder"""
        DEBUG.log("=== Cleanup MOD_P Subtitles ===")
        
        localization_path = os.path.join(self.mod_p_path, "OPP", "Content", "Localization")
        
        if not os.path.exists(localization_path):
            QtWidgets.QMessageBox.information(
                self, self.tr("no_localization_found"), 
                self.tr("no_localization_message").format(path=localization_path)
            )
            return
        

        subtitle_files = []
        
        try:
            for root, dirs, files in os.walk(localization_path):
                for file in files:
                    if file.endswith('.locres'):
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, localization_path)
                        
                     
                        path_parts = relative_path.split(os.sep)
                        if len(path_parts) >= 3:
                            category = path_parts[0]
                            language = path_parts[1]
                            filename = path_parts[2]
                        else:
                            category = "Unknown"
                            language = "Unknown"
                            filename = file
                  
                        file_size = os.path.getsize(file_path)
                        file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                        
                        subtitle_files.append({
                            'path': file_path,
                            'relative_path': relative_path,
                            'category': category,
                            'language': language,
                            'filename': filename,
                            'size': file_size,
                            'modified': file_time
                        })
        
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", f"Error scanning localization folder:\n{str(e)}")
            return
        
        if not subtitle_files:
            QtWidgets.QMessageBox.information(
                self, self.tr("no_localization_found"), 
                self.tr("no_subtitle_files").format(path=localization_path)
            )
            return
        
        DEBUG.log(f"Found {len(subtitle_files)} subtitle files in MOD_P")
        

        self.show_cleanup_dialog(subtitle_files, localization_path)
    def create_localization_exporter_simple_tab(self):
        """Create simple localization exporter tab with cleanup functionality"""
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        
        header = QtWidgets.QLabel(self.tr("localization_exporter"))
        header.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px;")
        layout.addWidget(header)
        
        info_group = QtWidgets.QGroupBox(self.tr("export_modified_subtitles"))
        info_layout = QtWidgets.QVBoxLayout(info_group)
        
        info_text = QtWidgets.QLabel(f"""   
            <h3>{self.tr("export_modified_subtitles")}</h3>
            <p>{self.tr("exports_modified_subtitles_desc")}</p>
            <ul>
                <li>{self.tr("creates_mod_p_structure")}</li>
                <li>{self.tr("supports_multiple_categories")}</li>
                <li>{self.tr("each_language_separate_folder")}</li>
                <li>{self.tr("ready_files_for_mods")}</li>
            </ul>
            """)
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)
        
        layout.addWidget(info_group)
        
    
        buttons_widget = QtWidgets.QWidget()
        buttons_layout = QtWidgets.QHBoxLayout(buttons_widget)
        
      
        export_btn = QtWidgets.QPushButton(self.tr("export_subtitles_for_game"))
        export_btn.setMaximumWidth(200)
        export_btn.clicked.connect(self.export_subtitles_for_game)
        
 
        cleanup_btn = QtWidgets.QPushButton(self.tr("cleanup_mod_subtitles"))
        cleanup_btn.setMaximumWidth(200)
        cleanup_btn.clicked.connect(self.cleanup_mod_p_subtitles)
        
        buttons_layout.addWidget(export_btn)
        buttons_layout.addWidget(cleanup_btn)
        buttons_layout.addStretch()
        
        layout.addWidget(buttons_widget)
        
     
        self.subtitle_export_status = QtWidgets.QTextEdit()
        self.subtitle_export_status.setReadOnly(True)
        self.subtitle_export_status.setPlainText(self.tr("subtitle_export_ready"))
        layout.addWidget(self.subtitle_export_status)
        
        # self.converter_tabs.addTab(tab, self.tr("localization_exporter"))
    def create_wem_processing_tab(self):

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        
        header = QtWidgets.QLabel("WEM File Processing")
        header.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px;")
        layout.addWidget(header)
        
        card = QtWidgets.QGroupBox("Instructions")
        card_layout = QtWidgets.QVBoxLayout(card)
        
        instructions = QtWidgets.QLabel(self.tr("converter_instructions2"))
        instructions.setWordWrap(True)
        card_layout.addWidget(instructions)
        
        layout.addWidget(card)
        
        path_group = QtWidgets.QGroupBox("Source Path")
        path_layout = QtWidgets.QHBoxLayout(path_group)
        
        self.wwise_path_edit_old = QtWidgets.QLineEdit()
        self.wwise_path_edit_old.setPlaceholderText("Select WWISE folder...")
        
        browse_btn = ModernButton(self.tr("browse"), primary=True)
        browse_btn.clicked.connect(self.select_wwise_folder_old)
        
        path_layout.addWidget(self.wwise_path_edit_old)
        path_layout.addWidget(browse_btn)
        
        layout.addWidget(path_group)
        
        self.process_btn = ModernButton(self.tr("process_wem_files_btn"), primary=True)
        self.process_btn.clicked.connect(self.process_wem_files)
        layout.addWidget(self.process_btn)
        
    
        self.open_target_btn = ModernButton(self.tr("open_target_folder_btn"))
        self.open_target_btn.clicked.connect(self.open_target_folder)
        layout.addWidget(self.open_target_btn)


        self.converter_status_old = QtWidgets.QTextEdit()
        self.converter_status_old.setReadOnly(True)
        layout.addWidget(self.converter_status_old)
        
        self.wem_processor_tabs.addTab(tab, "Process WEM")

    def browse_wwise_path(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose Wwise path",
            self.wwise_path_edit.text() or ""
        )
        if folder:
            self.wwise_path_edit.setText(folder)
            self.settings.data["wav_wwise_path"] = folder
            self.settings.save()
            
            if hasattr(self, 'wav_converter'):
                project_path = self.converter_project_path_edit.text()
                if project_path:
                    self.wav_converter.set_paths(folder, project_path, self.wav_converter.output_folder or tempfile.gettempdir())

    def browse_converter_project_path(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose path for Wwise project",
            self.converter_project_path_edit.text() or ""
        )
        if folder:
            self.converter_project_path_edit.setText(folder)
            self.settings.data["wav_project_path"] = folder
            self.settings.save()
  
            if hasattr(self, 'wav_converter'):
                wwise_path = self.wwise_path_edit.text()
                if wwise_path:
                    self.wav_converter.set_paths(wwise_path, folder, self.wav_converter.output_folder or tempfile.gettempdir())

    def browse_wav_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose folder with Audio files",
            self.wav_folder_edit.text() or ""
        )
        if folder:
            self.wav_folder_edit.setText(folder)
            self.settings.data["wav_folder_path"] = folder
            self.settings.save()

    def clear_conversion_files(self):
        """Clear conversion files list"""
        if self.wav_converter.file_pairs:
            reply = QtWidgets.QMessageBox.question(
                self, self.tr("confirmation"), 
                self.tr("confirm_clear"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply == QtWidgets.QMessageBox.Yes:
                self.wav_converter.clear_file_pairs()
                self.conversion_files_table.setRowCount(0)
                self.update_conversion_files_table()
        self.save_converter_file_list()

    def update_conversion_files_list(self):
        self.conversion_files_list.clear()
        for i, pair in enumerate(self.wav_converter.file_pairs):
            display_text = f"{i+1}. {pair['wav_name']} → {pair['target_name']} ({pair['target_size']:,} bytes)"
            self.conversion_files_list.addItem(display_text)


    def update_conversion_status(self, message, color="green"):
        color_map = {
            "green": "#4CAF50",
            "blue": "#2196F3", 
            "red": "#F44336",
            "orange": "#FF9800"
        }
        self.conversion_status.setText(message)
        self.conversion_status.setStyleSheet(f"color: {color_map.get(color, color)};")


    def select_wwise_folder_old(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select WWISE Folder", 
            self.settings.data.get("last_directory", "")
        )
        
        if folder:
            self.wwise_path_edit_old.setText(folder)
            self.settings.data["last_directory"] = folder
            self.settings.save()
    def update_filter_combo(self, lang):
        widgets = self.tab_widgets[lang]
        filter_combo = widgets["filter_combo"]
        try:
            filter_combo.currentIndexChanged.disconnect()
        except TypeError:
            pass
        current_text = filter_combo.currentText()
        filter_combo.clear()
        filter_combo.addItems([
            self.tr("all_files"), 
            self.tr("with_subtitles"), 
            self.tr("without_subtitles"), 
            self.tr("modified"),
            self.tr("modded")
        ])
        unique_tags = set()
        for entry in self.entries_by_lang.get(lang, []):
            key = os.path.splitext(entry.get("ShortName", ""))[0]
            marking = self.marked_items.get(key, {})
            tag = marking.get('tag')
            if tag:
                unique_tags.add(tag)

        if unique_tags:
            filter_combo.addItem("--- Tags ---")
            for tag in sorted(unique_tags):
                filter_combo.addItem(f"With Tag: {tag}")

        new_index = filter_combo.findText(current_text)
        if new_index >= 0:
            filter_combo.setCurrentIndex(new_index)
        else:
            filter_combo.setCurrentIndex(0)

        filter_combo.currentIndexChanged.connect(lambda: self.populate_tree(lang))
        
   
    def create_language_tab(self, lang):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        
        
        controls = QtWidgets.QWidget()
        controls.setMaximumHeight(40)
        controls_layout = QtWidgets.QHBoxLayout(controls)
        controls_layout.setContentsMargins(5, 5, 5, 5)

        filter_combo = QtWidgets.QComboBox()
        filter_combo.addItems([
            self.tr("all_files"), 
            self.tr("with_subtitles"), 
            self.tr("without_subtitles"), 
            self.tr("modified"),
            self.tr("modded")
        ])
        filter_combo.currentIndexChanged.connect(lambda: self.populate_tree(lang))

        sort_combo = QtWidgets.QComboBox()
        sort_combo.addItems([
            self.tr("name_a_z"), 
            self.tr("name_z_a"), 
            self.tr("id_asc"), 
            self.tr("id_desc"), 
            self.tr("recent_first")
        ])
        sort_combo.currentIndexChanged.connect(lambda: self.populate_tree(lang))
        show_orphans_checkbox = QtWidgets.QCheckBox(self.tr("show_scanned_files_check"))
        show_orphans_checkbox.setToolTip(self.tr("show_scanned_files_tooltip"))
        show_orphans_checkbox.setChecked(self.settings.data.get("show_orphaned_files", False))
        show_orphans_checkbox.stateChanged.connect(self.on_show_orphans_toggled)
        controls_layout.addWidget(QtWidgets.QLabel(self.tr("filter")))
        controls_layout.addWidget(filter_combo)
        controls_layout.addWidget(QtWidgets.QLabel(self.tr("sort")))
        controls_layout.addWidget(sort_combo)
        controls_layout.addWidget(show_orphans_checkbox)
        controls_layout.addStretch()

        stats_label = QtWidgets.QLabel()
        controls_layout.addWidget(stats_label)
        
        layout.addWidget(controls)
        
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        
        tree = AudioTreeWidget(wem_app=self, lang=lang)
        tree.setUniformRowHeights(True)
        tree.setAcceptDrops(True)
        tree.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        tree.viewport().setAcceptDrops(True)
        tree.setColumnCount(5) 
        tree.setHeaderLabels([self.tr("name"), self.tr("id"), self.tr("subtitle"), self.tr("status"), "Tag"])
        tree.setColumnWidth(0, 350)
        tree.setColumnWidth(1, 100)
        tree.setColumnWidth(2, 400)
        tree.setColumnWidth(3, 80)
        tree.setColumnWidth(4, 100)
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: self.show_context_menu(lang, pos))
        tree.itemSelectionChanged.connect(lambda: self.on_selection_changed(lang))
        tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        splitter.addWidget(tree)
        

        details_panel = QtWidgets.QWidget()
        details_layout = QtWidgets.QVBoxLayout(details_panel)
        

        player_widget = QtWidgets.QWidget()
        player_layout = QtWidgets.QVBoxLayout(player_widget)
        

        audio_progress = ClickableProgressBar()
        audio_progress.setTextVisible(False)
        audio_progress.setMaximumHeight(10)
        player_layout.addWidget(audio_progress)
        

        controls_widget = QtWidgets.QWidget()
        controls_layout = QtWidgets.QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        

        play_btn = QtWidgets.QPushButton("▶")
        play_btn.setMaximumWidth(40)
        play_btn.clicked.connect(lambda: self.play_current())
        audio_progress.clicked.connect(self.audio_player.set_position)
        play_mod_btn = QtWidgets.QPushButton(f"▶ {self.tr('mod')}")
        play_mod_btn.setMaximumWidth(60)
        play_mod_btn.setToolTip("Play modified audio if available")
        play_mod_btn.clicked.connect(lambda: self.play_current(play_mod=True))
        play_mod_btn.hide()  
        
        stop_btn = QtWidgets.QPushButton("■")
        stop_btn.setMaximumWidth(40)
        stop_btn.clicked.connect(self.stop_audio)
        

        time_label = QtWidgets.QLabel("00:00 / 00:00")
        time_label.setAlignment(QtCore.Qt.AlignCenter)
        

        size_warning = QtWidgets.QLabel()
        size_warning.setStyleSheet("color: red; font-weight: bold;")
        size_warning.hide()
        
        controls_layout.addWidget(play_btn)
        controls_layout.addWidget(play_mod_btn)
        controls_layout.addWidget(stop_btn)
        controls_layout.addWidget(time_label)
        controls_layout.addWidget(size_warning)
        controls_layout.addStretch()
        
        player_layout.addWidget(controls_widget)
        details_layout.addWidget(player_widget)
        

        subtitle_group = QtWidgets.QGroupBox(self.tr("subtitle_preview"))
        subtitle_layout = QtWidgets.QVBoxLayout(subtitle_group)
        subtitle_group.setMaximumHeight(150)
        subtitle_group.setMaximumWidth(800)
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(80) 
        scroll_area.setMaximumHeight(150) 

        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(5, 5, 5, 5)

        subtitle_text = QtWidgets.QTextEdit()
        subtitle_text.setReadOnly(True)
        subtitle_text.setMinimumHeight(60)
        scroll_layout.addWidget(subtitle_text)

        original_subtitle_label = QtWidgets.QLabel()
        original_subtitle_label.setWordWrap(True)
        original_subtitle_label.setStyleSheet("color: #666; font-style: italic;")
        original_subtitle_label.hide()
        scroll_layout.addWidget(original_subtitle_label)

        scroll_layout.addStretch() 

        scroll_area.setWidget(scroll_content)
        subtitle_layout.addWidget(scroll_area)
        

        original_subtitle_label = QtWidgets.QLabel()
        original_subtitle_label.setWordWrap(True)
        original_subtitle_label.setStyleSheet("color: #666; font-style: italic;")
        original_subtitle_label.hide()
        subtitle_layout.addWidget(original_subtitle_label)
        
        details_layout.addWidget(subtitle_group)
        

        info_group = QtWidgets.QGroupBox(self.tr("file_info"))
        info_layout = QtWidgets.QVBoxLayout(info_group)

        basic_info_widget = QtWidgets.QWidget()
        basic_info_layout = QtWidgets.QFormLayout(basic_info_widget)

        info_labels = {
            "id": QtWidgets.QLabel(),
            "name": QtWidgets.QLabel(),
            "path": QtWidgets.QLabel(),
            "source": QtWidgets.QLabel(),
            "tag": QtWidgets.QLabel()
        }

        basic_info_layout.addRow(f"{self.tr('id')}:", info_labels["id"])
        basic_info_layout.addRow(f"{self.tr('name')}:", info_labels["name"])
        basic_info_layout.addRow(f"{self.tr('path')}:", info_labels["path"])
        basic_info_layout.addRow(f"{self.tr('source')}:", info_labels["source"])
        info_layout.addWidget(basic_info_widget)


        comparison_group = QtWidgets.QGroupBox(self.tr("audio_comparison"))
        comparison_group.setMaximumHeight(220) 
        comparison_group.setMinimumHeight(220) 
        comparison_layout = QtWidgets.QHBoxLayout(comparison_group)

      
        original_widget = QtWidgets.QWidget()
        original_layout = QtWidgets.QVBoxLayout(original_widget)
        original_header = QtWidgets.QLabel(self.tr("original_audio"))
        original_header.setStyleSheet("font-weight: bold; color: #2196F3; padding: 5px;")
        original_layout.addWidget(original_header)

        original_info_layout = QtWidgets.QFormLayout()
        original_info_labels = {
            "duration": QtWidgets.QLabel(),
            "size": QtWidgets.QLabel(),
            "sample_rate": QtWidgets.QLabel(),
            "bitrate": QtWidgets.QLabel(),
            "channels": QtWidgets.QLabel(),
            "bnk_size": QtWidgets.QLabel(),
            "override_fx": QtWidgets.QLabel(),
            "modified_date": QtWidgets.QLabel()
        }

        original_info_layout.addRow(self.tr("duration"), original_info_labels["duration"])
        original_info_layout.addRow(self.tr("size"), original_info_labels["size"])
        original_info_layout.addRow(self.tr("sample_rate"), original_info_labels["sample_rate"])
        original_info_layout.addRow(self.tr("bitrate"), original_info_labels["bitrate"])
        original_info_layout.addRow(self.tr("channels"), original_info_labels["channels"])
        original_info_layout.addRow(self.tr("bnk_size_label"), original_info_labels["bnk_size"])
        original_info_layout.addRow(self.tr("in_game_effects_label"), original_info_labels["override_fx"])
        original_info_layout.addRow(" ", QtWidgets.QWidget())
        original_layout.addLayout(original_info_layout)

     
        modified_widget = QtWidgets.QWidget()
        modified_layout = QtWidgets.QVBoxLayout(modified_widget)
        modified_header = QtWidgets.QLabel(self.tr("modified_audio"))
        modified_header.setStyleSheet("font-weight: bold; color: #4CAF50; padding: 5px;")
        modified_layout.addWidget(modified_header)

        modified_info_layout = QtWidgets.QFormLayout()
        modified_info_labels = {
            "duration": QtWidgets.QLabel(),
            "size": QtWidgets.QLabel(),
            "sample_rate": QtWidgets.QLabel(),
            "bitrate": QtWidgets.QLabel(),
            "channels": QtWidgets.QLabel(), 
            "bnk_size": QtWidgets.QPushButton("N/A"),
            "override_fx": QtWidgets.QLabel(),
            "modified_date": QtWidgets.QLabel()
        }

        modified_info_layout.addRow(f"{self.tr("duration")}", modified_info_labels["duration"])
        modified_info_layout.addRow(f"{self.tr("size")}", modified_info_labels["size"])
        modified_info_layout.addRow(f"{self.tr("sample_rate")}", modified_info_labels["sample_rate"])
        modified_info_layout.addRow(f"{self.tr("bitrate")}", modified_info_labels["bitrate"])
        modified_info_layout.addRow(f"{self.tr("channels")}", modified_info_labels["channels"])
        modified_info_layout.addRow(self.tr("bnk_size_label"), modified_info_labels["bnk_size"])
        modified_info_layout.addRow(self.tr("in_game_effects_label"), modified_info_labels["override_fx"]),
        modified_info_layout.addRow(self.tr("last_modified_label"), modified_info_labels["modified_date"])
        modified_layout.addLayout(modified_info_layout)
        bnk_size_button = modified_info_labels["bnk_size"]
        bnk_size_button.setFlat(True)
        bnk_size_button.setStyleSheet("QPushButton { text-align: left; padding: 0; color: #000; border: none; background: transparent; }")
        bnk_size_button.setCursor(QtCore.Qt.ArrowCursor)
        bnk_size_button.setEnabled(False)
     
        comparison_layout.addWidget(original_widget)

   
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.VLine)
        separator.setFrameShadow(QtWidgets.QFrame.Sunken)
        separator.setStyleSheet("QFrame { color: #cccccc; }")
        comparison_layout.addWidget(separator)

        comparison_layout.addWidget(modified_widget)

        info_layout.addWidget(comparison_group)


        markers_group = QtWidgets.QGroupBox(self.tr("audio_markers"))
        markers_layout = QtWidgets.QVBoxLayout(markers_group)

 
        markers_comparison = QtWidgets.QHBoxLayout()


        original_markers_widget = QtWidgets.QWidget()
        original_markers_layout = QtWidgets.QVBoxLayout(original_markers_widget)

        original_markers_header = QtWidgets.QLabel(self.tr("original_markers"))
        original_markers_header.setStyleSheet("font-weight: bold; color: #2196F3; padding: 2px;")
        original_markers_layout.addWidget(original_markers_header)

        original_markers_list = QtWidgets.QListWidget()
        original_markers_list.setMaximumHeight(120)
        original_markers_list.setAlternatingRowColors(True)
        original_markers_layout.addWidget(original_markers_list)


        modified_markers_widget = QtWidgets.QWidget()
        modified_markers_layout = QtWidgets.QVBoxLayout(modified_markers_widget)

        modified_markers_header = QtWidgets.QLabel(self.tr("modified_markers"))
        modified_markers_header.setStyleSheet("font-weight: bold; color: #4CAF50; padding: 2px;")
        modified_markers_layout.addWidget(modified_markers_header)

        modified_markers_list = QtWidgets.QListWidget()
        modified_markers_list.setMaximumHeight(120)
        modified_markers_list.setAlternatingRowColors(True)
        modified_markers_layout.addWidget(modified_markers_list)

        markers_comparison.addWidget(original_markers_widget)

 
        markers_separator = QtWidgets.QFrame()
        markers_separator.setFrameShape(QtWidgets.QFrame.VLine)
        markers_separator.setFrameShadow(QtWidgets.QFrame.Sunken)
        markers_separator.setStyleSheet("QFrame { color: #cccccc; }")
        markers_comparison.addWidget(markers_separator)

        markers_comparison.addWidget(modified_markers_widget)

        markers_layout.addLayout(markers_comparison)
        info_layout.addWidget(markers_group)

        details_layout.addWidget(info_group)
        details_layout.addStretch()
        
        splitter.addWidget(details_panel)
        splitter.setSizes([700, 300])
        layout.addWidget(splitter)
        

        self.tab_widgets[lang] = {
            "filter_combo": filter_combo,
            "show_orphans_checkbox": show_orphans_checkbox,
            "sort_combo": sort_combo,
            "tree": tree,
            "stats_label": stats_label,
            "subtitle_text": subtitle_text,
            "original_subtitle_label": original_subtitle_label,
            "info_labels": info_labels,
            "original_info_labels": original_info_labels,
            "modified_info_labels": modified_info_labels,
            "original_markers_list": original_markers_list,
            "modified_markers_list": modified_markers_list,
            "details_panel": details_panel,
            "audio_progress": audio_progress,
            "time_label": time_label,
            "play_btn": play_btn,
            "play_mod_btn": play_mod_btn,
            "stop_btn": stop_btn,
            "size_warning": size_warning
        }
        
        self.tabs.addTab(tab, f"{lang} ({len(self.entries_by_lang.get(lang, []))})")
        basic_info_layout.addRow("Tag:", info_labels["tag"])
    def on_show_orphans_toggled(self, state):
        """Handles toggling the 'Show Scanned Files' checkbox."""
        is_checked = (state == QtCore.Qt.Checked)
        
        if self.settings.data.get("show_orphaned_files", True) == is_checked:
            return
        
        self.settings.data["show_orphaned_files"] = is_checked
        self.settings.save()
        DEBUG.log(f"Show orphaned files setting changed to: {is_checked}")

        for lang, widgets in self.tab_widgets.items():
            checkbox = widgets.get("show_orphans_checkbox")
            if checkbox:
                checkbox.blockSignals(True)
                checkbox.setChecked(is_checked)
                checkbox.blockSignals(False)

        self.rebuild_file_list_with_orphans()
    def get_wem_audio_info_with_markers(self, wem_path):
        """Get detailed audio information including markers from WEM file"""
        info = self.get_wem_audio_info(wem_path)
        
        if info is None:
            return None
        

        try:
            analyzer = WEMAnalyzer(wem_path)
            if analyzer.analyze():
                info['markers'] = analyzer.get_markers_info()
       
                if analyzer.sample_rate > 0:
                    info['sample_rate'] = analyzer.sample_rate
            else:
                info['markers'] = []
        except Exception as e:
            DEBUG.log(f"Error analyzing markers: {e}", "ERROR")
            info['markers'] = []
        
        return info

    def format_markers_for_display(self, markers):

        formatted_markers = []
        
        for marker in markers:
   
            if marker['position'] == 0:
                time_str = "Sample 0"
            else:
    
                time_seconds = marker['time_seconds']
                if time_seconds >= 1.0:

                    minutes = int(time_seconds // 60)
                    seconds = time_seconds % 60
                    time_str = f"{minutes:02d}:{seconds:06.3f}"
                else:

                    time_str = f"{time_seconds:.3f}s"
            

            label = marker['label']
            
    
            if label and label != "No label":
                display_text = f"#{marker['id']}: {time_str} - {label}"
            else:
                display_text = f"#{marker['id']}: {time_str}"
            
            formatted_markers.append(display_text)
        
        return formatted_markers
    def get_wem_audio_info(self, wem_path):
        """Get detailed audio information from WEM file"""
        try:
            result = subprocess.run(
                [self.vgmstream_path, "-m", wem_path],
                capture_output=True,
                text=True,
                timeout=10,
                startupinfo=startupinfo,
                creationflags=CREATE_NO_WINDOW,
                encoding='utf-8',
                errors='ignore'
            )
            
            if result.returncode == 0:
                info = {
                    'sample_rate': 0,
                    'channels': 0,
                    'samples': 0,
                    'duration_ms': 0,
                    'bitrate': 0,
                    'format': 'Unknown'
                }
                
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    
                    if "sample rate:" in line:
                        try:
                            info['sample_rate'] = int(line.split(':')[1].strip().split()[0])
                        except:
                            pass
                            
                    elif "channels:" in line:
                        try:
                            info['channels'] = int(line.split(':')[1].strip().split()[0])
                        except:
                            pass
                            
                    elif "stream total samples:" in line:
                        try:
                            info['samples'] = int(line.split(':')[1].strip().split()[0])
                        except:
                            pass
                            
                    elif "encoding:" in line:
                        try:
                            info['format'] = line.split(':')[1].strip()
                        except:
                            pass
                

                if info['sample_rate'] > 0 and info['samples'] > 0:
                    info['duration_ms'] = int((info['samples'] / info['sample_rate']) * 1000)
                    

                    file_size = os.path.getsize(wem_path)
                    if info['duration_ms'] > 0:
                        info['bitrate'] = int((file_size * 8) / (info['duration_ms'] / 1000))
                
                return info
                
        except Exception as e:
            DEBUG.log(f"Error getting audio info: {e}", "ERROR")
            
        return None

    def format_audio_info(self, info, label_suffix=""):
        """Format audio info for display"""
        if not info:
            return {
                f'duration{label_suffix}': "N/A",
                f'size{label_suffix}': "N/A", 
                f'sample_rate{label_suffix}': "N/A",
                f'bitrate{label_suffix}': "N/A",
                f'channels{label_suffix}': "N/A"
            }
        
        # Format duration
        duration_ms = info.get('duration_ms', 0)
        if duration_ms > 0:
            minutes = int(duration_ms // 60000)
            seconds = (duration_ms % 60000) / 1000.0
            duration_str = f"{minutes:02d}:{seconds:05.2f}"
        else:
            duration_str = "Unknown"
        
        # Format sample rate
        sample_rate = info.get('sample_rate', 0)
        if sample_rate > 0:
            if sample_rate >= 1000:
                sample_rate_str = f"{sample_rate/1000:.1f} kHz"
            else:
                sample_rate_str = f"{sample_rate} Hz"
        else:
            sample_rate_str = "Unknown"
        
        # Format bitrate
        bitrate = info.get('bitrate', 0)
        if bitrate > 0:
            if bitrate >= 1000:
                bitrate_str = f"{bitrate/1000:.1f} kbps"
            else:
                bitrate_str = f"{bitrate} bps"
        else:
            bitrate_str = "Unknown"
        
        # Format channels
        channels = info.get('channels', 0)
        if channels == 1:
            channels_str = "Mono"
        elif channels == 2:
            channels_str = "Stereo"
        elif channels > 2:
            channels_str = f"{channels} channels"
        else:
            channels_str = "Unknown"
        
        return {
            f'duration{label_suffix}': duration_str,
            f'sample_rate{label_suffix}': sample_rate_str,
            f'bitrate{label_suffix}': bitrate_str,
            f'channels{label_suffix}': channels_str
        }
    def export_subtitles(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Subtitles", "subtitles_export.json", 
            "JSON Files (*.json);;Text Files (*.txt)"
        )
        
        if path:
            if path.endswith(".json"):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"Subtitles": self.subtitles}, f, ensure_ascii=False, indent=2)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    for key, subtitle in sorted(self.subtitles.items()):
                        f.write(f"{key}: {subtitle}\n")
                        
            self.status_bar.showMessage(f"Exported to {path}", 3000)

    def import_subtitles(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Subtitles", "", "JSON Files (*.json)"
        )
        
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                imported = data.get("Subtitles", {})
                count = len(imported)
                
                reply = QtWidgets.QMessageBox.question(
                    self, "Import Subtitles",
                    f"Import {count} subtitles?\nThis will overwrite existing subtitles.",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                )
                
                if reply == QtWidgets.QMessageBox.Yes:
                    self.subtitles.update(imported)
                    
                    for key, value in imported.items():
                        if key in self.original_subtitles and self.original_subtitles[key] != value:
                            self.modified_subtitles.add(key)
                        else:
                            self.modified_subtitles.discard(key)

                    current_lang = self.get_current_language()
                    if current_lang and current_lang in self.tab_widgets:
                        self.populate_tree(current_lang)
                        
                    self.status_bar.showMessage(f"Imported {count} subtitles", 3000)
                    self.update_status()
                    
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Import Error", str(e))

    def show_shortcuts(self):
        """Show keyboard shortcuts"""
        shortcuts_text = f"""
        <h2>{self.tr("keyboard_shortcuts")}</h2>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
        <tr style="background-color: #f0f0f0;">
            <th>{self.tr("shortcuts_table_action")}</th>
            <th>{self.tr("shortcuts_table_shortcut")}</th>
            <th>{self.tr("shortcuts_table_description")}</th>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_edit_subtitle")}</b></td>
            <td>F2</td>
            <td>{self.tr("shortcut_edit_selected")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_save_subtitles")}</b></td>
            <td>Ctrl+S</td>
            <td>{self.tr("shortcut_save_all_changes")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_export_audio")}</b></td>
            <td>Ctrl+E</td>
            <td>{self.tr("shortcut_export_selected")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_revert_original")}</b></td>
            <td>Ctrl+R</td>
            <td>{self.tr("shortcut_revert_selected")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_deploy_run")}</b></td>
            <td>F5</td>
            <td>{self.tr("shortcut_deploy_launch")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_debug_console")}</b></td>
            <td>Ctrl+D</td>
            <td>{self.tr("shortcut_show_debug")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_settings")}</b></td>
            <td>Ctrl+,</td>
            <td>{self.tr("shortcut_open_settings")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_exit")}</b></td>
            <td>Ctrl+Q</td>
            <td>{self.tr("shortcut_close_app")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_play_original_action")}</b></td>
            <td>Space</td>
            <td>{self.tr("shortcut_play_original_desc")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_play_mod_action")}</b></td>
            <td>Ctrl+Space</td>
            <td>{self.tr("shortcut_play_mod_desc")}</td>
        </tr>
        <tr>
            <td><b>{self.tr("shortcut_delete_mod_action")}</b></td>
            <td>Delete</td>
            <td>{self.tr("shortcut_delete_mod_desc")}</td>
        </tr>
        </table>

        <h3>{self.tr("mouse_actions")}</h3>
        <ul>
            <li>{self.tr("mouse_double_subtitle")}</li>
            <li>{self.tr("mouse_double_file")}</li>
            <li>{self.tr("mouse_right_click")}</li>
        </ul>
        """
        
        msg = QtWidgets.QMessageBox()
        msg.setWindowTitle("Keyboard Shortcuts")
        msg.setTextFormat(QtCore.Qt.RichText)
        msg.setText(shortcuts_text)
        msg.setStandardButtons(QtWidgets.QMessageBox.Ok)
        msg.exec_()
    def check_updates_on_startup(self):
        thread = threading.Thread(target=self._check_updates_thread, args=(True,))
        thread.daemon = True
        thread.start()

    def check_updates(self):
        self.statusBar().showMessage("Checking for updates...")
        
        thread = threading.Thread(target=self._check_updates_thread, args=(False,))
        thread.daemon = True
        thread.start()

    def _check_updates_thread(self, silent=False):
        try:
            repo_url = "https://api.github.com/repos/Bezna/OutlastTrials_AudioEditor/releases/latest"
            
            response = requests.get(repo_url, timeout=10)
            response.raise_for_status()
            
            release_data = response.json()
            latest_version = release_data['tag_name'].lstrip('v')
            download_url = release_data['html_url']
            release_notes = release_data.get('body', 'No release notes available.')

            if version.parse(latest_version) > version.parse(current_version):
                QtCore.QMetaObject.invokeMethod(
                    self, "_show_update_available",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, latest_version),
                    QtCore.Q_ARG(str, download_url),
                    QtCore.Q_ARG(str, release_notes),
                    QtCore.Q_ARG(bool, silent)
                )
            else:
                if not silent:
                    QtCore.QMetaObject.invokeMethod(
                        self, "_show_up_to_date",
                        QtCore.Qt.QueuedConnection
                    )
                else:

                    QtCore.QMetaObject.invokeMethod(
                        self, "_update_status_silent",
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(str, "")
                    )
                    
        except requests.exceptions.RequestException as e:

            if not silent:
                QtCore.QMetaObject.invokeMethod(
                    self, "_show_network_error",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, str(e))
                )
            else:
                QtCore.QMetaObject.invokeMethod(
                    self, "_update_status_silent",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "")
                )
        except Exception as e:
 
            if not silent:
                QtCore.QMetaObject.invokeMethod(
                    self, "_show_error",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, str(e))
                )
            else:
                QtCore.QMetaObject.invokeMethod(
                    self, "_update_status_silent",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "")
                )
    @QtCore.pyqtSlot(str, str, str, bool)
    def _show_update_available(self, latest_version, download_url, release_notes, silent=False):
        """Show update available dialog"""
        self.statusBar().showMessage("Update available!")
        
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Update Available")
        msg.setIcon(QtWidgets.QMessageBox.Information)
        
        text = f"""New version available: v{latest_version}
    Current version: {current_version}

    Release Notes:
    {release_notes[:300]}{'...' if len(release_notes) > 300 else ''}

    Do you want to download the update?"""
        
        msg.setText(text)
        msg.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        
        if msg.exec_() == QtWidgets.QMessageBox.Yes:
            import webbrowser
            webbrowser.open(download_url)
    @QtCore.pyqtSlot()
    def _show_up_to_date(self):
        """Show up to date message"""
        self.statusBar().showMessage("You are running the latest version.")
        
        QtWidgets.QMessageBox.information(
            self, "Check for Updates",
            "You are running OutlastTrials AudioEditor " + current_version + "\n\n"
            "This is the latest version!"
        )

    @QtCore.pyqtSlot(str)
    def _show_network_error(self, error):
        """Show network error"""
        self.statusBar().showMessage("Failed to check for updates.")
        
        QtWidgets.QMessageBox.warning(
            self, "Update Check Failed",
            f"Failed to check for updates.\n\n"
            f"Please check your internet connection and try again.\n\n"
            f"Error: {error}\n\n"
            f"You can manually check for updates at:\n"
            f"https://github.com/Bezna/OutlastTrials_AudioEditor"
        )

    @QtCore.pyqtSlot(str)
    def _show_error(self, error):
        """Show general error"""
        self.statusBar().showMessage("Error checking for updates.")
        
        QtWidgets.QMessageBox.critical(
            self, "Error",
            f"An error occurred while checking for updates:\n\n{error}"
        )
    @QtCore.pyqtSlot(str)
    def _update_status_silent(self, message):
        """Silently update status bar"""
        if message:
            self.statusBar().showMessage(message)
        else:
            self.statusBar().clearMessage()
    def report_bug(self):
        """Show bug report dialog"""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self.tr("report_bug"))
        dialog.setMinimumSize(500, 400)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        info_label = QtWidgets.QLabel(self.tr("bug_report_info"))
        layout.addWidget(info_label)
        
        desc_label = QtWidgets.QLabel(f"{self.tr('description')}:")
        layout.addWidget(desc_label)
        
        desc_text = QtWidgets.QTextEdit()
        desc_text.setPlaceholderText(
            "Please describe:\n"
            "1. What you were trying to do\n"
            "2. What happened instead\n"
            "3. Steps to reproduce the issue"
        )
        layout.addWidget(desc_text)
        
        email_label = QtWidgets.QLabel(f"{self.tr('email_optional')}:")
        layout.addWidget(email_label)
        
        email_edit = QtWidgets.QLineEdit()
        email_edit.setPlaceholderText("your@email.com")
        layout.addWidget(email_edit)
        
        btn_layout = QtWidgets.QHBoxLayout()
        
        copy_btn = QtWidgets.QPushButton(self.tr("copy_report_clipboard"))
        send_btn = QtWidgets.QPushButton(self.tr("open_github_issues"))
        cancel_btn = QtWidgets.QPushButton(self.tr("cancel"))
        
        def copy_report():
            report = f"""
    BUG REPORT - OutlastTrials AudioEditor {current_version}
    ==========================================
    Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    Email: {email_edit.text() or 'Not provided'}

    Description:
    {desc_text.toPlainText()}

    System Info:
    - OS: {sys.platform}
    - Python: {sys.version.split()[0]}
    - PyQt5: {QtCore.PYQT_VERSION_STR}

    Debug Log (last 50 lines):
    {chr(10).join(DEBUG.logs[-50:])}
    """
            QtWidgets.QApplication.clipboard().setText(report)
            QtWidgets.QMessageBox.information(dialog, "Success", "Bug report copied to clipboard!")
        
        def open_github():
            import webbrowser
            webbrowser.open("https://github.com/Bezna/OutlastTrials_AudioEditor/issues")
        
        copy_btn.clicked.connect(copy_report)
        send_btn.clicked.connect(open_github)
        cancel_btn.clicked.connect(dialog.reject)
        
        btn_layout.addWidget(copy_btn)
        btn_layout.addWidget(send_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dialog.exec_()

    def show_about(self):
        """Show about dialog with animations"""
        about_dialog = QtWidgets.QDialog(self)
        about_dialog.setWindowTitle(self.tr("about") + " OutlastTrials AudioEditor")
        about_dialog.setMinimumSize(600, 500)
        
        layout = QtWidgets.QVBoxLayout(about_dialog)

        header_widget = QtWidgets.QWidget()
        header_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0078d4, stop:1 #106ebe);
                border-radius: 5px;
            }
        """)
        header_layout = QtWidgets.QVBoxLayout(header_widget)
        
        title_label = QtWidgets.QLabel("OutlastTrials AudioEditor")
        title_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 28px;
                font-weight: bold;
                background: transparent;
            }
            QLabel:hover {
                color: #ffff99;
            }
        """)
        title_label.setAlignment(QtCore.Qt.AlignCenter)
        title_label.setCursor(QtCore.Qt.PointingHandCursor)
        
        title_label.mousePressEvent = lambda event: self.show_secret_easter_egg()
        
        version_label = QtWidgets.QLabel("Version " + current_version)
        version_label.setStyleSheet("""
            QLabel {
                color: #e0e0e0;
                font-size: 16px;
                background: transparent;
            }
        """)
        version_label.setAlignment(QtCore.Qt.AlignCenter)
        
        header_layout.addWidget(title_label)
        header_layout.addWidget(version_label)
        header_widget.setFixedHeight(120)
        
        layout.addWidget(header_widget)

        about_tabs = QtWidgets.QTabWidget()

        about_content = QtWidgets.QTextBrowser()
        about_content.setOpenExternalLinks(True)
        about_content.setHtml(f"""
        <div style="padding: 20px;">
        <p style="font-size: 14px; line-height: 1.6;">
        {self.tr("about_description")}
        </p>

        <h3>{self.tr("key_features")}</h3>
        <ul style="line-height: 1.8;">
            <li>{self.tr("audio_management")}</li>
            <li>{self.tr("subtitle_editing")}</li>
            <li>{self.tr("mod_creation")}</li>
            <li>{self.tr("multi_language")}</li>
            <li>{self.tr("modern_ui")}</li>
        </ul>

        <h3>{self.tr("technology_stack")}</h3>
        <p>{self.tr("built_with")}</p>
        <ul>
            <li>{self.tr("unreal_locres_tool")}</li>
            <li>{self.tr("vgmstream_tool")}</li>
            <li>{self.tr("repak_tool")}</li>
            <li>{self.tr("ffmpeg_tool")}</li>
        </ul>
        </div>
        """)
        about_tabs.addTab(about_content, self.tr("about"))

        credits_content = QtWidgets.QTextBrowser()
        credits_content.setHtml(f"""
        <div style="padding: 20px;">
        <h3>{self.tr("development_team")}</h3>
        <p><b>Developer:</b> Bezna</p>        
        <p>Tester/Polish Translator: Alaneg</p>
        <p>Tester/Mexican Spanish Translator: Mercedes</p>
        
        <h3>Special Thanks</h3>
        <ul>
            <li>vgmstream team - For audio conversion tools</li>
            <li>UnrealLocres developers - For localization support</li>
            <li>hypermetric - For mod packaging</li>
            <li>FFmpeg - For audio processing</li>
            <li>Red Barrels - For creating Outlast Trials</li>
        </ul>
        
        <h3>Open Source Libraries</h3>
        <ul>
            <li>PyQt5 - GUI Framework</li>
            <li>Python Standard Library</li>
        </ul>
        
        <p style="margin-top: 30px; color: #666;">
        This software is provided "as is" without warranty of any kind.
        Use at your own risk.
        </p>
        </div>
        """)
        about_tabs.addTab(credits_content, self.tr("credits"))
        
        license_content = QtWidgets.QTextBrowser()
        license_content.setHtml(f"""
        <div style="padding: 20px;">
        <h3>{self.tr("license_agreement")}</h3>
        <p>Copyright (c) 2026 OutlastTrials AudioEditor</p>
        
        <p>Permission is hereby granted, free of charge, to any person obtaining a copy
        of this software and associated documentation files (the "Software"), to deal
        in the Software without restriction, including without limitation the rights
        to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
        copies of the Software, and to permit persons to whom the Software is
        furnished to do so, subject to the following conditions:</p>
        
        <p>The above copyright notice and this permission notice shall be included in all
        copies or substantial portions of the Software.</p>
        
        <p>THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
        IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
        FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
        AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
        LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
        OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
        SOFTWARE.</p>
        </div>
        """)
        about_tabs.addTab(license_content, self.tr("license"))
        
        layout.addWidget(about_tabs)
        
        footer_widget = QtWidgets.QWidget()
        footer_layout = QtWidgets.QHBoxLayout(footer_widget)
        
        github_btn = QtWidgets.QPushButton("GitHub")
        discord_btn = QtWidgets.QPushButton("Discord")
        donate_btn = QtWidgets.QPushButton(self.tr("donate"))
        
        github_btn.clicked.connect(lambda: QtWidgets.QMessageBox.information(self, "GitHub", "https://github.com/Bezna/OutlastTrials_AudioEditor"))
        discord_btn.clicked.connect(lambda: QtWidgets.QMessageBox.information(self, "Discord", "My Discord: Bezna"))
        donate_btn.clicked.connect(lambda: QtWidgets.QMessageBox.information(self, "Donate", "https://www.donationalerts.com/r/bezna_"))
        
        footer_layout.addWidget(github_btn)
        footer_layout.addWidget(discord_btn)
        footer_layout.addWidget(donate_btn)
        footer_layout.addStretch()
        
        close_btn = QtWidgets.QPushButton(self.tr("close"))
        close_btn.clicked.connect(about_dialog.close)
        footer_layout.addWidget(close_btn)
        
        layout.addWidget(footer_widget)
        
        about_dialog.exec_()

    def show_secret_easter_egg(self):
        secret_dialog = QtWidgets.QDialog(self)
        secret_dialog.setWindowTitle("Cat")
        secret_dialog.setFixedSize(450, 500)
        secret_dialog.setModal(True)
        secret_dialog.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.WindowStaysOnTopHint)
        secret_dialog.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ff6b9d, stop:0.5 #c44569, stop:1 #f8b500);
            }
        """)
        
        layout = QtWidgets.QVBoxLayout(secret_dialog)
        layout.setSpacing(15)
        
        title = QtWidgets.QLabel(self.tr("easter_egg_title"))
        title.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 22px;
                font-weight: bold;
                text-align: center;
                background: transparent;
                padding: 10px;
            }
        """)
        title.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title)
        
        image_container = QtWidgets.QWidget()
        image_container.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.1);
                border-radius: 15px;
                padding: 10px;
            }
        """)
        image_layout = QtWidgets.QVBoxLayout(image_container)
        
        cat_image_label = QtWidgets.QLabel()
        cat_image_label.setAlignment(QtCore.Qt.AlignCenter)
        cat_image_label.setMinimumSize(300, 300)
        cat_image_label.setText(self.tr("easter_egg_loading"))
        cat_image_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 16px;
                text-align: center;
                padding: 20px;
            }
        """)
        
        message = QtWidgets.QLabel("Loading...")
        message.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                text-align: center;
                background: transparent;
                padding: 15px;
                line-height: 1.4;
            }
        """)
        message.setAlignment(QtCore.Qt.AlignCenter)
        message.setWordWrap(True)
        
        self.easter_egg_loader = EasterEggLoader(self)
        
        def on_config_loaded(config):
            print(f"Config loaded: {config}")
            message.setText(f"{config.get('message', self.tr('easter_egg_message'))}")
            self.easter_egg_loader.load_image(config.get('easter_egg_image', ''))
            
        def on_image_loaded(pixmap):
            print("Setting pixmap to label...")
            scaled_pixmap = pixmap.scaled(280, 280, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            cat_image_label.setPixmap(scaled_pixmap)
            cat_image_label.setText("")
            print("Pixmap set successfully!")
            
        def on_loading_failed(error):
            print(f"Loading failed: {error}")
            message.setText(f"{self.tr('easter_egg_message')}")
            cat_image_label.setStyleSheet("""
                QLabel {
                    color: #ffaaaa;
                    font-size: 14px;
                    text-align: center;
                    padding: 40px;
                }
            """)
        
        self.easter_egg_loader.config_loaded.connect(on_config_loaded)
        self.easter_egg_loader.image_loaded.connect(on_image_loaded)
        self.easter_egg_loader.loading_failed.connect(on_loading_failed)
        
        self.easter_egg_loader.load_config()
        
        image_layout.addWidget(cat_image_label)
        layout.addWidget(image_container)
        layout.addWidget(message)
        
        
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.9);
                color: #333;
                border: none;
                border-radius: 20px;
                padding: 12px 30px;
                font-weight: bold;
                font-size: 14px;
                margin: 10px;
            }
            QPushButton:hover {
                background: white;
            }
            QPushButton:pressed {
                background: #f0f0f0;
            }
        """)
        
        close_btn.clicked.connect(secret_dialog.close)
        
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(close_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        self.animate_easter_egg(secret_dialog)
        
        secret_dialog.exec_()
    def animate_easter_egg(self, dialog):
        dialog.setWindowOpacity(0.0)
        dialog.show()
        
        self.fade_animation = QtCore.QPropertyAnimation(dialog, b"windowOpacity")
        self.fade_animation.setDuration(500)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.start()
    def restore_window_state(self):
        if self.settings.data.get("window_geometry"):
            try:
                geometry = bytes.fromhex(self.settings.data["window_geometry"])
                self.restoreGeometry(geometry)
            except:
                self.resize(1400, 800)
        else:
            self.resize(1400, 800)

    def closeEvent(self, event):
        DEBUG.log("=== Application Closing ===")

        if hasattr(self, 'updater_thread') and self.updater_thread and self.updater_thread.isRunning():
            reply = QtWidgets.QMessageBox.question(
                self, 
                self.tr("update_in_progress_title"),
                self.tr("confirm_exit_during_update_message"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply == QtWidgets.QMessageBox.No:
                event.ignore()
                return
            else:
                self.updater_thread.cancel()
                self.updater_thread.wait(5000)
                DEBUG.log("Update process cancelled due to application exit.")
        
        if self.auto_save_timer.isActive():
            self.auto_save_timer.stop()
            DEBUG.log("Auto-save timer stopped on close")
        
        self.settings.data["window_geometry"] = self.saveGeometry().toHex().data().decode()
        saved_markings = {}
        for key, data in self.marked_items.items():
            saved_data = {}
            if 'color' in data and data['color']:
                saved_data['color'] = data['color'].name()
            if 'tag' in data:
                saved_data['tag'] = data['tag']
            if saved_data:
                saved_markings[key] = saved_data
        self.settings.data["marked_items"] = saved_markings
        self.settings.save()
        
        if self.modified_subtitles:
            reply = QtWidgets.QMessageBox.question(
                self, self.tr("save_changes_question"),
                self.tr("unsaved_changes_message"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel
            )
            
            if reply == QtWidgets.QMessageBox.Cancel:
                event.ignore()
                return
            elif reply == QtWidgets.QMessageBox.Yes:
                self.save_subtitles_to_file()
        self.save_converter_file_list()        
        self.stop_audio()
        self.audio_player.stop()
        if hasattr(self, 'wav_converter'):
             self.wav_converter.stop_conversion()

        for f in getattr(self, 'temp_files_to_cleanup', []):
            try: os.remove(f)
            except: pass
        event.accept()

