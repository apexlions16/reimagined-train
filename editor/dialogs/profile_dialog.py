"""
ProfileDialog - Mod profile editing dialog.
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


class ProfileDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, existing_data=None, translator=None):
        super().__init__(parent)
        self.parent_app = parent
        self.is_edit_mode = existing_data is not None
        self.tr = translator if translator else lambda key: key
        self.setWindowTitle(self.tr("edit_profile") if self.is_edit_mode else self.tr("create_profile"))
        self.setMinimumWidth(400)

        self.layout = QtWidgets.QFormLayout(self)
        
        self.name_edit = QtWidgets.QLineEdit()
        self.author_edit = QtWidgets.QLineEdit()
        self.version_edit = QtWidgets.QLineEdit()
        self.description_edit = QtWidgets.QTextEdit()
        self.description_edit.setFixedHeight(80)
        
        self.icon_path = ""
        self.icon_preview = QtWidgets.QLabel(self.tr("no_icon_selected"))
        self.icon_preview.setFixedSize(64, 64)
        self.icon_preview.setStyleSheet("border: 1px solid #ccc; text-align: center;")
        self.icon_preview.setAlignment(QtCore.Qt.AlignCenter)
        
        icon_button = QtWidgets.QPushButton(self.tr("browse"))
        icon_button.clicked.connect(self.select_icon)
        
        icon_layout = QtWidgets.QHBoxLayout()
        icon_layout.addWidget(self.icon_preview)
        icon_layout.addWidget(icon_button)
        icon_layout.addStretch()

        if self.is_edit_mode:
            profile_name = os.path.basename(existing_data["path"])
            self.name_edit.setText(profile_name)
            self.name_edit.setReadOnly(True) 
            
            info = existing_data["data"]
            self.author_edit.setText(info.get("author", ""))
            self.version_edit.setText(info.get("version", "1.0"))
            self.description_edit.setPlainText(info.get("description", ""))
            
            self.icon_path = existing_data["icon"]
            if os.path.exists(self.icon_path):
                pixmap = QtGui.QPixmap(self.icon_path)
                self.icon_preview.setPixmap(pixmap.scaled(64, 64, QtCore.Qt.KeepAspectRatio))

        self.layout.addRow(self.tr("profile_name"), self.name_edit)
        self.layout.addRow(self.tr("author"), self.author_edit)
        self.layout.addRow(self.tr("version"), self.version_edit)
        self.layout.addRow(self.tr("description"), self.description_edit)
        self.layout.addRow(self.tr("icon_png"), icon_layout)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.layout.addRow(self.buttons)

    def select_icon(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, self.tr("select_icon"), "", f"{self.tr('png_images')} (*.png)")
        if path:
            self.icon_path = path
            pixmap = QtGui.QPixmap(path)
            self.icon_preview.setPixmap(pixmap.scaled(64, 64, QtCore.Qt.KeepAspectRatio))

    def get_data(self):
        return {
            "name": self.name_edit.text().strip(),
            "icon_path": self.icon_path,
            "info": {
                "author": self.author_edit.text().strip(),
                "version": self.version_edit.text().strip(),
                "description": self.description_edit.toPlainText().strip()
            }
        }
    
    def accept(self):
        if not self.name_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, self.tr("validation_error"), self.tr("profile_name_empty"))
            return
        super().accept()         
