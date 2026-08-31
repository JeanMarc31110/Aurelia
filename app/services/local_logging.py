import logging
from logging.handlers import RotatingFileHandler

from app.local_config import ensure_local_directories, load_local_config


class WindowsSafeRotatingFileHandler(RotatingFileHandler):
    """Release the file after every record so abrupt local stops do not lock it."""

    def emit(self, record):
        try:
            super().emit(record)
        finally:
            if self.stream:
                self.stream.close()
                self.stream = None


def configure_local_logging(config=None):
    config = ensure_local_directories(config or load_local_config())
    logger = logging.getLogger("aurelia")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    target = str(config.log_path.resolve())
    for handler in list(logger.handlers):
        if getattr(handler, "_aurelia_log_path", None) == target:
            return logger
        if getattr(handler, "_aurelia_log_path", None):
            logger.removeHandler(handler)
            handler.close()
    handler = WindowsSafeRotatingFileHandler(
        config.log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8", delay=True
    )
    handler._aurelia_log_path = target
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    return logger


def close_local_logging():
    logger = logging.getLogger("aurelia")
    for handler in list(logger.handlers):
        if getattr(handler, "_aurelia_log_path", None):
            logger.removeHandler(handler)
            handler.close()
