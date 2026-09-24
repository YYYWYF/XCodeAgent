"""验证 Agent Runtime 公开入口集成探针的判定逻辑。"""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

from app.services.agent_runtime_integration_probe import (
    probe_direct_runtime_integration,
    public_invocation_paths,
)
from tests.agent_runtime_direct_test_utils import direct_technical_plan


class _FakeResponse:
    """提供探针所需的最小 HTTP 响应契约：状态、响应头和可逐行读取的事件流。"""

    def __init__(
        self,
        status: int = 200,
        headers: dict[str, str] | None = None,
        lines: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._stream = BytesIO(b"".join(lines or []))

    def readline(self) -> bytes:
        """按行返回事件流内容，模拟 SSE 的逐行读取。"""

        return self._stream.readline()

    def __enter__(self) -> "_FakeResponse":
        """支持 with 语句，与 urllib 的响应契约保持一致。"""

        return self

    def __exit__(self, *exc: object) -> bool:
        """退出上下文时不吞掉异常。"""

        return False


def _sse_line(event_type: str) -> bytes:
    """构造一行携带 AG-UI 事件名的 SSE 数据。"""

    return f"data: {json.dumps({'type': event_type})}\n\n".encode("utf-8")


def _allow_all_origin(origin: str = "http://localhost:5173") -> _FakeResponse:
    """构造放行指定前端源的 CORS 预检响应。"""

    return _FakeResponse(204, {"Access-Control-Allow-Origin": origin})


class PublicInvocationPathTests(unittest.TestCase):
    """验证只从 TechnicalPlan 读取声明为公开的 AG-UI 调用路径。"""

    def test_reads_only_public_invocations_and_deduplicates(self) -> None:
        """只取 exposure=public 的路径，保持声明顺序并去重。"""

        plan = {
            "agent_contracts": [
                {"invocation": {"exposure": "public", "path": "/agents/a/run"}},
                {
                    "invocation": {
                        "exposure": "internal",
                        "path": "/internal/agents/b/run",
                    }
                },
                {"invocation": {"exposure": "public", "path": "/agents/a/run"}},
                {"invocation": {"exposure": "public", "path": "/agents/c/run"}},
            ]
        }

        self.assertEqual(
            public_invocation_paths(plan), ("/agents/a/run", "/agents/c/run")
        )

    def test_returns_empty_without_public_invocation(self) -> None:
        """没有公开调用契约时必须返回空元组，而不是回退到猜测的路径。"""

        self.assertEqual(public_invocation_paths({"agent_contracts": []}), ())
        self.assertEqual(public_invocation_paths({}), ())


class ProbeVerdictTests(unittest.TestCase):
    """验证探针在各类真实响应下给出的通过或阻断判定。"""

    def _probe(
        self,
        responses: list[object],
        *,
        frontend_origin: str | None = "http://localhost:5173",
        technical_plan: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], object]:
        """在受控 urlopen 下运行探针，并返回结果与调用记录。"""

        with patch(
            "app.services.agent_runtime_integration_probe.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = probe_direct_runtime_integration(
                runtime_url="http://127.0.0.1:51000",
                frontend_origin=frontend_origin,
                technical_plan=technical_plan or direct_technical_plan(),
            )
        return result, urlopen

    def test_passing_probe_reads_public_path_and_ag_ui_handshake(self) -> None:
        """预检放行且公开入口返回 AG-UI 运行开始事件时判定通过。"""

        run = _FakeResponse(
            200,
            {"Content-Type": "text/event-stream"},
            [_sse_line("RUN_STARTED"), _sse_line("TEXT_MESSAGE_START")],
        )

        result, urlopen = self._probe([_allow_all_origin(), run])

        self.assertTrue(result["passed"])
        self.assertEqual(result["path"], "/ag-ui/agents/assistant")
        self.assertTrue(result["cors"]["passed"])
        self.assertEqual(result["protocol"]["events"][0], "RUN_STARTED")
        self.assertEqual(
            urlopen.call_args_list[1].args[0].full_url,
            "http://127.0.0.1:51000/ag-ui/agents/assistant",
        )

    def test_rejected_cors_preflight_blocks_before_calling_public_edge(self) -> None:
        """预检未回显前端源时必须阻断，且不再向公开入口发起调用。"""

        result, urlopen = self._probe([_FakeResponse(204, {})])

        self.assertFalse(result["passed"])
        self.assertIn("CORS", result["message"])
        self.assertIsNone(result["protocol"])
        self.assertEqual(urlopen.call_count, 1)

    def test_missing_frontend_origin_skips_preflight(self) -> None:
        """取不到前端源时跳过预检，避免用猜测的源误判。"""

        run = _FakeResponse(
            200, {"Content-Type": "text/event-stream"}, [_sse_line("RUN_STARTED")]
        )

        result, urlopen = self._probe([run], frontend_origin=None)

        self.assertTrue(result["passed"])
        self.assertTrue(result["cors"]["skipped"])
        self.assertEqual(urlopen.call_count, 1)

    def test_auth_rejection_counts_as_reachable_route(self) -> None:
        """认证终止点生效时的 401 同样证明调用路由可达。"""

        error = URLError("auth")
        result, _ = self._probe(
            [_allow_all_origin(), _http_error(401, error)]
        )

        self.assertTrue(result["passed"])
        self.assertTrue(result["protocol"]["authenticated"])

    def test_missing_route_is_reported_as_contract_mismatch(self) -> None:
        """公开入口没有该路径时判定为调用契约与实际路由不一致。"""

        result, _ = self._probe(
            [_allow_all_origin(), _http_error(404, URLError("missing"))]
        )

        self.assertFalse(result["passed"])
        self.assertIn("调用契约与实际路由不一致", result["message"])

    def test_non_event_stream_response_blocks(self) -> None:
        """返回 200 但不是事件流时必须阻断，避免把静态响应误判为 AG-UI。"""

        run = _FakeResponse(200, {"Content-Type": "application/json"}, [])

        result, _ = self._probe([_allow_all_origin(), run])

        self.assertFalse(result["passed"])
        self.assertIn("未按 AG-UI 返回事件流", result["message"])

    def test_event_stream_without_run_started_blocks(self) -> None:
        """事件流里没有 AG-UI 运行开始事件时判定协议未成立。"""

        run = _FakeResponse(
            200,
            {"Content-Type": "text/event-stream"},
            [_sse_line("TEXT_MESSAGE_CONTENT")],
        )

        result, _ = self._probe([_allow_all_origin(), run])

        self.assertFalse(result["passed"])
        self.assertIn("未出现 AG-UI 运行开始事件", result["message"])

    def test_connection_failure_blocks(self) -> None:
        """公开入口不可达时必须阻断预览就绪。"""

        result, _ = self._probe(
            [_allow_all_origin(), URLError("connection refused")]
        )

        self.assertFalse(result["passed"])
        self.assertIn("公开入口请求失败", result["message"])

    def test_missing_public_contract_blocks_without_any_request(self) -> None:
        """TechnicalPlan 未声明公开调用路径时不发起任何请求即阻断。"""

        result, urlopen = self._probe([], technical_plan={"agent_contracts": []})

        self.assertFalse(result["passed"])
        self.assertIn("未声明公开 AG-UI 调用路径", result["message"])
        self.assertEqual(urlopen.call_count, 0)


def _http_error(code: int, reason: object) -> object:
    """构造带响应头的 HTTPError，供探针读取状态与 Content-Type。"""

    from email.message import Message
    from urllib.error import HTTPError

    headers = Message()
    headers["Content-Type"] = "application/json"
    return HTTPError(
        url="http://127.0.0.1:51000/ag-ui/agents/assistant",
        code=code,
        msg=str(reason),
        hdrs=headers,
        fp=BytesIO(b""),
    )


if __name__ == "__main__":
    unittest.main()
