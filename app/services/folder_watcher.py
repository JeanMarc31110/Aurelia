import logging
import threading

from app.local_config import ensure_local_directories, load_local_config
from app.services.local_ingestion import LocalIngestionService


logger = logging.getLogger("aurelia.watcher")


class FolderWatcher:
    def __init__(self, config=None, ingestion_service=None):
        self.config = ensure_local_directories(config or load_local_config())
        self.ingestion_service = ingestion_service or LocalIngestionService(self.config)
        self._stop_event = threading.Event()
        self._thread = None

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def scan_once(self):
        results = []
        for path in sorted(self.config.inbox_dir.iterdir(), key=lambda item: item.name.lower()):
            if self._stop_event.is_set():break
            if not path.is_file():continue
            logger.info("file_detected filename=%s", path.name)
            try:
                results.append(self.ingestion_service.process_file(path, stop_event=self._stop_event))
            except InterruptedError:
                break
            except (OSError, TimeoutError) as exc:
                logger.warning("file_temporarily_unavailable filename=%s error=%s", path.name, type(exc).__name__)
            except Exception as exc:
                logger.exception("watcher_file_error filename=%s error=%s", path.name, type(exc).__name__)
        return results

    def _run(self):
        logger.info("watcher_started inbox=%s", self.config.inbox_dir)
        while not self._stop_event.is_set():
            try:self.scan_once()
            except Exception as exc:logger.exception("watcher_scan_error error=%s", type(exc).__name__)
            self._stop_event.wait(self.config.watcher_poll_seconds)
        logger.info("watcher_stopped")

    def start(self):
        if self.running:return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="aurelia-folder-watcher", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout=10):
        if not self._thread:return False
        self._stop_event.set()
        self._thread.join(timeout)
        stopped = not self._thread.is_alive()
        if stopped:self._thread = None
        return stopped
