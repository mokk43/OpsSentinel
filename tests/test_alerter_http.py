from monitor.alerter import Alerter
from monitor.config import Settings


def test_post_signal_success(monkeypatch):
    settings = Settings(
        alert_base_url="http://127.0.0.1:9780",
        alert_source_uuid="src-1",
        alert_push_credential="secret",
    )
    alerter = Alerter(settings)

    class FakeResp:
        status_code = 200
        text = "ok"

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return FakeResp()

    monkeypatch.setattr(alerter._session, "post", fake_post)
    assert alerter.post_signal("Aliyun.OPS", "hello") is True
    assert calls[0]["url"].endswith("/v1/sources/src-1/signals")
    assert calls[0]["json"]["name"] == "Aliyun.OPS"
    assert calls[0]["json"]["message"] == "hello"
    assert "occurredAt" in calls[0]["json"]
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    alerter.close()


def test_post_signal_retries_then_fails(monkeypatch):
    settings = Settings(
        alert_base_url="http://127.0.0.1:9780",
        alert_source_uuid="src-1",
        alert_push_credential="secret",
    )
    alerter = Alerter(settings)

    class FakeResp:
        status_code = 500
        text = "nope"

    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResp()

    monkeypatch.setattr(alerter._session, "post", fake_post)
    monkeypatch.setattr("monitor.alerter.time.sleep", lambda *_: None)
    assert alerter.post_signal("Aliyun.OPS.Monitor", "x") is False
    alerter.close()
