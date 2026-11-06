import sys
import logging
from typing import Callable, Optional

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

class FlushingStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes sys.stdout after every emitted log record."""
    def emit(self, record):
        super().emit(record)
        self.flush()

class LogCallbackHandler(logging.Handler):
    """Handler that invokes a callback with formatted log records safely."""
    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        try:
            msg = self.format(record)
            self.callback(msg)
        except Exception:
            # Silently catch exceptions to prevent handler errors on worker threads
            pass

def setup_logger(name: str = "news_engine", level: int = logging.INFO) -> logging.Logger:
    """Configures and returns the news_engine logger with stdout flushing."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers on re-runs
    if not any(isinstance(h, FlushingStreamHandler) for h in logger.handlers):
        stdout_handler = FlushingStreamHandler(sys.stdout)
        formatter = logging.Formatter(fmt=DEFAULT_LOG_FORMAT, datefmt=DATE_FORMAT)
        stdout_handler.setFormatter(formatter)
        logger.addHandler(stdout_handler)

    logger.propagate = False
    return logger

def get_logger(module_name: str) -> logging.Logger:
    """Convenience function to get a child logger under news_engine."""
    setup_logger()
    return logging.getLogger(f"news_engine.{module_name}")
