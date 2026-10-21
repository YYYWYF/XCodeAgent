"""不连接真实模型或 LangSmith 的测试夹具。"""

import json
from pathlib import Path
from typing import Any

import httpx
from httpx_sse import connect_sse

from app.config import Settings


def settings(path: Path, **overrides: Any) -> Settings:
    """创建隔离 SQLite 与假凭证配置，忽略部署环境文件。"""
    return Settings(_env_file=None, direct_api_key="test-direct-secret",
                    history_db_path=path / "history.sqlite3", **overrides)


def envelope(run_id: str = "run-1", streaming: bool = True, metadata: dict | None = None) -> dict:
    """构造与 Studio 适配器一致的官方 AG-UI 输入。"""
    return {"threadId": "thread-1", "runId": run_id, "messages": [], "tools": [], "context": [],
            "forwardedProps": {"modelInference": {"request": {
                "model": "caller-model-ignored", "messages": [{"role": "user", "content": "你好"}],
                "stream": streaming, "thinking": {"type": "disabled"}},
                "timeoutSeconds": 30, "maxRetries": 0, "metadata": metadata or {}}}}


def chunk(delta: dict | None = None, finish_reason: str | None = None, usage: dict | None = None) -> dict:
    """生成标准上游 Chat Completion 分片。"""
    result = {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1,
              "model": "upstream-model", "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}]}
    if usage:
        result["usage"] = usage
    return result


def completion() -> dict:
    """生成完整非流式响应，包含工具调用和用量。"""
    return {"id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": "upstream-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "你好",
                         "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "search", "arguments": '{"q":"a"}'}}]},
                         "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}


def sse_response(chunks: list[dict]) -> httpx.Response:
    """只在模拟上游中编码官方 SDK 可读 SSE，不实现产品 SSE 解析器。"""
    body = "".join(f"data: {json.dumps(item)}\n\n" for item in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def read_events(client: Any, body: dict, token: str = "local-model-transmit-dev-token") -> list[dict]:
    """使用标准 SSE 客户端读取服务事件，测试不手写 SSE 拆包。"""
    with connect_sse(client, "POST", "/model-inference/run", json=body,
                     headers={"Authorization": f"Bearer {token}"}) as source:
        return [json.loads(event.data) for event in source.iter_sse()]
