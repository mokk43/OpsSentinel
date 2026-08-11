import os
import time
from pathlib import Path

from monitor.retention import cleanup_screenshots
from monitor.state import (
    LockBusy,
    acquire_run_lock,
    load_state,
    record_llm_failure,
    record_llm_success,
    save_state,
)


def test_state_llm_counters(tmp_path: Path):
    path = tmp_path / "state.json"
    state = load_state(path)
    assert record_llm_failure(state, "b1") == 1
    assert record_llm_failure(state, "b1") == 2
    save_state(path, state)
    reloaded = load_state(path)
    assert reloaded.board("b1").consecutive_llm_failures == 2
    record_llm_success(reloaded, "b1")
    assert reloaded.board("b1").consecutive_llm_failures == 0


def test_lock_busy(tmp_path: Path):
    lock = tmp_path / "lock"
    with acquire_run_lock(lock):
        raised = False
        try:
            with acquire_run_lock(lock):
                pass
        except LockBusy:
            raised = True
        assert raised


def test_retention(tmp_path: Path):
    shot_dir = tmp_path / "shots" / "b"
    shot_dir.mkdir(parents=True)
    old = shot_dir / "old.png"
    new = shot_dir / "new.png"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    old_mtime = time.time() - 20 * 3600
    os.utime(old, (old_mtime, old_mtime))
    deleted = cleanup_screenshots(tmp_path / "shots", keep_hours=12)
    assert deleted == 1
    assert not old.exists()
    assert new.exists()
