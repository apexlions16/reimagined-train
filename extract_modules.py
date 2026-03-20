"""
OutlastTrialsAudioEditor.py modular extraction script.
Splits the monolithic file into the editor/ package structure.
"""
import os
import re

SOURCE = "OutlastTrialsAudioEditor.py"

print(f"Reading {SOURCE}...")
with open(SOURCE, "r", encoding="utf-8") as f:
    content = f.read()
    lines = content.splitlines(keepends=True)

total_lines = len(lines)
print(f"Total lines: {total_lines}")

def write_file(path, content_str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content_str)
    print(f"  Written: {path}")

def get_lines(start, end):
    """1-indexed, inclusive"""
    return "".join(lines[start-1:end])

# ============================================================
# Find class/function boundaries dynamically
# ============================================================
def find_class_start(class_name):
    pattern = re.compile(rf"^class {re.escape(class_name)}\b", re.MULTILINE)
    for i, line in enumerate(lines, 1):
        if pattern.match(line.rstrip()):
            return i
    return None

def find_next_class_or_def(from_line, stop_classes):
    """Find the line number of the next top-level class in stop_classes after from_line"""
    pattern = re.compile(r"^class \w+")
    for i in range(from_line, len(lines)):
        line = lines[i].rstrip()
        if pattern.match(line):
            for cls in stop_classes:
                if line == f"class {cls}:" or line.startswith(f"class {cls}("):
                    return i + 1  # 1-indexed
    return len(lines) + 1

def find_class_end(start_line, next_starts):
    """Return end line (1-indexed) = one line before the next class starts"""
    mins = [s for s in next_starts if s > start_line]
    if mins:
        return min(mins) - 1
    return len(lines)

# ---- Detect all top-level class positions ----
class_positions = {}  # class_name -> start_line (1-indexed)
top_level_class_pattern = re.compile(r"^class (\w+)")
for i, line in enumerate(lines, 1):
    m = top_level_class_pattern.match(line)
    if m:
        class_positions[m.group(1)] = i

print(f"Found {len(class_positions)} top-level classes: {list(class_positions.keys())}")

all_class_starts = sorted(class_positions.values())

def class_block(class_name):
    start = class_positions.get(class_name)
    if start is None:
        print(f"  WARNING: class {class_name} not found!")
        return ""
    next_starts = [s for s in all_class_starts if s > start]
    end = min(next_starts) - 1 if next_starts else len(lines)
    return "".join(lines[start-1:end])

def multi_class_block(*class_names):
    """Extract from first class start to end of last class"""
    starts = [class_positions[n] for n in class_names if n in class_positions]
    if not starts:
        return ""
    first_start = min(starts)
    last_start = max(starts)
    next_after_last = [s for s in all_class_starts if s > last_start]
    end = min(next_after_last) - 1 if next_after_last else len(lines)
    return "".join(lines[first_start-1:end])

# ============================================================
# HEADER (imports + constants + TRANSLATIONS)
# ============================================================
# Lines 1-51: imports and constants (before TRANSLATIONS dict)
# Find where TRANSLATIONS starts
translations_start = None
for i, line in enumerate(lines, 1):
    if "TRANSLATIONS = {" in line:
        translations_start = i
        break
print(f"TRANSLATIONS dict starts at line: {translations_start}")

imports_block = "".join(lines[0:translations_start-1])

# Find where TRANSLATIONS ends (next top-level class or function)
translations_end = None
# Find "def tr(" function that follows TRANSLATIONS dict
tr_func_line = None
for i, line in enumerate(lines[translations_start:], translations_start+1):
    if re.match(r"^def tr\(", line):
        tr_func_line = i
        break
    if re.match(r"^class \w+", line):
        break

# Find the end of TRANSLATIONS dict - it's a huge dict literal
# We find the line just before first class
first_class_line = all_class_starts[0]
translations_block = "".join(lines[translations_start-1:first_class_line-1])

# ============================================================
# Create directory structure
# ============================================================
dirs = [
    "editor",
    "editor/translations",
    "editor/core",
    "editor/threads",
    "editor/dialogs",
    "editor/widgets",
]
for d in dirs:
    os.makedirs(d, exist_ok=True)
    print(f"  Created dir: {d}")

# ============================================================
# 1. editor/constants.py
# ============================================================
constants_content = '''"""
Constants and shared namedtuples for OutlastTrials AudioEditor.
"""
import sys
import subprocess
from collections import namedtuple

CuePoint = namedtuple('CuePoint', ['id', 'position', 'chunk_id', 'chunk_start', 'block_start', 'sample_offset'])
Label = namedtuple('Label', ['id', 'text'])

if sys.platform == "win32":
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    CREATE_NO_WINDOW = 0x08000000
else:
    startupinfo = None
    CREATE_NO_WINDOW = 0

current_version = "v1.1.2"

try:
    import numpy as np
    import scipy.io.wavfile as wavfile
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
'''
write_file("editor/constants.py", constants_content)

# ============================================================
# 2. editor/translations/ - split by language
# ============================================================
# The TRANSLATIONS dict has "en", "ru", "es_MX", "pl", "tr" etc.
# We'll extract the whole dict and put it in translations/__init__.py

translations_init = f'''"""
Translations package for OutlastTrials AudioEditor.
Imports all language dicts and provides the tr() function.
"""
from .en import EN_TRANSLATIONS
from .ru import RU_TRANSLATIONS
from .es import ES_TRANSLATIONS
from .pl import PL_TRANSLATIONS

# Try to import optional language packs
try:
    from .tr import TR_TRANSLATIONS
except ImportError:
    TR_TRANSLATIONS = {{}}

TRANSLATIONS = {{
    "en": EN_TRANSLATIONS,
    "ru": RU_TRANSLATIONS,
    "es_MX": ES_TRANSLATIONS,
    "pl": PL_TRANSLATIONS,
    "tr": TR_TRANSLATIONS,
}}


def tr(key, lang="en", **kwargs):
    """Translate a key to the given language."""
    lang_dict = TRANSLATIONS.get(lang, TRANSLATIONS.get("en", {{}}))
    text = lang_dict.get(key, TRANSLATIONS.get("en", {{}}).get(key, key))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text
'''

# Now extract each language sub-dict from the TRANSLATIONS variable in the source
# Find "en": {, "ru": {, etc. positions
def extract_lang_dict(lang_key):
    """Extract the dict for a given language key from the full content"""
    # Find the language key in the TRANSLATIONS dict
    pattern = re.compile(rf'^\s+"{lang_key}":\s*{{', re.MULTILINE)
    m = pattern.search(content)
    if not m:
        print(f"  WARNING: Language '{lang_key}' not found in TRANSLATIONS!")
        return "{}"
    
    start_pos = m.start()
    # Find opening brace
    brace_pos = content.index("{", m.start() + len(m.group()) - 1)
    
    # Count braces to find the end
    depth = 0
    i = brace_pos
    while i < len(content):
        c = content[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end_pos = i + 1
                break
        i += 1
    
    dict_str = content[brace_pos:end_pos]
    return dict_str

for lang_key, lang_code, var_name in [
    ("en", "en", "EN_TRANSLATIONS"),
    ("ru", "ru", "RU_TRANSLATIONS"),
    ("es_MX", "es", "ES_TRANSLATIONS"),
    ("pl", "pl", "PL_TRANSLATIONS"),
    ("tr", "tr", "TR_TRANSLATIONS"),
]:
    lang_dict = extract_lang_dict(lang_key)
    lang_content = f'"""\n{lang_key} translations for OutlastTrials AudioEditor.\n"""\n\n{var_name} = {lang_dict}\n'
    write_file(f"editor/translations/{lang_code}.py", lang_content)

write_file("editor/translations/__init__.py", translations_init)

# ============================================================
# COMMON IMPORTS for modules
# ============================================================
COMMON_IMPORTS = '''import sys
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
'''

# ============================================================
# 3. Core modules
# ============================================================

# editor/core/settings.py - AppSettings
settings_block = class_block("AppSettings")
write_file("editor/core/settings.py", f'"""\nAppSettings - Application settings management.\n"""\n{COMMON_IMPORTS}\n\n{settings_block}')

# editor/core/debug.py - DebugLogger
debug_block = class_block("DebugLogger")
write_file("editor/core/debug.py", f'"""\nDebugLogger - Debug logging utility.\n"""\n{COMMON_IMPORTS}\n\n{debug_block}')

# editor/core/audio_player.py - AudioPlayer
audio_player_block = class_block("AudioPlayer")
write_file("editor/core/audio_player.py", f'"""\nAudioPlayer - Audio playback using QMediaPlayer.\n"""\n{COMMON_IMPORTS}\n\n{audio_player_block}')

# editor/core/wem_analyzer.py - WEMAnalyzer
wem_analyzer_block = class_block("WEMAnalyzer")
write_file("editor/core/wem_analyzer.py", f'"""\nWEMAnalyzer - WEM file analysis.\n"""\n{COMMON_IMPORTS}\n\n{wem_analyzer_block}')

# editor/core/bnk_editor.py - SoundEntry + BNKEditor
bnk_block = multi_class_block("SoundEntry", "BNKEditor")
write_file("editor/core/bnk_editor.py", f'"""\nBNK File Editor - SoundEntry and BNKEditor classes.\n"""\n{COMMON_IMPORTS}\n\n{bnk_block}')

# editor/core/locres_manager.py - UnrealLocresManager
locres_block = class_block("UnrealLocresManager")
write_file("editor/core/locres_manager.py", f'"""\nUnrealLocresManager - .locres file management.\n"""\n{COMMON_IMPORTS}\n\n{locres_block}')

# editor/core/audio_converter.py - AudioToWavConverter
audio_conv_block = class_block("AudioToWavConverter")
write_file("editor/core/audio_converter.py", f'"""\nAudioToWavConverter - Audio to WAV conversion.\n"""\n{COMMON_IMPORTS}\n\n{audio_conv_block}')

# editor/core/volume_processor.py - VolumeProcessor
vol_proc_block = class_block("VolumeProcessor")
write_file("editor/core/volume_processor.py", f'"""\nVolumeProcessor - Audio volume processing.\n"""\n{COMMON_IMPORTS}\n\n{vol_proc_block}')

# editor/core/wav_to_wem.py - WavToWemConverter
wav_wem_block = class_block("WavToWemConverter")
write_file("editor/core/wav_to_wem.py", f'"""\nWavToWemConverter - WAV to WEM conversion via Wwise.\n"""\n{COMMON_IMPORTS}\n\n{wav_wem_block}')

write_file("editor/core/__init__.py", '"""Core modules for OutlastTrials AudioEditor."""\n')

# ============================================================
# 4. Thread modules
# ============================================================

# editor/threads/subtitle_loader.py
sub_loader_block = class_block("SubtitleLoaderThread")
write_file("editor/threads/subtitle_loader.py", f'"""\nSubtitleLoaderThread - Loads subtitle files in background.\n"""\n{COMMON_IMPORTS}\n\n{sub_loader_block}')

# editor/threads/bnk_info_loader.py
bnk_info_block = class_block("BnkInfoLoader")
write_file("editor/threads/bnk_info_loader.py", f'"""\nBnkInfoLoader - Loads BNK info in background.\n"""\n{COMMON_IMPORTS}\n\n{bnk_info_block}')

# editor/threads/wem_scanner.py
wem_scan_block = class_block("WemScannerThread")
write_file("editor/threads/wem_scanner.py", f'"""\nWemScannerThread - Scans for WEM files in background.\n"""\n{COMMON_IMPORTS}\n\n{wem_scan_block}')

# editor/threads/resource_updater.py
res_upd_block = class_block("ResourceUpdaterThread")
write_file("editor/threads/resource_updater.py", f'"""\nResourceUpdaterThread - Updates game resources in background.\n"""\n{COMMON_IMPORTS}\n\n{res_upd_block}')

# editor/threads/save_subtitles.py
save_subs_block = class_block("SaveSubtitlesThread")
write_file("editor/threads/save_subtitles.py", f'"""\nSaveSubtitlesThread - Saves subtitles in background.\n"""\n{COMMON_IMPORTS}\n\n{save_subs_block}')

# editor/threads/import_mod.py
import_mod_block = class_block("ImportModThread")
write_file("editor/threads/import_mod.py", f'"""\nImportModThread - Imports mod files in background.\n"""\n{COMMON_IMPORTS}\n\n{import_mod_block}')

# editor/threads/compile_mod.py
compile_mod_block = class_block("CompileModThread")
write_file("editor/threads/compile_mod.py", f'"""\nCompileModThread - Compiles mod in background.\n"""\n{COMMON_IMPORTS}\n\n{compile_mod_block}')

# editor/threads/file_threads.py - AddFilesThread, AddSingleFileThread, DropFilesThread
file_threads_block = multi_class_block("AddFilesThread", "AddSingleFileThread", "DropFilesThread")
write_file("editor/threads/file_threads.py", f'"""\nFile operation threads - Adding and dropping files.\n"""\n{COMMON_IMPORTS}\n\n{file_threads_block}')

write_file("editor/threads/__init__.py", '"""Thread modules for OutlastTrials AudioEditor."""\n')

# ============================================================
# 5. Dialog modules
# ============================================================

# editor/dialogs/subtitle_editor.py
sub_ed_block = class_block("SubtitleEditor")
write_file("editor/dialogs/subtitle_editor.py", f'"""\nSubtitleEditor - Subtitle editing dialog.\n"""\n{COMMON_IMPORTS}\n\n{sub_ed_block}')

# editor/dialogs/volume_editor.py
vol_ed_block = class_block("WemVolumeEditDialog")
write_file("editor/dialogs/volume_editor.py", f'"""\nWemVolumeEditDialog - WEM volume editing dialog.\n"""\n{COMMON_IMPORTS}\n\n{vol_ed_block}')

# editor/dialogs/batch_volume.py
batch_vol_block = class_block("BatchVolumeEditDialog")
write_file("editor/dialogs/batch_volume.py", f'"""\nBatchVolumeEditDialog - Batch volume editing dialog.\n"""\n{COMMON_IMPORTS}\n\n{batch_vol_block}')

# editor/dialogs/audio_trim.py - AudioTrimDialog + WaveformWidget
audio_trim_block = multi_class_block("WaveformWidget", "AudioTrimDialog")
write_file("editor/dialogs/audio_trim.py", f'"""\nAudioTrimDialog and WaveformWidget - Audio trimming dialog.\n"""\n{COMMON_IMPORTS}\n\n{audio_trim_block}')

# editor/dialogs/debug_window.py
debug_win_block = class_block("DebugWindow")
write_file("editor/dialogs/debug_window.py", f'"""\nDebugWindow - Debug console window.\n"""\n{COMMON_IMPORTS}\n\n{debug_win_block}')

# editor/dialogs/statistics.py
stats_block = class_block("StatisticsDialog")
write_file("editor/dialogs/statistics.py", f'"""\nStatisticsDialog - Project statistics dialog.\n"""\n{COMMON_IMPORTS}\n\n{stats_block}')

# editor/dialogs/profile_dialog.py
prof_block = class_block("ProfileDialog")
write_file("editor/dialogs/profile_dialog.py", f'"""\nProfileDialog - Mod profile editing dialog.\n"""\n{COMMON_IMPORTS}\n\n{prof_block}')

# editor/dialogs/profile_manager.py
prof_mgr_block = class_block("ProfileManagerDialog")
write_file("editor/dialogs/profile_manager.py", f'"""\nProfileManagerDialog - Mod profile manager dialog.\n"""\n{COMMON_IMPORTS}\n\n{prof_mgr_block}')

# editor/dialogs/progress.py
prog_block = class_block("ProgressDialog")
write_file("editor/dialogs/progress.py", f'"""\nProgressDialog - Progress indicator dialog.\n"""\n{COMMON_IMPORTS}\n\n{prog_block}')

write_file("editor/dialogs/__init__.py", '"""Dialog modules for OutlastTrials AudioEditor."""\n')

# ============================================================
# 6. Widget modules
# ============================================================

modern_btn_block = class_block("ModernButton")
write_file("editor/widgets/modern_button.py", f'"""\nModernButton - Custom styled button widget.\n"""\n{COMMON_IMPORTS}\n\n{modern_btn_block}')

audio_tree_block = class_block("AudioTreeWidget")
write_file("editor/widgets/audio_tree.py", f'"""\nAudioTreeWidget - Audio file tree widget.\n"""\n{COMMON_IMPORTS}\n\n{audio_tree_block}')

search_bar_block = class_block("SearchBar")
write_file("editor/widgets/search_bar.py", f'"""\nSearchBar - Search bar widget.\n"""\n{COMMON_IMPORTS}\n\n{search_bar_block}')

click_widgets_block = multi_class_block("ClickableProgressBar", "ClickableLabel")
write_file("editor/widgets/clickable_widgets.py", f'"""\nClickableProgressBar and ClickableLabel - Clickable UI widgets.\n"""\n{COMMON_IMPORTS}\n\n{click_widgets_block}')

easter_egg_block = class_block("EasterEggLoader")
write_file("editor/widgets/easter_egg.py", f'"""\nEasterEggLoader - Easter egg feature.\n"""\n{COMMON_IMPORTS}\n\n{easter_egg_block}')

write_file("editor/widgets/__init__.py", '"""Widget modules for OutlastTrials AudioEditor."""\n')

# ============================================================
# 7. editor/app.py - WemSubtitleApp (main window)
# ============================================================
app_start = class_positions.get("WemSubtitleApp")
if app_start:
    # Find end: next class after WemSubtitleApp
    next_after_app = [s for s in all_class_starts if s > app_start]
    app_end = min(next_after_app) - 1 if next_after_app else len(lines)
    wem_app_block = "".join(lines[app_start-1:app_end])
    write_file("editor/app.py", f'''\"""
WemSubtitleApp - Main application window.
\"""
{COMMON_IMPORTS}
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

{wem_app_block}
''')
else:
    print("ERROR: WemSubtitleApp not found!")

# ============================================================
# 8. editor/__init__.py
# ============================================================
write_file("editor/__init__.py", '''"""
OutlastTrials AudioEditor - editor package.
Import WemSubtitleApp from editor.app to launch the application.
"""
''')

# ============================================================
# 9. main.py - Entry point
# ============================================================
# Extract everything from after WemSubtitleApp class to end of file
# This includes: EasterEggLoader, global_exception_handler, CompileModThread,
# AddFilesThread, AddSingleFileThread, DropFilesThread, and main()

# Find main() function
main_func_line = None
for i, line in enumerate(lines, 1):
    if re.match(r"^def main\(\)", line.rstrip()):
        main_func_line = i
        break

# Find global_exception_handler
geh_line = None
for i, line in enumerate(lines, 1):
    if re.match(r"^def global_exception_handler\b", line.rstrip()):
        geh_line = i
        break

thread_exc_line = None
for i, line in enumerate(lines, 1):
    if re.match(r"^def thread_exception_handler\b", line.rstrip()):
        thread_exc_line = i
        break

# Extract from geh or thread_exc down to end
start_of_footer = min(x for x in [geh_line, thread_exc_line, main_func_line] if x is not None)
footer_block = "".join(lines[start_of_footer-1:])

# Also find if __name__ block
ifname_line = None
for i, line in enumerate(lines, 1):
    if line.strip() == 'if __name__ == "__main__":':
        ifname_line = i

print(f"main() at line: {main_func_line}, global_exception_handler at: {geh_line}")
print(f"if __name__ at: {ifname_line}")

main_content = f'''"""
OutlastTrials AudioEditor - Main entry point.
Run this file to launch the application.
"""
import sys
import os
import traceback

from PyQt5 import QtWidgets, QtCore, QtGui

from editor.constants import current_version
from editor.translations import TRANSLATIONS, tr
from editor.app import WemSubtitleApp

{footer_block}
'''

write_file("main.py", main_content)

print("\n" + "="*60)
print("EXTRACTION COMPLETE!")
print("="*60)
print("\nStructure created:")
for root, dirs, files in os.walk("editor"):
    level = root.replace("editor", "").count(os.sep)
    indent = " " * 2 * level
    print(f"{indent}editor/{os.path.basename(root)}/")
    subindent = " " * 2 * (level + 1)
    for file in files:
        print(f"{subindent}{file}")
print("  main.py")
print("\nNext steps:")
print("  python -c \"from editor.app import WemSubtitleApp; print('Import OK')\"")
print("  python main.py")
