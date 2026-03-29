from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from typing import Iterator


LOGGER_ROOT = "qwen_comfy_clone"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class QueueLogHandler(logging.Handler):
    def __init__(self, target_queue: Queue[str]) -> None:
        super().__init__()
        self._queue = target_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._queue.put(message)


def get_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logging.getLogger(LOGGER_ROOT)
    suffix = name if name.startswith(f"{LOGGER_ROOT}.") else f"{LOGGER_ROOT}.{name}"
    return logging.getLogger(suffix)


def ensure_console_logging(level: int = logging.INFO) -> logging.Logger:
    logger = get_logger()
    logger.setLevel(level)
    logger.propagate = False
    for handler in logger.handlers:
        if getattr(handler, "_qwen_console_handler", False):
            return logger

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass

    handler = logging.StreamHandler(sys.stdout)
    handler._qwen_console_handler = True  # type: ignore[attr-defined]
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(handler)
    return logger


@contextmanager
def run_log_capture(log_path: Path, *, queue: Queue[str] | None = None) -> Iterator[Path]:
    logger = ensure_console_logging()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding="utf-8-sig")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(file_handler)

    queue_handler: QueueLogHandler | None = None
    if queue is not None:
        queue_handler = QueueLogHandler(queue)
        queue_handler.setLevel(logging.INFO)
        queue_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(queue_handler)

    try:
        yield log_path
    finally:
        logger.removeHandler(file_handler)
        file_handler.close()
        if queue_handler is not None:
            logger.removeHandler(queue_handler)
            queue_handler.close()
