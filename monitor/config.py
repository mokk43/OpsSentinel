"""Configuration loading (env + dashboards.yaml)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: parent of the monitor package
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _split_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


class DashboardConfig(BaseModel):
    id: str
    url: str
    storage_state: str | None = None
    login_url_markers: list[str] = Field(default_factory=list)
    login_title_markers: list[str] = Field(default_factory=list)
    success_url_markers: list[str] = Field(default_factory=list)

    @field_validator(
        "login_url_markers",
        "login_title_markers",
        "success_url_markers",
        mode="before",
    )
    @classmethod
    def _coerce_markers(cls, v: Any) -> list[str]:
        return _split_csv(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tz_name: str = "Asia/Shanghai"
    check_interval_minutes: int = 10

    llm_base_url: str = "https://api.moonshot.cn/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_max_image_bytes: int = 10 * 1024 * 1024
    llm_max_image_side_px: int = 8000

    alert_base_url: str = "http://127.0.0.1:9780"
    alert_source_uuid: str = ""
    alert_push_credential: str = ""

    dashboards_file: Path = PROJECT_ROOT / "dashboards.yaml"
    screenshot_dir: Path = PROJECT_ROOT / "screenshots"
    screenshot_keep_hours: int = 12
    storage_state_dir: Path = PROJECT_ROOT / "storage_state"
    lock_file: Path = PROJECT_ROOT / ".monitor.lock"
    state_file: Path = PROJECT_ROOT / "state.json"
    log_dir: Path = PROJECT_ROOT / "logs"

    run_timeout_seconds: int = 480
    page_goto_timeout_ms: int = 30_000
    page_settle_ms: int = 2_000
    viewport_width: int = 1920
    viewport_height: int = 1080
    device_scale_factor: int = 2

    login_url_markers: str = "login,signin,sso,passport,auth"
    login_title_markers: str = "login,sign in,登录,登入"
    success_url_markers: str = ""

    # Business window (inclusive start; last start allowed at end)
    window_start_hour: int = 9
    window_start_minute: int = 30
    window_end_hour: int = 18
    window_end_minute: int = 10

    board_alert_name: str = "Aliyun.OPS"
    monitor_alert_name: str = "Aliyun.OPS.Monitor"
    llm_failure_threshold: int = 2
    alert_message_max_chars: int = 4000

    @field_validator(
        "dashboards_file",
        "screenshot_dir",
        "storage_state_dir",
        "lock_file",
        "state_file",
        "log_dir",
        mode="before",
    )
    @classmethod
    def _resolve_path(cls, v: Any) -> Path:
        path = Path(v)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def global_login_url_markers(self) -> list[str]:
        return _split_csv(self.login_url_markers)

    def global_login_title_markers(self) -> list[str]:
        return _split_csv(self.login_title_markers)

    def global_success_url_markers(self) -> list[str]:
        return _split_csv(self.success_url_markers)

    def alert_signals_url(self) -> str:
        base = self.alert_base_url.rstrip("/")
        return f"{base}/v1/sources/{self.alert_source_uuid}/signals"

    def storage_state_path_for(self, dashboard: DashboardConfig) -> Path:
        if dashboard.storage_state:
            path = Path(dashboard.storage_state)
            return path if path.is_absolute() else PROJECT_ROOT / path
        return self.storage_state_dir / f"{dashboard.id}.json"

    def ensure_dirs(self) -> None:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.storage_state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)


def load_dashboards(path: Path) -> list[DashboardConfig]:
    if not path.exists():
        raise FileNotFoundError(f"Dashboards file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("dashboards") or []
    if not items:
        raise ValueError(f"No dashboards defined in {path}")
    return [DashboardConfig.model_validate(item) for item in items]


@lru_cache
def get_settings() -> Settings:
    return Settings()
