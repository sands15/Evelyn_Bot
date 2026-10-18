
"""Also cover discovery with tests as the import root."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_evelyn_test_attempt", Path(__file__).resolve().parents[2] / "tools/run_test_attempt.py")
_capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_capture)
_capture.install_unittest_capture()
