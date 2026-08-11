import json

import pytest

from monitor.analyzer import parse_analysis_text, strip_code_fences


def test_parse_plain_json():
    raw = json.dumps(
        {
            "status": "healthy",
            "has_critical_issues": False,
            "issues": [],
            "summary": "ok",
        }
    )
    result = parse_analysis_text(raw)
    assert result.status == "healthy"
    assert result.has_critical_issues is False


def test_parse_fenced_json():
    raw = """```json
{"status": "warning", "has_critical_issues": false, "issues": [], "summary": "warn"}
```"""
    result = parse_analysis_text(raw)
    assert result.status == "warning"
    assert strip_code_fences(raw).startswith("{")


def test_parse_invalid():
    with pytest.raises(Exception):
        parse_analysis_text("not json at all")
