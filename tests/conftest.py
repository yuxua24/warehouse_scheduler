"""pytest configuration: ensure vendored packages are on sys.path."""
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_vendored = os.path.join(_project_root, ".venv_packages")
if os.path.isdir(_vendored) and _vendored not in sys.path:
    sys.path.insert(0, _vendored)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
