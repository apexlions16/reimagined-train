"""
EasterEggLoader - Easter egg feature.
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


class EasterEggLoader(QObject):
    config_loaded = pyqtSignal(dict)    
    image_loaded = pyqtSignal(object)    
    loading_failed = pyqtSignal(str)    
    def __init__(self, parent_app, parent=None):
        super().__init__(parent)
        self.parent_app = parent_app
    def load_config(self):
        import threading
        
        def download_config():
            try:
                import requests
                import json
 
                config_url = "https://raw.githubusercontent.com/Bezna/OutlastTrials_AudioEditor/refs/heads/main/data/nothing.json"
                
                headers = {
                    'User-Agent': 'OutlastTrials_AudioEditor/1.0',
                    'Accept': 'application/json',
                }
                
                response = requests.get(config_url, timeout=10, headers=headers)
                response.raise_for_status()
                
                config = response.json()
                print(f"Config loaded successfully: {config}")
                
                self.config_loaded.emit(config)
                
            except Exception as e:
                print(f"Failed to load config: {e}")
                
                default_config = {
                    "easter_egg_image": "https://i.imgur.com/VeWWVDN.png",
                    "message": self.parent_app.tr('easter_egg_message'),
                    "version": "fallback"
                }
                self.config_loaded.emit(default_config)
        
        thread = threading.Thread(target=download_config)
        thread.daemon = True
        thread.start()
    
    def load_image(self, image_url):
        import threading
        
        def download_image():
            try:
                import requests
                from PyQt5.QtGui import QPixmap
                import time
                
                if not image_url:
                    raise Exception("No image URL provided")
                
                print(f"Loading image from: {image_url}")
                
                time.sleep(0.5)  
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'image/*',
                }
                
                response = requests.get(image_url, timeout=15, headers=headers)
                response.raise_for_status()
                
                print(f"Image downloaded, size: {len(response.content)} bytes")
                
                pixmap = QPixmap()
                success = pixmap.loadFromData(response.content)
                
                if success and not pixmap.isNull():
                    print("Image loaded successfully")
                    self.image_loaded.emit(pixmap)
                else:
                    raise Exception("Failed to create QPixmap")
                    
            except Exception as e:
                print(f"Failed to load image: {e}")
                self.loading_failed.emit(str(e))
        
        thread = threading.Thread(target=download_image)
        thread.daemon = True
        thread.start()
def global_exception_handler(exc_type, exc_value, exc_traceback):
    error_details = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    full_error_msg = f"An unexpected error occurred:\n\n{error_details}"
    
    DEBUG.log("="*20 + " CRITICAL ERROR " + "="*20, "ERROR")
    DEBUG.log(full_error_msg, "ERROR")
    
    log_filename = f"crash_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(base_path, "data", log_filename)
    
    try:
        with open(log_path, 'w', encoding='utf-8') as crash_file:
            crash_file.write("=== CRASH REPORT ===\n")
            crash_file.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            crash_file.write(f"Version: {current_version}\n\n")
            crash_file.write(f"OS: {sys.platform}\n")
            crash_file.write("--- Error Details ---\n")
            crash_file.write(full_error_msg + "\n\n")
            crash_file.write("--- Full Session Log ---\n")
            crash_file.write(DEBUG.get_logs())
        
        final_message_for_user = f"{full_error_msg}\n\nA detailed crash log has been saved to:\n{log_path}"
    except Exception as save_error:
        final_message_for_user = f"{full_error_msg}\n\nFailed to save detailed crash log: {str(save_error)}"
    
    app = QtWidgets.QApplication.instance()
    if app:
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Critical)
        msg.setWindowTitle("Application Error")
        msg.setText("The application has encountered a critical error and will close.")
        msg.setInformativeText("Please report this bug with the details from the log files found in the 'data' folder.")
        msg.setDetailedText(final_message_for_user)
        
        copy_btn = msg.addButton("Copy Error to Clipboard", QtWidgets.QMessageBox.ActionRole)
        msg.addButton("Close", QtWidgets.QMessageBox.RejectRole)
        
        msg.exec_()
        
        if msg.clickedButton() == copy_btn:
            QtWidgets.QApplication.clipboard().setText(final_message_for_user)
    
    print("CRITICAL ERROR:", final_message_for_user)
    sys.exit(1)

sys.excepthook = global_exception_handler

def thread_exception_handler(args):
    global_exception_handler(args.exc_type, args.exc_value, args.exc_traceback)
threading.excepthook = thread_exception_handler
