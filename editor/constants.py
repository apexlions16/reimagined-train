"""
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
