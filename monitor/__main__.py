"""CLI entry: python -m monitor {run,login,cleanup-screenshots,doctor}."""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime

from loguru import logger

from monitor.alerter import Alerter
from monitor.analyzer import AnalysisError, Analyzer
from monitor.config import DashboardConfig, Settings, get_settings, load_dashboards
from monitor.logging_setup import setup_logging
from monitor.retention import cleanup_screenshots
from monitor.schedule_window import is_within_business_window, now_in_tz, window_skip_reason
from monitor.screenshot import (
    AuthError,
    CaptureError,
    ImageBudgetError,
    capture_dashboard,
    interactive_login,
)
from monitor.state import (
    LockBusy,
    acquire_run_lock,
    load_state,
    record_llm_failure,
    record_llm_success,
    record_run_start,
    save_state,
)


def _find_board(settings: Settings, board_id: str) -> DashboardConfig:
    boards = load_dashboards(settings.dashboards_file)
    for board in boards:
        if board.id == board_id:
            return board
    known = ", ".join(b.id for b in boards)
    raise SystemExit(f"Unknown board id {board_id!r}. Known: {known}")


def cmd_login(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings.log_dir, verbose=args.verbose)
    board = _find_board(settings, args.board)
    path = interactive_login(board, settings)
    print(f"Saved storage_state: {path}")
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(settings.log_dir, verbose=args.verbose)
    deleted = cleanup_screenshots(settings.screenshot_dir, settings.screenshot_keep_hours)
    logger.info("Cleanup removed {} file(s)", deleted)
    print(f"Deleted {deleted} file(s)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings.log_dir, verbose=args.verbose)
    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        status = "OK" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))

    check("LLM_API_KEY set", bool(settings.llm_api_key))
    check("LLM_MODEL set", bool(settings.llm_model), settings.llm_model or "(empty)")
    check("ALERT_SOURCE_UUID set", bool(settings.alert_source_uuid))
    check("ALERT_PUSH_CREDENTIAL set", bool(settings.alert_push_credential))
    check("dashboards file exists", settings.dashboards_file.exists(), str(settings.dashboards_file))

    try:
        boards = load_dashboards(settings.dashboards_file)
        check("dashboards non-empty", len(boards) > 0, f"{len(boards)} board(s)")
        for board in boards:
            state_path = settings.storage_state_path_for(board)
            check(
                f"storage_state for {board.id}",
                state_path.exists(),
                str(state_path),
            )
    except Exception as exc:  # noqa: BLE001
        check("load dashboards", False, str(exc))

    now = now_in_tz(settings.tz_name)
    in_window = is_within_business_window(
        now,
        tz_name=settings.tz_name,
        start_hour=settings.window_start_hour,
        start_minute=settings.window_start_minute,
        end_hour=settings.window_end_hour,
        end_minute=settings.window_end_minute,
    )
    print(f"[INFO] now={now.isoformat()} in_business_window={in_window}")
    print(f"[INFO] alert_url={settings.alert_signals_url()}")
    return 0 if ok else 1


def _process_board(
    board: DashboardConfig,
    settings: Settings,
    alerter: Alerter,
    analyzer: Analyzer,
    state,
    deadline: float,
) -> None:
    if time.monotonic() > deadline:
        alerter.send_monitor_failure(
            board.id,
            "timeout",
            "run deadline exceeded before board processing",
        )
        return

    record_run_start(state, board.id)

    try:
        capture = capture_dashboard(board, settings)
    except AuthError as exc:
        logger.error("Auth failure board={}: {}", board.id, exc)
        alerter.send_monitor_failure(board.id, exc.stage, str(exc))
        return
    except ImageBudgetError as exc:
        logger.error("Image budget board={}: {}", board.id, exc)
        alerter.send_monitor_failure(board.id, exc.stage, str(exc))
        return
    except CaptureError as exc:
        logger.error("Capture failure board={}: {}", board.id, exc)
        alerter.send_monitor_failure(board.id, exc.stage, str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected capture error board={}", board.id)
        alerter.send_monitor_failure(
            board.id,
            "capture",
            f"unexpected: {exc}",
            detail=traceback.format_exc()[-1500:],
        )
        return

    if time.monotonic() > deadline:
        alerter.send_monitor_failure(
            board.id,
            "timeout",
            "run deadline exceeded after capture",
        )
        return

    try:
        analysis = analyzer.analyze(capture.path)
    except AnalysisError as exc:
        count = record_llm_failure(state, board.id)
        logger.error(
            "LLM failure board={} consecutive={} err={}",
            board.id,
            count,
            exc,
        )
        if count >= settings.llm_failure_threshold:
            alerter.send_monitor_failure(
                board.id,
                "llm_parse",
                f"{count} consecutive LLM failures",
                detail=exc.raw or str(exc),
            )
        else:
            logger.warning(
                "LLM failure board={} logged only ({}/{})",
                board.id,
                count,
                settings.llm_failure_threshold,
            )
        return
    except Exception as exc:  # noqa: BLE001
        count = record_llm_failure(state, board.id)
        logger.exception("Unexpected LLM error board={}", board.id)
        if count >= settings.llm_failure_threshold:
            alerter.send_monitor_failure(
                board.id,
                "llm_parse",
                f"unexpected LLM error ({count} consecutive)",
                detail=traceback.format_exc()[-1500:],
            )
        return

    record_llm_success(state, board.id)
    logger.info(
        "Analysis board={} status={} has_critical={} issues={} summary={}",
        board.id,
        analysis.status,
        analysis.has_critical_issues,
        len(analysis.issues),
        analysis.summary,
    )
    for issue in analysis.issues:
        logger.info(
            "Issue board={} severity={} component={} issue={} evidence={}",
            board.id,
            issue.severity,
            issue.component,
            issue.issue,
            issue.evidence,
        )

    if analysis.has_critical_issues:
        alerter.send_board_critical(board.id, board.url, analysis)
    else:
        logger.info("Board {} healthy/non-critical — no alert", board.id)


def cmd_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings.log_dir, verbose=args.verbose)

    now = now_in_tz(settings.tz_name)
    reason = window_skip_reason(
        now,
        tz_name=settings.tz_name,
        start_hour=settings.window_start_hour,
        start_minute=settings.window_start_minute,
        end_hour=settings.window_end_hour,
        end_minute=settings.window_end_minute,
    )
    if reason and not args.force:
        logger.info("Skipping run: {}", reason)
        return 0
    if args.force and reason:
        logger.warning("Force run outside window: {}", reason)

    try:
        boards = load_dashboards(settings.dashboards_file)
    except Exception as exc:  # noqa: BLE001
        setup_logging(settings.log_dir, verbose=args.verbose)
        logger.error("Config error loading dashboards: {}", exc)
        # Best-effort monitor page for config faults inside window
        alerter = Alerter(settings)
        try:
            alerter.send_monitor_failure("global", "config", str(exc))
        finally:
            alerter.close()
        return 1

    try:
        with acquire_run_lock(settings.lock_file):
            return _run_locked(settings, boards, now)
    except LockBusy:
        logger.info("Skipping run: previous run still active (lock {})", settings.lock_file)
        return 0


def _run_locked(settings: Settings, boards: list[DashboardConfig], now: datetime) -> int:
    logger.info(
        "Starting monitor cycle at {} boards={}",
        now.isoformat(),
        [b.id for b in boards],
    )
    deadline = time.monotonic() + settings.run_timeout_seconds
    state = load_state(settings.state_file)
    alerter = Alerter(settings)
    analyzer = Analyzer(settings)
    try:
        for board in boards:
            try:
                _process_board(board, settings, alerter, analyzer, state, deadline)
            except Exception:  # noqa: BLE001
                logger.exception("Unhandled error processing board={}", board.id)
                alerter.send_monitor_failure(
                    board.id,
                    "run",
                    "unhandled exception",
                    detail=traceback.format_exc()[-1500:],
                )
            save_state(settings.state_file, state)

        deleted = cleanup_screenshots(
            settings.screenshot_dir, settings.screenshot_keep_hours
        )
        logger.info("Retention deleted {} screenshot(s)", deleted)
        logger.info("Monitor cycle complete")
        return 0
    finally:
        alerter.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor",
        description="OpsSentinel OPS dashboard visual monitor",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Single monitoring cycle")
    run_p.add_argument(
        "--force",
        action="store_true",
        help="Run even outside the business-hours window",
    )
    run_p.set_defaults(func=cmd_run)

    login_p = sub.add_parser("login", help="Headed browser login; save storage_state")
    login_p.add_argument("--board", required=True, help="Dashboard id from dashboards.yaml")
    login_p.set_defaults(func=cmd_login)

    clean_p = sub.add_parser("cleanup-screenshots", help="Delete screenshots older than retention")
    clean_p.set_defaults(func=cmd_cleanup)

    doctor_p = sub.add_parser("doctor", help="Validate config and auth state files")
    doctor_p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
