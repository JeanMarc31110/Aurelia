import hashlib
import os
from pathlib import Path


def mutex_name(data_dir):
    identity = hashlib.sha256(str(Path(data_dir).resolve()).lower().encode("utf-8")).hexdigest()[:20]
    return f"Local\\Aurelia-{identity}"


class InstanceLock:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.acquired = False
        self._handle = None
        self._file = None

    def acquire(self):
        if self.acquired:
            return True
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.CreateMutexW(None, False, mutex_name(self.data_dir))
            if not handle:
                raise OSError(ctypes.get_last_error(), "Impossible de créer le mutex Aurelia")
            if ctypes.get_last_error() == 183:
                kernel32.CloseHandle(handle)
                return False
            self._handle = (kernel32, handle)
            self.acquired = True
            return True

        import fcntl
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file = (self.data_dir / "aurelia.instance.lock").open("a+")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._file.close();self._file = None
            return False
        self.acquired = True
        return True

    def release(self):
        if not self.acquired:
            return False
        if os.name == "nt":
            kernel32, handle = self._handle
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
            self._handle = None
        else:
            import fcntl
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close();self._file = None
        self.acquired = False
        return True

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Aurelia est déjà en cours d'exécution")
        return self

    def __exit__(self, *_):
        self.release()
