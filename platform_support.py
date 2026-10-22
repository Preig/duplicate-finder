"""Small OS integrations; no commands run through a shell."""
import os
import subprocess
import sys

IS_MAC = sys.platform == "darwin"
TRASH_NAME = "Trash" if IS_MAC else "Recycle Bin"
FILE_MANAGER = "Finder" if IS_MAC else "Explorer"
UI_FONT = "Helvetica Neue" if IS_MAC else "Segoe UI"
TITLE_FONT = "Helvetica Neue" if IS_MAC else "Segoe UI Semibold"


def reveal_file(path):
    path = os.path.abspath(path)
    if IS_MAC:
        subprocess.Popen(["/usr/bin/open", "-R", path])
    elif os.name == "nt":
        subprocess.Popen(["explorer.exe", "/select,", path])
    else:
        raise OSError("Reveal is supported on Windows and macOS only")
