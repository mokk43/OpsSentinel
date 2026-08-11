"""HTTP client for the local signals alerter."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from loguru import logger

from monitor.config import Settings
from monitor.models import AnalysisResult


def format_occurred_at(when: datetime | None, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    if when is None:
        when = datetime.now(tz)
    elif when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    else:
        when = when.astimezone(tz)
    return when.isoformat(timespec="seconds")


def _truncate_message(message: str, max_chars: int) -> str:
    if len(message) <= max_chars:
        return message
    suffix = "\n…(truncated)"
    keep = max_chars - len(suffix)
    if keep < 1:
        return message[:max_chars]
    return message[:keep] + suffix


def format_board_message(
    board_id: str,
    url: str,
    analysis: AnalysisResult,
    *,
    max_chars: int = 4000,
) -> str:
    critical = [i for i in analysis.issues if i.severity == "critical"]
    lines = [
        f"[{board_id}] {analysis.summary}",
        f"URL: {url}",
        "",
    ]
    bullets: list[str] = []
    omitted = 0
    for issue in critical:
        bullet = f"- {issue.component} | {issue.issue} | {issue.evidence}"
        candidate = "\n".join(lines + bullets + [bullet])
        if len(candidate) > max_chars - 32:
            omitted = len(critical) - len(bullets)
            break
        bullets.append(bullet)
    if not bullets and critical:
        # At least try one truncated bullet
        issue = critical[0]
        bullets.append(
            f"- {issue.component} | {issue.issue} | {issue.evidence}"[: max_chars // 2]
        )
        omitted = max(0, len(critical) - 1)
    if omitted:
        bullets.append(f"…and {omitted} more")
    if not bullets:
        bullets.append("- (no critical issue details)")
    return _truncate_message("\n".join(lines + bullets), max_chars)


def format_monitor_message(
    scope: str,
    stage: str,
    short_reason: str,
    detail: str = "",
    *,
    max_chars: int = 4000,
) -> str:
    lines = [f"[{scope}] {stage}: {short_reason}"]
    if detail:
        detail = detail.strip().replace("\r\n", "\n")
        if len(detail) > 1500:
            detail = detail[:1500] + "…"
        lines.append(f"Detail: {detail}")
    return _truncate_message("\n".join(lines), max_chars)


class Alerter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def post_signal(self, name: str, message: str, occurred_at: str | None = None) -> bool:
        """POST one signal. Returns True on 2xx. Retries once on transient failure."""
        if not self.settings.alert_source_uuid or not self.settings.alert_push_credential:
            logger.error("Alert source UUID or push credential not configured")
            return False

        payload = {
            "name": name,
            "message": message,
            "occurredAt": occurred_at
            or format_occurred_at(None, self.settings.tz_name),
        }
        url = self.settings.alert_signals_url()
        headers = {
            "Authorization": f"Bearer {self.settings.alert_push_credential}",
            "Content-Type": "application/json",
        }

        last_error: str | None = None
        for attempt in range(2):
            try:
                response = self._session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=10,
                )
                if 200 <= response.status_code < 300:
                    logger.info(
                        "Alert posted name={} status={} attempt={}",
                        name,
                        response.status_code,
                        attempt + 1,
                    )
                    return True
                last_error = f"HTTP {response.status_code}: {response.text[:500]}"
                logger.warning(
                    "Alert post failed name={} attempt={} error={}",
                    name,
                    attempt + 1,
                    last_error,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning(
                    "Alert post error name={} attempt={} error={}",
                    name,
                    attempt + 1,
                    last_error,
                )
            if attempt == 0:
                time.sleep(0.5)

        logger.error("Alert post exhausted retries name={} error={}", name, last_error)
        return False

    def send_board_critical(
        self,
        board_id: str,
        url: str,
        analysis: AnalysisResult,
    ) -> bool:
        message = format_board_message(
            board_id,
            url,
            analysis,
            max_chars=self.settings.alert_message_max_chars,
        )
        ok = self.post_signal(self.settings.board_alert_name, message)
        if not ok:
            # Board alert failed → try monitor alert once; if that fails, log only
            mon_msg = format_monitor_message(
                board_id,
                "dispatch",
                "failed to post board alert",
                detail=message[:500],
                max_chars=self.settings.alert_message_max_chars,
            )
            if not self.post_signal(self.settings.monitor_alert_name, mon_msg):
                logger.error(
                    "Board and monitor dispatch both failed board_id={}", board_id
                )
        return ok

    def send_monitor_failure(
        self,
        scope: str,
        stage: str,
        short_reason: str,
        detail: str = "",
    ) -> bool:
        message = format_monitor_message(
            scope,
            stage,
            short_reason,
            detail=detail,
            max_chars=self.settings.alert_message_max_chars,
        )
        ok = self.post_signal(self.settings.monitor_alert_name, message)
        if not ok:
            logger.error(
                "Monitor alert dispatch failed scope={} stage={} reason={}",
                scope,
                stage,
                short_reason,
            )
        return ok
