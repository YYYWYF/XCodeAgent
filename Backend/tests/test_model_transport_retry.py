"""模型传输层重试：断连类错误有界重试，内容类错误原样抛出。"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from ag_ui.core import CustomEvent, RunFinishedEvent, RunStartedEvent
from ag_ui.encoder import EventEncoder
from langchain.agents import create_agent

from app.agents.transmit_chat_model import TransmitChatModel
from app.graph.nodes.direct_modification import respond_to_workspace_question
from app.protocols.temporary_chat import build_temporary_chat_ag_ui_stream
from app.services.application_lifecycle import ensure_application_lifecycle
from app.services.model_transmit_client import ModelTransmitClient, ModelTransmitError
from app.services.model_transport_retry import run_with_transport_retry
from app.services.run_workers import bind_run_workers


def test_transmit_workspace_question_projects_identity_for_both_entry_points(monkeypatch, tmp_path):
    """经真实 Agent 和转发请求校验普通、临时问答，以及无项目工作区之间的身份隔离。"""
    requests = []

    def handle(request):
        """收集实际 HTTP 请求并返回最小合法 AG-UI 响应。"""
        payload = json.loads(request.content)
        if request.url.path == "/model-info/run":
            name, value = "model-info.result", {"model": "test-model", "provider": "openai", "upstream": "direct"}
        else:
            requests.append(payload["forwardedProps"]["modelInference"]["metadata"])
            name, value = "model-inference.result", {"choices": [
                {"message": {"role": "assistant", "content": "工作区问答结果"}, "finish_reason": "stop"},
            ]}
        ids = {"thread_id": payload["threadId"], "run_id": payload["runId"]}
        encoder = EventEncoder()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(
            encoder.encode(event) for event in [
                RunStartedEvent(**ids), CustomEvent(name=name, value=value), RunFinishedEvent(**ids),
            ]
        ))

    model = TransmitChatModel(transmit_url="https://transmit.test", transport=httpx.MockTransport(handle))
    agent = create_agent(model=model)
    monkeypatch.setattr("app.agents.create_agent_bundle", lambda *_: SimpleNamespace(workspace_assistant=agent))

    async def temporary_question(workspace):
        """消费临时问答的正式 AG-UI 流，确认工作线程也成功返回。"""
        stream = build_temporary_chat_ag_ui_stream(payload={"forwardedProps": {
            "temporaryChat": {"workspaceRoot": str(workspace), "request": "这个项目怎么构建？"},
        }})
        frames = [frame async for frame in stream]
        assert '"status":"completed"' in "".join(frames)

    regular, temporary, unregistered = (tmp_path / name for name in ("regular", "temporary", "unregistered"))
    for root in (regular, temporary, unregistered):
        root.mkdir()
    ensure_application_lifecycle(regular, application_id="app-regular", application_name="普通问答")
    ensure_application_lifecycle(temporary, application_id="app-temporary", application_name="临时问答")
    with bind_run_workers("regular-run", str(regular)):
        result = respond_to_workspace_question({"workspace": str(regular), "request": "前端用什么？"})
    assert result["status"] == "completed"
    asyncio.run(temporary_question(temporary))
    asyncio.run(temporary_question(unregistered))
    model.invoke("已显式绑定项目", config={"metadata": {"project_id": "app-explicit", "workspace": str(regular)}})

    assert [request.get("projectId") for request in requests] == ["app-regular", "app-temporary", None, "app-explicit"]
    for request in requests[:3]:
        assert request["extensions"]["context"]["business_operation"] == "answer_workspace_question"


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



def _consume_transmit(client, *, asynchronous, name):
    """从同一模型适配入口消费同步或异步事件，避免绕过 HTTP 与生命周期校验。"""
    payload = client.payload("thread-test", "run-test", {})
    if not asynchronous:
        return list(client.iterate(f"/{name}/run", payload, result_name=f"{name}.result"))

    async def consume():
        """收齐异步事件，让测试同时覆盖 aiterate 的响应读取和关闭。"""
        return [item async for item in client.aiterate(f"/{name}/run", payload, result_name=f"{name}.result")]

    return asyncio.run(consume())


def _service_error_response(name, value, *, status=200):
    """使用官方 AG-UI 编码器构造服务错误，并让 HTTP 错误体保持未读取。"""
    error = CustomEvent(name=f"{name}.error", value=value)
    if status != 200:
        body = error.model_dump_json(by_alias=True).encode()
        return httpx.Response(status, headers={"content-type": "application/json"}, stream=httpx.ByteStream(body))
    encoder = EventEncoder()
    body = encoder.encode(RunStartedEvent(thread_id="thread-test", run_id="run-test")) + encoder.encode(error)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(body.encode()))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("name", ["model-inference", "model-info"])
@pytest.mark.parametrize("code,retryable", [
    ("UPSTREAM_TIMEOUT", True), ("UPSTREAM_CONNECTION_ERROR", True), ("UPSTREAM_RATE_LIMIT", True),
    ("UPSTREAM_AUTH_ERROR", False), ("UPSTREAM_PROTOCOL_ERROR", False), ("CUSTOM_500", False), (0, False),
])
def test_transmit_sse_error_keeps_current_code_and_message(asynchronous, name, code, retryable):
    """两种接口和消费方式保留当前错误字段，未知业务码及数值零不被改写。"""
    responses = []

    def handler(request):
        """每次仅模拟一个模型转发请求，不访问真实接口。"""
        response = _service_error_response(name, {"returnCode": code, "errorMsg": "Internal Server Error. "})
        responses.append(response)
        return response

    client = ModelTransmitClient("https://transmit.test", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelTransmitError) as caught:
        _consume_transmit(client, asynchronous=asynchronous, name=name)
    assert caught.value.code == str(code)
    assert str(caught.value) == "Internal Server Error. "
    assert caught.value.retryable is retryable
    assert caught.value.status_code is None
    assert len(responses) == 1 and responses[0].is_closed


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("name", ["model-inference", "model-info"])
@pytest.mark.parametrize("status,code", [(401, "REQUEST_AUTH_ERROR"), (413, "REQUEST_TOO_LARGE"),
                                         (422, "REQUEST_VALIDATION_ERROR")])
def test_transmit_http_rejection_reads_current_error_event(asynchronous, name, status, code):
    """HTTP 拒绝读取 JSON 事件中的具体提示，并以模型异常保留真实状态。"""
    responses = []

    def handler(request):
        """使用未读取的 HTTP 字节流检查同步 read 与异步 aread 分支。"""
        response = _service_error_response(name, {"returnCode": code, "errorMsg": "请求被转发服务拒绝"}, status=status)
        responses.append(response)
        return response

    client = ModelTransmitClient("https://transmit.test", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelTransmitError) as caught:
        _consume_transmit(client, asynchronous=asynchronous, name=name)
    assert caught.value.code == code
    assert str(caught.value) == "请求被转发服务拒绝"
    assert caught.value.status_code == status
    assert not caught.value.retryable
    assert len(responses) == 1 and responses[0].is_closed


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("value", [
    {"code": "UPSTREAM_TIMEOUT", "message": "旧字段"},
    {"returnCode": "UPSTREAM_TIMEOUT", "errorMsg": "当前错误", "retryable": True},
    {"returnCode": None, "errorMsg": "空错误码"},
    {"returnCode": "UPSTREAM_TIMEOUT", "errorMsg": " "},
])
def test_transmit_invalid_error_payload_is_not_a_retryable_network_failure(asynchronous, value):
    """拒绝旧字段和不完整合同，不把协议问题包装成可自动重试的网络异常。"""
    client = ModelTransmitClient("https://transmit.test", transport=httpx.MockTransport(
        lambda request: _service_error_response("model-inference", value)))
    with pytest.raises(ModelTransmitError) as caught:
        _consume_transmit(client, asynchronous=asynchronous, name="model-inference")
    assert caught.value.code == "INVALID_MODEL_RESULT"
    assert not caught.value.retryable


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("name", ["model-inference", "model-info"])
def test_transmit_success_keeps_complete_run_lifecycle(asynchronous, name):
    """错误合同调整后，两类正常模型结果继续通过完整生命周期交付。"""
    value = {"model": "test-model", "provider": "openai", "upstream": "direct"} if name == "model-info" else {
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
    encoder = EventEncoder()
    body = "".join(encoder.encode(event) for event in [
        RunStartedEvent(thread_id="thread-test", run_id="run-test"),
        CustomEvent(name=f"{name}.result", value=value),
        RunFinishedEvent(thread_id="thread-test", run_id="run-test"),
    ])
    client = ModelTransmitClient("https://transmit.test", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())))
    assert _consume_transmit(client, asynchronous=asynchronous, name=name) == [("result", value)]


@pytest.mark.parametrize("code,attempts", [("UPSTREAM_TIMEOUT", 3), ("UPSTREAM_CONNECTION_ERROR", 3),
                                           ("UPSTREAM_AUTH_ERROR", 1), ("REQUEST_VALIDATION_ERROR", 1),
                                           ("CUSTOM_500", 1)])
def test_transmit_current_errors_keep_existing_bounded_retry_policy(monkeypatch, code, attempts):
    """新错误字段接入原网络重试边界，认证、参数和未知业务失败只尝试一次。"""
    monkeypatch.setattr("app.services.model_transport_retry.time.sleep", lambda _: None)
    calls = []

    def operation():
        """把真实合同解析后的异常交给既有业务重试函数。"""
        calls.append(code)
        raise ModelTransmitError.from_payload({"returnCode": code, "errorMsg": "模型服务失败"})

    with pytest.raises(ModelTransmitError) as caught:
        run_with_transport_retry(operation)
    assert len(calls) == attempts
    assert caught.value.code == code
