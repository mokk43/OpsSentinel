"""Screenshot retention sweeper."""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger


def cleanup_screenshots(screenshot_dir: Path, keep_hours: int) -> int:
    """
    Delete files under screenshot_dir older than keep_hours.
    Returns number of files deleted.
    """
    if keep_hours < 0:
        raise ValueError("keep_hours must be >= 0")
    if not screenshot_dir.exists():
        return 0

    cutoff = time.time() - (keep_hours * 3600)
    deleted = 0
    for path in screenshot_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            logger.warning("stat failed for {}: {}", path, exc)
            continue
        if mtime < cutoff:
            try:
                path.unlink()
                deleted += 1
                logger.info("Deleted old screenshot {}", path)
            except OSError as exc:
                logger.warning("Failed to delete {}: {}", path, exc)
    return deleted
