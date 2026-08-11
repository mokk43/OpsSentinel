"""Process lock and durable per-board counters."""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from loguru import logger

if sys.platform == "win32":  # pragma: no cover
    import msvcrt
else:
    import fcntl


@dataclass
class BoardState:
    consecutive_llm_failures: int = 0
    last_successful_run_at: str | None = None
    last_run_at: str | None = None


@dataclass
class MonitorState:
    boards: dict[str, BoardState] = field(default_factory=dict)

    def board(self, board_id: str) -> BoardState:
        if board_id not in self.boards:
            self.boards[board_id] = BoardState()
        return self.boards[board_id]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(path: Path) -> MonitorState:
    if not path.exists():
        return MonitorState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read state file {}: {}", path, exc)
        return MonitorState()

    boards: dict[str, BoardState] = {}
    for board_id, data in (raw.get("boards") or {}).items():
        boards[board_id] = BoardState(
            consecutive_llm_failures=int(data.get("consecutive_llm_failures") or 0),
            last_successful_run_at=data.get("last_successful_run_at"),
            last_run_at=data.get("last_run_at"),
        )
    return MonitorState(boards=boards)


def save_state(path: Path, state: MonitorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "boards": {
            board_id: {
                "consecutive_llm_failures": b.consecutive_llm_failures,
                "last_successful_run_at": b.last_successful_run_at,
                "last_run_at": b.last_run_at,
            }
            for board_id, b in state.boards.items()
        }
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def record_run_start(state: MonitorState, board_id: str) -> None:
    state.board(board_id).last_run_at = _utc_now_iso()


def record_llm_success(state: MonitorState, board_id: str) -> None:
    b = state.board(board_id)
    b.consecutive_llm_failures = 0
    b.last_successful_run_at = _utc_now_iso()


def record_llm_failure(state: MonitorState, board_id: str) -> int:
    """Increment and return new consecutive failure count."""
    b = state.board(board_id)
    b.consecutive_llm_failures += 1
    return b.consecutive_llm_failures


class LockBusy(Exception):
    """Another monitor run holds the lock."""


@contextmanager
def acquire_run_lock(lock_path: Path) -> Iterator[None]:
    """
    Exclusive flock on lock_path. Raises LockBusy if not acquired.
    Non-blocking; caller should exit 0 on LockBusy.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if sys.platform == "win32":  # pragma: no cover
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LockBusy(str(lock_path)) from exc
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LockBusy(str(lock_path)) from exc

        # Record pid for debugging
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
        logger.debug("Acquired run lock {}", lock_path)
        try:
            yield
        finally:
            if sys.platform == "win32":  # pragma: no cover
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
            logger.debug("Released run lock {}", lock_path)
    finally:
        os.close(fd)
