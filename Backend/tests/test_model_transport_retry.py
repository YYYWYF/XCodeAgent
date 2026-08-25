"""模型传输层重试：断连类错误有界重试，内容类错误原样抛出。"""

import httpx
import pytest

from app.services.model_transport_retry import run_with_transport_retry


def test_transport_error_retries_until_success(monkeypatch):
    monkeypatch.setattr("app.services.model_transport_retry.time.sleep", lambda _: None)
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.RemoteProtocolError("incomplete chunked read")
        return "ok"

    assert run_with_transport_retry(flaky, operation_name="测试调用") == "ok"
    assert attempts["count"] == 3


def test_transport_error_raises_last_error_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr("app.services.model_transport_retry.time.sleep", lambda _: None)
    attempts = {"count": 0}

    def always_broken() -> str:
        attempts["count"] += 1
        raise httpx.ReadError("connection reset")

    with pytest.raises(httpx.ReadError):
        run_with_transport_retry(always_broken, attempts=3, operation_name="测试调用")
    assert attempts["count"] == 3


def test_content_error_is_not_retried():
    attempts = {"count": 0}

    def bad_json() -> str:
        attempts["count"] += 1
        raise ValueError("模型未返回合法 JSON")

    with pytest.raises(ValueError, match="合法 JSON"):
        run_with_transport_retry(bad_json, operation_name="测试调用")
    assert attempts["count"] == 1


def test_success_on_first_attempt_does_not_retry():
    attempts = {"count": 0}

    def fine() -> str:
        attempts["count"] += 1
        return "ok"

    assert run_with_transport_retry(fine, operation_name="测试调用") == "ok"
    assert attempts["count"] == 1
