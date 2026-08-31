import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def is_frozen():
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resource_root():
    return Path(sys._MEIPASS).resolve() if is_frozen() else SOURCE_ROOT


def resource_path(*parts):
    return resource_root().joinpath(*parts)


def program_directory():
    return Path(sys.executable).resolve().parent if is_frozen() else SOURCE_ROOT
