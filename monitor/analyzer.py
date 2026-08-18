"""LLM vision analysis of dashboard screenshots."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from loguru import logger
from openai import OpenAI
from pydantic import ValidationError

from monitor.config import Settings
from monitor.models import AnalysisResult

SYSTEM_PROMPT = """You are an SRE assistant. Analyze OPS dashboard screenshots and flag only issues that need immediate human attention.

Detect (use severity "critical" when clearly visible):
- DOWN / FAILED / ERROR services (red or crossed-out indicators)
- 全租户 Top 50X API has obvious spikes, single bar's value is more than 100
- Resource use >90% (CPU, memory, disk, network), except for ES数据节点 group
- For ES数据节点 group, CPU usage >120% is critical
- Latency spikes (>5x baseline) when baseline/labels are visible
- Error rate >5%
- DB/connection pool failures
- RDS CPU usage >70%
- Growing queue backlog
- Unexpected node/pod/instance drop
- Visible text: critical, fatal, outage, unreachable, timeout

Ignore:
- Green/healthy states
- Yellow warnings marked acknowledged or scheduled
- Normal metrics
- Resolved/historical incidents

Rules:
- Return strict JSON only. No markdown fences. No commentary.
- Do not invent issues. Evidence must quote exact on-screen text or an unambiguous visual cue.
- If unsure whether something is critical, use severity "high", not "critical".
- has_critical_issues must be true if and only if at least one issue has severity "critical".
- If fully healthy: status "healthy", has_critical_issues false, issues [].

JSON schema:
{
  "status": "critical" | "warning" | "healthy",
  "has_critical_issues": true | false,
  "issues": [
    {
      "component": "<name>",
      "issue": "<short problem>",
      "severity": "critical" | "high",
      "evidence": "<exact text/visual cue from the image>"
    }
  ],
  "summary": "<one-sentence health assessment>"
}
"""

USER_PROMPT = "Analyze this OPS dashboard for critical issues. Return JSON only."


class AnalysisError(Exception):
    """LLM call or parse failed after in-cycle retry."""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
        # Also handle single leading/trailing fence lines manually
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_analysis_text(text: str) -> AnalysisResult:
    cleaned = strip_code_fences(text)
    # Try direct JSON; if extra prose, attempt first {...} blob
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(cleaned[start : end + 1])
        else:
            raise
    return AnalysisResult.model_validate(data)


def _image_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    if mime == "jpg":
        mime = "jpeg"
    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


class Analyzer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.llm_api_key:
            logger.warning("LLM_API_KEY is empty")
        self.client = OpenAI(
            api_key=settings.llm_api_key or "missing",
            base_url=settings.llm_base_url,
        )

    def _complete(self, image_path: Path, *, reinforce_json: bool = False) -> str:
        if not self.settings.llm_model:
            raise AnalysisError("LLM_MODEL is not configured")

        user_text = USER_PROMPT
        if reinforce_json:
            user_text = (
                USER_PROMPT
                + " Previous reply was not valid JSON. Reply with a single JSON object only."
            )

        image_url = _image_data_url(image_path)
        response = self.client.chat.completions.create(
            model=self.settings.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": user_text}
                    ]
                },
            ],
            temperature=0.6,
            # Moonshot/Kimi thinking is not an OpenAI SDK param — pass via extra_body.
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = response.choices[0].message.content
        if not content:
            raise AnalysisError("Empty LLM response content")
        return content

    def analyze(self, image_path: Path) -> AnalysisResult:
        """
        Analyze screenshot. One in-cycle retry on parse/format failure.
        Raises AnalysisError if still invalid (counts as one failed cycle toward K=2).
        """
        raw = ""
        try:
            raw = self._complete(image_path, reinforce_json=False)
            return parse_analysis_text(raw)
        except (AnalysisError, ValidationError, json.JSONDecodeError, OSError) as first_exc:
            logger.warning(
                "LLM analysis parse/call failed once: {}; retrying with JSON nudge",
                first_exc,
            )
            try:
                raw = self._complete(image_path, reinforce_json=True)
                return parse_analysis_text(raw)
            except (AnalysisError, ValidationError, json.JSONDecodeError, OSError) as second_exc:
                excerpt = (raw or str(second_exc))[:800]
                raise AnalysisError(
                    f"LLM analysis failed after retry: {second_exc}",
                    raw=excerpt,
                ) from second_exc
