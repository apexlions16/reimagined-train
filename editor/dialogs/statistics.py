"""
StatisticsDialog - Project statistics dialog.
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


class StatisticsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        self.tr = parent.tr if hasattr(parent, 'tr') else lambda key: key
        
        self.setWindowTitle(self.tr("project_statistics_title"))
        self.setMinimumSize(600, 500)

        self.layout = QtWidgets.QVBoxLayout(self)

        header_layout = QtWidgets.QHBoxLayout()
        header_layout.addWidget(QtWidgets.QLabel(f"<b>{self.tr('mod_profile_label')}</b>"))
        self.profile_name_label = QtWidgets.QLabel("...")
        self.profile_name_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        header_layout.addWidget(self.profile_name_label)
        header_layout.addStretch()
        self.recalculate_btn = QtWidgets.QPushButton(self.tr("recalculate_btn"))
        self.recalculate_btn.clicked.connect(self.calculate_statistics)
        header_layout.addWidget(self.recalculate_btn)
        self.layout.addLayout(header_layout)
        
        general_group = QtWidgets.QGroupBox(self.tr("general_stats_group"))
        general_layout = QtWidgets.QFormLayout(general_group)
        self.audio_files_label = QtWidgets.QLabel(self.tr("calculating_stats"))
        self.subtitle_files_label = QtWidgets.QLabel(self.tr("calculating_stats"))
        self.mod_size_label = QtWidgets.QLabel(self.tr("calculating_stats"))
        general_layout.addRow(self.tr("total_audio_files"), self.audio_files_label)
        general_layout.addRow(self.tr("total_subtitle_files"), self.subtitle_files_label)
        general_layout.addRow(self.tr("total_mod_size"), self.mod_size_label)
        self.layout.addWidget(general_group)
        
        subtitle_group = QtWidgets.QGroupBox(self.tr("subtitle_stats_group"))
        subtitle_layout = QtWidgets.QFormLayout(subtitle_group)
        self.modified_subs_label = QtWidgets.QLabel(self.tr("calculating_stats"))
        self.new_subs_label = QtWidgets.QLabel(self.tr("calculating_stats"))
        self.affected_langs_label = QtWidgets.QLabel(self.tr("calculating_stats"))
        subtitle_layout.addRow(self.tr("modified_subtitle_entries"), self.modified_subs_label)
        subtitle_layout.addRow(self.tr("new_subtitle_entries"), self.new_subs_label)
        subtitle_layout.addRow(self.tr("total_languages_affected"), self.affected_langs_label)
        self.layout.addWidget(subtitle_group)
        
        files_group = QtWidgets.QGroupBox(self.tr("modified_files_group"))
        files_layout = QtWidgets.QVBoxLayout(files_group)
        self.files_list_widget = QtWidgets.QTextEdit()
        self.files_list_widget.setReadOnly(True)
        files_layout.addWidget(self.files_list_widget)
        
        copy_btn = QtWidgets.QPushButton(self.tr("copy_list_btn"))
        copy_btn.clicked.connect(self.copy_file_list)
        files_layout.addWidget(copy_btn, 0, QtCore.Qt.AlignRight)
        self.layout.addWidget(files_group)
        
        QtCore.QTimer.singleShot(100, self.calculate_statistics)

    def calculate_statistics(self):
        self.recalculate_btn.setEnabled(False)
        self.profile_name_label.setText(self.parent_app.active_profile_name or "N/A")
        
        if not self.parent_app.mod_p_path or not self.parent_app.active_profile_name:
            error_msg = self.tr("no_profile_active_for_stats")
            self.audio_files_label.setText(error_msg)
            self.subtitle_files_label.setText(error_msg)
            self.mod_size_label.setText(error_msg)
            self.modified_subs_label.setText(error_msg)
            self.new_subs_label.setText(error_msg)
            self.affected_langs_label.setText(error_msg)
            self.files_list_widget.setText(error_msg)
            return

        mod_audio_path = os.path.join(self.parent_app.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows")
        mod_loc_path = os.path.join(self.parent_app.mod_p_path, "OPP", "Content", "Localization")
        
        audio_files = []
        subtitle_files = []
        total_size = 0

        if os.path.exists(self.parent_app.mod_p_path):
            for root, dirs, files in os.walk(self.parent_app.mod_p_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    total_size += os.path.getsize(file_path)
                    
                    rel_path = os.path.relpath(file_path, self.parent_app.mod_p_path)
                    if file.endswith(".wem"):
                        audio_files.append(rel_path)
                    elif file.endswith(".locres"):
                        subtitle_files.append(rel_path)
        
        self.audio_files_label.setText(str(len(audio_files)))
        self.subtitle_files_label.setText(str(len(subtitle_files)))
        
        if total_size > 1024 * 1024:
            self.mod_size_label.setText(f"{total_size / (1024*1024):.2f} MB")
        else:
            self.mod_size_label.setText(f"{total_size / 1024:.2f} KB")

        modified_count = len(self.parent_app.modified_subtitles)
        new_count = sum(1 for key in self.parent_app.modified_subtitles if key not in self.parent_app.original_subtitles)
        self.modified_subs_label.setText(f"{modified_count} ({modified_count - new_count} existing)")
        self.new_subs_label.setText(str(new_count))
        
        affected_langs = set()
        lang_to_check = self.parent_app.settings.data.get("subtitle_lang")
        if self.parent_app.modified_subtitles:
             affected_langs.add(lang_to_check)
        self.affected_langs_label.setText(", ".join(sorted(affected_langs)) if affected_langs else "0")

        all_files = sorted(audio_files) + sorted(subtitle_files)
        self.files_list_widget.setText("\n".join(all_files))
        
        self.recalculate_btn.setEnabled(True)

    def copy_file_list(self):
        QtWidgets.QApplication.clipboard().setText(self.files_list_widget.toPlainText())
        self.parent_app.statusBar().showMessage(self.tr("list_copied_msg"), 2000)
