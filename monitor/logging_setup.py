"""Loguru configuration."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_dir: Path, *, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    logger.add(
        log_dir / "monitor.log",
        rotation="10 MB",
        retention="14 days",
        level="DEBUG",
        encoding="utf-8",
    )
