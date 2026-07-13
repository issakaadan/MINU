from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.runtime import get_runtime_paths

_configured = False


def _has_handler(logger: logging.Logger, handler_type: type[logging.Handler], target: Path) -> bool:
    for handler in logger.handlers:
        if isinstance(handler, handler_type) and Path(
            getattr(handler, "baseFilename", "")
        ) == target:
            return True
    return False


def configure_logging() -> Path:
    global _configured
    runtime_paths = get_runtime_paths()
    log_path = runtime_paths.logs_dir / "application.log"
    if _configured:
        return log_path

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if not _has_handler(root_logger, RotatingFileHandler, log_path):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.captureWarnings(True)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _configured = True
    return log_path
