"""Small native-library bridge for pip Qt inside a Conda environment."""

import ctypes
from pathlib import Path
import sys


def load_conda_xcb_cursor():
    # Pip's Qt libraries do not search Conda's lib directory for this X11 plugin
    # dependency. Load the installed library by its absolute path, without
    # changing system library paths or replacing the user's Qt platform choice.
    if sys.platform.startswith("linux"):
        library = Path(sys.prefix) / "lib" / "libxcb-cursor.so.0"
        if library.is_file():
            return ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
    return None
