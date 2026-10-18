"""Local-checkout-only backend for the reviewed Transformers security patch."""
import importlib.util
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_evelyn_security_wheel_builder", _PROJECT.parent / "wheel_builder.py")
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)


def get_requires_for_build_wheel(config_settings=None):
    return []


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _builder.build_wheel("transformers", _PROJECT, wheel_directory, metadata_directory)


class UnsupportedOperation(Exception):
    pass


def build_sdist(sdist_directory, config_settings=None):
    raise UnsupportedOperation("Use this local checkout; source distributions are unsupported")
