import os
import sys
import time
import socket
import threading
import webbrowser
from pathlib import Path

def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

BASE = application_dir()
os.chdir(BASE)
sys.path.insert(0, str(BASE))

def port_open(host="127.0.0.1", port=8000):
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False

def open_browser_when_ready():
    for _ in range(80):
        if port_open():
            webbrowser.open("http://127.0.0.1:8000")
            return
        time.sleep(0.25)

def main():
    from app.db import init_db
    from app.auth import ensure_default_admin
    init_db()
    ensure_default_admin()
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="warning", reload=False, access_log=False)

if __name__ == "__main__":
    main()
