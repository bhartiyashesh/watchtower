"""
Path resolution for frozen (PyInstaller) and source execution modes.

BUNDLE_DIR  — read-only assets bundled by PyInstaller (templates, YOLO model, images).
              Falls back to "." in development.

RUNTIME_DIR — read-write directory next to the executable (for .env, events.db,
              thumbnails, caches). Falls back to "." in development.
"""

import sys
from pathlib import Path


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


# Read-only assets bundled by PyInstaller (templates, YOLO model, static images)
BUNDLE_DIR: Path = Path(getattr(sys, "_MEIPASS", "")) if _is_frozen() else Path(".")

# Read-write runtime directory (next to the executable, or CWD in dev)
RUNTIME_DIR: Path = Path(sys.executable).parent if _is_frozen() else Path(".")
