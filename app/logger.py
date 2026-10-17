import logging
from logging.handlers import RotatingFileHandler

from .config import settings


def setup_logging() -> None:
    level_name = str(settings.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    # Transport debug/info output can include presigned storage URLs.
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Avoid duplicate handlers when app reloads.
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(fmt)

    root.addHandler(file_handler)
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
