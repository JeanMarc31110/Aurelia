import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from app.instance_lock import InstanceLock
from app.local_config import ensure_local_directories, load_local_config
from app.resource_paths import program_directory
from app.services.data_migration import migrate_legacy_database
from app.services.local_logging import configure_local_logging
from app.services.runtime_migration import migrate_legacy_runtime_data


HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def browser_enabled():
    return os.getenv("AURELIA_NO_BROWSER", "").strip().lower() not in {"1", "true", "yes", "on"}


def port_open(host=HOST, port=PORT):
    try:
        with socket.create_connection((host, port), timeout=.4):
            return True
    except OSError:
        return False


def aurelia_ready(timeout=.8):
    try:
        with urllib.request.urlopen(f"{URL}/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and payload.get("app") == "aurelia"
    except Exception:
        return False


def wait_and_open_browser(opener=webbrowser.open, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if aurelia_ready():
            opener(URL)
            return True
        time.sleep(.25)
    logging.getLogger("aurelia.launcher").error("server_start_timeout")
    return False


def show_message(message, error=False):
    if os.name == "nt" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Aurelia", 0x10 if error else 0x40)
    elif sys.stderr:
        print(message, file=sys.stderr if error else sys.stdout)


def main():
    os.chdir(program_directory())
    config = ensure_local_directories(load_local_config())
    configure_local_logging(config)
    logger = logging.getLogger("aurelia.launcher")
    lock = InstanceLock(config.data_dir)
    if not lock.acquire():
        if aurelia_ready(2):
            if browser_enabled():webbrowser.open(URL)
            logger.info("second_launch_opened_existing_instance")
            return 0
        show_message("Aurelia est déjà en cours d’exécution.", error=True)
        logger.warning("second_launch_instance_locked")
        return 2
    try:
        if port_open():
            if aurelia_ready(2):
                if browser_enabled():webbrowser.open(URL)
                return 0
            show_message("Le port 8000 est déjà utilisé par un autre logiciel.", error=True)
            logger.error("port_8000_used_by_other_application")
            return 3
        migrate_legacy_runtime_data(config)
        migration = migrate_legacy_database(config)
        logger.info("data_migration_status=%s", migration["status"])
        if browser_enabled():
            threading.Thread(target=wait_and_open_browser, name="aurelia-browser", daemon=True).start()
        import uvicorn
        from app.main import app
        server = uvicorn.Server(uvicorn.Config(
            app, host=HOST, port=PORT, log_level="warning", reload=False, access_log=False,
            log_config=None,
        ))
        app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
        server.run()
        return 0
    except Exception as exc:
        logger.exception("launcher_failed error=%s", type(exc).__name__)
        show_message("Aurelia n’a pas pu démarrer. Consultez le journal local.", error=True)
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
