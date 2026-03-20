"""
AudioTreeWidget - Audio file tree widget.
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


class AudioTreeWidget(QtWidgets.QTreeWidget):
    def __init__(self, parent=None, wem_app=None, lang=None):
        super().__init__(parent)
        self.wem_app = wem_app
        self.lang = lang
        self._highlighted_item = None
        self._highlighted_brush = QtGui.QBrush(QtGui.QColor(255, 255, 180))
    def keyPressEvent(self, event):
        """Handle key presses for audio playback and other actions."""
        key = event.key()
        modifiers = event.modifiers()

        if key == QtCore.Qt.Key_Space and modifiers == QtCore.Qt.NoModifier:
            if self.wem_app:
                self.wem_app.play_current(play_mod=False)
            event.accept()

        elif key == QtCore.Qt.Key_Space and modifiers == QtCore.Qt.ControlModifier:
            if self.wem_app:
                self.wem_app.play_current(play_mod=True)
            event.accept()

        elif key == QtCore.Qt.Key_Delete and modifiers == QtCore.Qt.NoModifier:
            if self.wem_app:
                self.wem_app.delete_current_mod_audio() 
            event.accept()
        else:
            super().keyPressEvent(event)
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
           
            pos = event.pos()
            item = self.itemAt(pos)
            self._set_highlighted_item(item)
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._set_highlighted_item(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_highlighted_item(None)
        if not event.mimeData().hasUrls():
            return super().dropEvent(event)
        urls = event.mimeData().urls()
        if not urls:
            return
        file_path = urls[0].toLocalFile()
        if not file_path.lower().endswith(('.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.wma', '.opus', '.webm')):
            QtWidgets.QMessageBox.warning(self, self.tr("invalid_file_title"), self.tr("audio_only_drop_msg"))
            return
        pos = event.pos()
        item = self.itemAt(pos)
        if not item or item.childCount() > 0:
            QtWidgets.QMessageBox.information(self, self.tr("drop_audio_title"), self.tr("drop_on_file_msg"))
            return
        entry = item.data(0, QtCore.Qt.UserRole)
        if not entry:
            return
        shortname = entry.get("ShortName", "")
        reply = QtWidgets.QMessageBox.question(
            self, self.tr("replace_audio_title"),
            self.tr("replace_audio_confirm_msg").format(shortname=shortname, filename=os.path.basename(file_path)),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            if self.wem_app:
                self.wem_app.quick_load_custom_audio(entry, self.lang, custom_file=file_path)
        event.acceptProposedAction()

    def _set_highlighted_item(self, item):
   
        if self._highlighted_item is not None:
            for col in range(self.columnCount()):
                self._highlighted_item.setBackground(col, QtGui.QBrush())
    
        self._highlighted_item = item
        if item is not None:
            for col in range(self.columnCount()):
                item.setBackground(col, self._highlighted_brush)
