"""
OutlastTrials AudioEditor - Main entry point.
Run this file to launch the application: python main.py
"""
import sys
import os
import traceback
import threading
from datetime import datetime

from PyQt5 import QtWidgets, QtCore, QtGui

from editor.constants import current_version
from editor.translations import TRANSLATIONS
from editor.core.settings import AppSettings
from editor.app import WemSubtitleApp


def global_exception_handler(exc_type, exc_value, exc_traceback):
    error_details = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    full_error_msg = f"An unexpected error occurred:\n\n{error_details}"

    log_filename = f"crash_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    base_path = (os.path.dirname(sys.executable)
                 if getattr(sys, 'frozen', False)
                 else os.path.dirname(os.path.abspath(__file__)))
    log_path = os.path.join(base_path, "data", log_filename)

    try:
        os.makedirs(os.path.join(base_path, "data"), exist_ok=True)
        with open(log_path, 'w', encoding='utf-8') as crash_file:
            crash_file.write("=== CRASH REPORT ===\n")
            crash_file.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            crash_file.write(f"Version: {current_version}\n\n")
            crash_file.write(f"OS: {sys.platform}\n")
            crash_file.write("--- Error Details ---\n")
            crash_file.write(full_error_msg + "\n\n")
        final_message_for_user = f"{full_error_msg}\n\nA detailed crash log has been saved to:\n{log_path}"
    except Exception as save_error:
        final_message_for_user = f"{full_error_msg}\n\nFailed to save detailed crash log: {str(save_error)}"

    app = QtWidgets.QApplication.instance()
    if app:
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Critical)
        msg.setWindowTitle("Application Error")
        msg.setText("The application has encountered a critical error and will close.")
        msg.setInformativeText(
            "Please report this bug with the details from the log files found in the 'data' folder."
        )
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


if __name__ == "__main__":
    from PyQt5.QtCore import QSharedMemory
    from PyQt5.QtWidgets import QMessageBox

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    shared_memory_key = "DAA73E5A-A93B-4264-8263-6901E788C946-OutlastTrialsAudioEditor"
    shared_memory = QSharedMemory(shared_memory_key)

    temp_settings = AppSettings()
    lang = temp_settings.data.get("ui_language", "en")
    temp_tr = lambda key: TRANSLATIONS.get(lang, {}).get(key, key)

    if not shared_memory.create(1):
        QMessageBox.warning(
            None,
            temp_tr("app_already_running_title"),
            temp_tr("app_already_running_msg")
        )
        sys.exit(0)

    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    splash_path = os.path.join(base_path, "data", "splash.png")
    splash = None

    if os.path.exists(splash_path):
        original_pixmap = QtGui.QPixmap(splash_path)
        splash = QtWidgets.QSplashScreen(original_pixmap, QtCore.Qt.WindowStaysOnTopHint)
        splash.setMask(original_pixmap.mask())

        def show_splash_message(message_key):
            pixmap_with_text = original_pixmap.copy()
            painter = QtGui.QPainter(pixmap_with_text)
            font = QtGui.QFont()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QtGui.QColor(220, 220, 220))
            rect = pixmap_with_text.rect()
            text_rect = QtCore.QRect(rect.x(), rect.y() + rect.height() - 40, rect.width(), 30)
            painter.drawText(text_rect, QtCore.Qt.AlignCenter, temp_tr(message_key))
            painter.end()
            splash.setPixmap(pixmap_with_text)
            app.processEvents()

        show_splash_message("splash_loading_app")
        splash.show()
        app.processEvents()

    try:
        if splash:
            show_splash_message("splash_init_ui")
        window = WemSubtitleApp()

        if splash:
            show_splash_message("splash_loading_profiles")
        if not window.initialize_profiles_and_ui():
            sys.exit(0)

        if splash:
            splash.finish(window)

        window.show()

        QtCore.QTimer.singleShot(100, window.load_orphans_from_cache_or_scan)

        sys.exit(app.exec_())

    except Exception as e:
        error_msg = f"An unexpected error occurred:\n\n{str(e)}\n\n"
        error_msg += "Traceback:\n" + traceback.format_exc()

        log_filename = f"crash_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        log_path = os.path.join(
            os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)),
            log_filename
        )

        try:
            with open(log_path, 'w', encoding='utf-8') as log_file:
                log_file.write("=== CRASH LOG ===\n")
                log_file.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_file.write(f"Version: {current_version}\n")
                log_file.write(f"OS: {sys.platform}\n")
                log_file.write(f"Python: {sys.version}\n")
                log_file.write(f"PyQt5: {QtCore.PYQT_VERSION_STR}\n\n")
                log_file.write("Error Details:\n")
                log_file.write(error_msg)
            error_msg += f"\n\nCrash log saved to: {log_path}"
        except Exception as save_error:
            error_msg += f"\n\nFailed to save crash log: {str(save_error)}"

        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Critical)
        msg.setWindowTitle("Application Error")
        msg.setText("The application has encountered an error and will close.")
        msg.setInformativeText("Please report this bug with the details below.")
        msg.setDetailedText(error_msg)
        copy_btn = msg.addButton("Copy Error to Clipboard", QtWidgets.QMessageBox.ActionRole)
        msg.addButton("Close", QtWidgets.QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() == copy_btn:
            QtWidgets.QApplication.clipboard().setText(error_msg)
        sys.exit(1)
