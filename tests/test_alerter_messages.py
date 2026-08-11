from monitor.alerter import format_board_message, format_monitor_message
from monitor.models import AnalysisResult, Issue


def test_board_message_critical_only():
    analysis = AnalysisResult(
        status="critical",
        has_critical_issues=True,
        summary="RDS and svc down",
        issues=[
            Issue(
                component="RDS",
                issue="CPU high",
                severity="critical",
                evidence="CPU 88%",
            ),
            Issue(
                component="cache",
                issue="elevated latency",
                severity="high",
                evidence="p99 200ms",
            ),
        ],
    )
    msg = format_board_message("aliyun-ops", "https://example/ops", analysis)
    assert msg.startswith("[aliyun-ops] RDS and svc down")
    assert "URL: https://example/ops" in msg
    assert "RDS | CPU high | CPU 88%" in msg
    assert "cache" not in msg


def test_board_message_truncation():
    issues = [
        Issue(
            component=f"c{i}",
            issue="down",
            severity="critical",
            evidence="x" * 200,
        )
        for i in range(50)
    ]
    analysis = AnalysisResult(
        status="critical",
        has_critical_issues=True,
        summary="many",
        issues=issues,
    )
    msg = format_board_message("b", "https://u", analysis, max_chars=500)
    assert len(msg) <= 500
    assert "more" in msg or "truncated" in msg


def test_monitor_message():
    msg = format_monitor_message("aliyun-ops", "auth", "login wall", detail="url=/login")
    assert msg.startswith("[aliyun-ops] auth: login wall")
    assert "Detail: url=/login" in msg


def test_has_critical_realigned_from_issues():
    analysis = AnalysisResult(
        status="healthy",
        has_critical_issues=False,
        summary="x",
        issues=[
            Issue(
                component="svc",
                issue="DOWN",
                severity="critical",
                evidence="red DOWN",
            )
        ],
    )
    assert analysis.has_critical_issues is True
    assert analysis.status == "critical"
