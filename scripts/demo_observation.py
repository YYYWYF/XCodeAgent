"""离线展示含英文 current_operation 的模型 JSON；无需真实模型或追踪账号。"""

import json
import sys
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from httpx_sse import connect_sse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.main import create_app
from app.upstream import Upstream


def mock_model(request: httpx.Request) -> httpx.Response:
    """仅在内存模拟内容流及独立 usage 尾帧，不建立上游网络连接。"""
    base = {"id": "chatcmpl-offline-demo", "object": "chat.completion.chunk", "created": 1, "model": "demo-model"}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "离线演示完成"}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {**base, "choices": [], "usage": {"prompt_tokens": 284, "completion_tokens": 45, "total_tokens": 329}},
    ]
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def main() -> None:
    """通过完整本地 AG-UI 路由输出真实格式，临时历史在结束后自动清理。"""
    with tempfile.TemporaryDirectory() as directory:
        settings = Settings(_env_file=None, model_upstream="direct", direct_base_url="https://offline.example.test/v1",
                            direct_model_name="demo-model", direct_api_key="offline-fake-key",
                            service_token="offline-demo-token",
                            history_db_path=Path(directory) / "history.sqlite3")
        app = create_app(settings, Upstream(settings, httpx.MockTransport(mock_model)))
        payload = {
            "threadId": "demo-thread", "runId": "demo-run", "messages": [], "tools": [], "context": [],
            "forwardedProps": {"modelInference": {"request": {
                "messages": [{"role": "system", "content": "You are the technical-planning model for an app-generation workflow."},
                             {"role": "user", "content": "离线测试"}], "stream": True,
            }, "maxRetries": 0, "metadata": {"projectId": "demo-application"}}},
        }
        with TestClient(app) as client:
            assert client.get("/health").json()["status"] == "ok"
            with connect_sse(client, "POST", "/model-inference/run", json=payload,
                             headers={"Authorization": "Bearer offline-demo-token"}) as source:
                source.response.raise_for_status()
                events = [json.loads(event.data) for event in source.iter_sse()]
            assert events[-1]["type"] == "RUN_FINISHED"
            assert app.state.history.get("demo-run")["status"] == "completed"


if __name__ == "__main__":
    main()
