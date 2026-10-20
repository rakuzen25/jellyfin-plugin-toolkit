"""Versioned build and distribution support for independent Jellyfin plugins."""

import sys
from pathlib import Path

# Release imports occur only after bootstrap has verified every archived file.
# A source checkout uses the explicitly prepared, ignored development directory.
_root = Path(__file__).resolve().parent.parent
_runtime = _root / "_vendor"
if not _runtime.is_dir():
    _runtime = _root / ".build/runtime"
if _runtime.is_dir():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(_runtime))
