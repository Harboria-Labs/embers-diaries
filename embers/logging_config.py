"""Runtime logging setup driven by :mod:`embers.config`."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import LoggingConfig


def configure_logging(config: LoggingConfig) -> None:
    """Configure Ember's logger without taking over unrelated application logs."""
    logger = logging.getLogger("embers")
    logger.setLevel(getattr(logging, config.level))
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s")
    if config.file is None:
        handler: logging.Handler = logging.StreamHandler()
    else:
        config.file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            config.file,
            maxBytes=config.max_bytes,
            backupCount=config.backup_count,
            encoding="utf-8",
        )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
