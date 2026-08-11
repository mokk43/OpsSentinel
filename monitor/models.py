"""Shared domain models for analysis results."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Severity = Literal["critical", "high"]
Status = Literal["critical", "warning", "healthy"]


class Issue(BaseModel):
    component: str
    issue: str
    severity: Severity
    evidence: str


class AnalysisResult(BaseModel):
    status: Status
    has_critical_issues: bool
    issues: list[Issue] = Field(default_factory=list)
    summary: str = ""

    @field_validator("issues", mode="before")
    @classmethod
    def _none_issues(cls, v: Any) -> Any:
        return v or []

    @model_validator(mode="after")
    def _align_critical_flag(self) -> AnalysisResult:
        critical_present = any(i.severity == "critical" for i in self.issues)
        # Trust computed flag over model mistakes
        object.__setattr__(self, "has_critical_issues", critical_present)
        if critical_present and self.status == "healthy":
            object.__setattr__(self, "status", "critical")
        return self
