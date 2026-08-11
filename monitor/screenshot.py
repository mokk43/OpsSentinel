"""Playwright screenshot capture and auth-wall detection."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger
from playwright.sync_api import Browser, Page, sync_playwright

from monitor.config import DashboardConfig, Settings


class CaptureError(Exception):
    """Screenshot or navigation failed."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


class AuthError(CaptureError):
    """Login wall detected or storage_state missing."""

    def __init__(self, message: str) -> None:
        super().__init__("auth", message)


class ImageBudgetError(CaptureError):
    """Screenshot exceeds configured LLM image budget."""

    def __init__(self, message: str) -> None:
        super().__init__("image_budget", message)


@dataclass
class CaptureResult:
    path: Path
    final_url: str
    title: str
    width: int | None
    height: int | None
    size_bytes: int


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as f:
            sig = f.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                return None
            length = struct.unpack(">I", f.read(4))[0]
            chunk_type = f.read(4)
            if chunk_type != b"IHDR" or length < 8:
                return None
            data = f.read(length)
            width, height = struct.unpack(">II", data[:8])
            return int(width), int(height)
    except OSError:
        return None


def check_image_budget(
    path: Path,
    *,
    max_bytes: int,
    max_side_px: int,
) -> tuple[int, int | None, int | None]:
    """
    Return (size_bytes, width, height). Raise ImageBudgetError if over budget.
    Never resizes — fail closed.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CaptureError("capture", f"cannot stat screenshot: {exc}") from exc

    dims = _png_dimensions(path)
    width = dims[0] if dims else None
    height = dims[1] if dims else None

    if size > max_bytes:
        raise ImageBudgetError(
            f"screenshot {size} bytes exceeds max {max_bytes} bytes"
        )
    if width is not None and height is not None:
        side = max(width, height)
        if side > max_side_px:
            raise ImageBudgetError(
                f"screenshot side {side}px exceeds max {max_side_px}px "
                f"(dimensions {width}x{height})"
            )
    return size, width, height


def _markers_match(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    return any(m.lower() in lowered for m in markers if m)


def assert_not_login_wall(
    page: Page,
    dashboard: DashboardConfig,
    settings: Settings,
) -> None:
    final_url = page.url or ""
    title = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001 — title can throw on blank pages
        title = ""

    login_url_markers = dashboard.login_url_markers or settings.global_login_url_markers()
    login_title_markers = (
        dashboard.login_title_markers or settings.global_login_title_markers()
    )
    success_url_markers = (
        dashboard.success_url_markers or settings.global_success_url_markers()
    )

    if _markers_match(final_url, login_url_markers):
        raise AuthError(f"login wall URL detected: {final_url}")
    if _markers_match(title, login_title_markers):
        raise AuthError(f"login wall title detected: {title!r} url={final_url}")

    if success_url_markers and not _markers_match(final_url, success_url_markers):
        raise AuthError(
            f"URL missing success markers {success_url_markers}: {final_url}"
        )


def screenshot_filename(board_id: str, when: datetime, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    if when.tzinfo is None:
        local = when.replace(tzinfo=tz)
    else:
        local = when.astimezone(tz)
    # 20260811T143210+0800.png
    stamp = local.strftime("%Y%m%dT%H%M%S%z")
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", board_id)
    return f"{safe_id}_{stamp}.png"


def capture_dashboard(
    dashboard: DashboardConfig,
    settings: Settings,
    *,
    headed: bool = False,
) -> CaptureResult:
    state_path = settings.storage_state_path_for(dashboard)
    if not state_path.exists():
        raise AuthError(
            f"storage_state missing at {state_path}; run: python -m monitor login --board {dashboard.id}"
        )

    out_dir = settings.screenshot_dir / dashboard.id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / screenshot_filename(
        dashboard.id, datetime.now(ZoneInfo(settings.tz_name)), settings.tz_name
    )

    logger.info(
        "Capturing board={} url={} state={}",
        dashboard.id,
        dashboard.url,
        state_path,
    )

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=not headed)
        try:
            context = browser.new_context(
                storage_state=str(state_path),
                viewport={
                    "width": settings.viewport_width,
                    "height": settings.viewport_height,
                },
                device_scale_factor=settings.device_scale_factor,
            )
            page = context.new_page()
            try:
                page.goto(
                    dashboard.url,
                    wait_until="networkidle",
                    timeout=settings.page_goto_timeout_ms,
                )
            except Exception as exc:  # Playwright timeout/errors
                raise CaptureError("capture", f"navigation failed: {exc}") from exc

            if settings.page_settle_ms > 0:
                page.wait_for_timeout(settings.page_settle_ms)

            assert_not_login_wall(page, dashboard, settings)

            final_url = page.url
            title = page.title()
            page.screenshot(path=str(out_path), full_page=True, type="png")
            context.close()
        finally:
            browser.close()

    size, width, height = check_image_budget(
        out_path,
        max_bytes=settings.llm_max_image_bytes,
        max_side_px=settings.llm_max_image_side_px,
    )
    logger.info(
        "Captured board={} path={} bytes={} dims={}x{}",
        dashboard.id,
        out_path,
        size,
        width,
        height,
    )
    return CaptureResult(
        path=out_path,
        final_url=final_url,
        title=title,
        width=width,
        height=height,
        size_bytes=size,
    )


def interactive_login(dashboard: DashboardConfig, settings: Settings) -> Path:
    """
    Open a headed browser for manual login; save storage_state on Enter.
    """
    state_path = settings.storage_state_path_for(dashboard)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting headed login for board={} url={}. Complete login in the browser, "
        "then return here and press Enter to save session to {}",
        dashboard.id,
        dashboard.url,
        state_path,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            context = browser.new_context(
                viewport={
                    "width": settings.viewport_width,
                    "height": settings.viewport_height,
                },
                device_scale_factor=settings.device_scale_factor,
            )
            page = context.new_page()
            page.goto(dashboard.url, wait_until="domcontentloaded", timeout=60_000)
            input("Press Enter after login is complete to save storage_state… ")
            # Best-effort assertion; still save even if markers uncertain
            try:
                assert_not_login_wall(page, dashboard, settings)
            except AuthError as exc:
                logger.warning(
                    "Page still looks like login ({}); saving state anyway — "
                    "re-run login if capture fails",
                    exc,
                )
            context.storage_state(path=str(state_path))
        finally:
            browser.close()

    logger.info("Saved storage_state to {}", state_path)
    return state_path
