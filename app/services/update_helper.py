import argparse
import ctypes
import os
import sqlite3
import time
from pathlib import Path

from app.local_config import load_local_config
from app.services.local_logging import configure_local_logging
from app.services.updates import execute_prepared_update, read_update_state


def process_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        process = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not process:
            return False
        ctypes.windll.kernel32.CloseHandle(process)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for_parent_exit(pid, timeout=60):
    deadline = time.monotonic() + timeout
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(.25)
    return not process_alive(pid)


def schema_reader_from_state(state):
    config = load_local_config(Path(state["live_app_dir"]))

    def read_schema():
        if not config.sqlite_path.is_file():
            return 0
        with sqlite3.connect(config.sqlite_path) as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    return config, read_schema


def main(argv=None):
    parser = argparse.ArgumentParser(description="Aurelia independent update helper")
    parser.add_argument("state_path")
    parser.add_argument("--parent-pid", type=int, required=True)
    arguments = parser.parse_args(argv)
    state = read_update_state(arguments.state_path)
    config, schema_reader = schema_reader_from_state(state)
    configure_local_logging(config)
    if not wait_for_parent_exit(arguments.parent_pid):
        return 2
    result = execute_prepared_update(arguments.state_path, schema_reader)
    return 0 if result["status"] in {"COMPLETED", "ROLLED_BACK", "RECOVERY_REQUIRED"} else 1
