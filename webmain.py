import os
import sys
from pathlib import Path


def _working_root() -> Path:
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        internal = exe_dir / '_internal'
        return internal if internal.is_dir() else exe_dir
    return Path(__file__).resolve().parent


os.chdir(_working_root())

if os.environ.get('VOICETRANSL_WEB_DEBUG'):
    os.environ.setdefault('QTWEBENGINE_REMOTE_DEBUGGING', os.environ.get('VOICETRANSL_WEB_DEBUG_PORT', '9222'))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)

from webui.shell import main  # noqa: E402


if __name__ == '__main__':
    main()
