"""Unit 模型 SDK 故障到 Planning、AG-UI 和 Durable 诊断的回归。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import openai

from app.protocols.workflow import build_workflow_ag_ui_stream
from app.persistence.execution_recovery import get_execution
from app.services import planning_run as transitions
from app.services.dag_planning_orchestrator import DagPlanningError
from app.services.execution_failure_classifier import public_failure_diagnostic
from app.services.execution_recovery_projection import recovery_failure_diagnostic
from app.services.planning_recovery_contracts import build_planning_recovery_snapshot
from app.services.unit_generation import UnitGenerationInfrastructureError, generate_unit_candidate_once
from app.services.unit_generation_orchestrator import _infrastructure_issue
from app.services.unit_model_failure import (
    MODEL_CALL_FAILED, MODEL_CONNECTION_FAILED, MODEL_HTTP_ERROR,
    MODEL_REQUEST_TIMEOUT, MODEL_RESPONSE_INVALID, MODEL_SETUP_FAILED,
    classify_unit_model_failure, failure_evidence_from_issue,
)
from tests.planning_run_fixtures import AT, ready, run
from tests.test_unit_generation import _job, _settings


class UnitModelFailureTests(unittest.IsolatedAsyncioTestCase):
    """覆盖真实 SDK 类型、明确边界和同一失败的公开投影。"""

    def test_sdk_types_produce_only_supported_facts(self) -> None:
        """超时优先于连接，HTTP 响应与协议错误保留已证实状态。"""

        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        failed_response = httpx.Response(429, request=request)
        valid_response = httpx.Response(200, request=request)
        cases = (
            (openai.APITimeoutError(request=request), MODEL_REQUEST_TIMEOUT, None),
            (openai.APIConnectionError(request=request), MODEL_CONNECTION_FAILED, None),
            (openai.APIStatusError("secret-token", response=failed_response, body={"code": "rate_limit"}), MODEL_HTTP_ERROR, 429),
            (openai.APIResponseValidationError(valid_response, body={"secret": "secret-token"}), MODEL_RESPONSE_INVALID, 200),
            (anthropic.APITimeoutError(request=request), MODEL_REQUEST_TIMEOUT, None),
            (httpx.ConnectTimeout("late"), MODEL_REQUEST_TIMEOUT, None),
            (RuntimeError("secret-token"), MODEL_CALL_FAILED, None),
        )
        for error, code, status in cases:
            with self.subTest(error=type(error).__name__):
                fact = classify_unit_model_failure(error, stage="model_invoke")
                self.assertEqual(fact.code, code)
                self.assertEqual(fact.http_status, status)
                self.assertNotIn("secret-token", fact.message)
        self.assertEqual(
            classify_unit_model_failure(RuntimeError("bad key"), stage="model_setup").code,
            MODEL_SETUP_FAILED,
        )
        spoofed = RuntimeError("service status 503")
        spoofed.status_code = 503
        self.assertEqual(
            classify_unit_model_failure(spoofed, stage="model_invoke").code,
            MODEL_CALL_FAILED,
        )

    async def test_session_deadline_is_not_request_timeout(self) -> None:
        """整个 Unit 到期沿用基础设施码，不虚构 SDK 请求超时。"""

        job = _job()
        job = job.model_copy(update={
            "policy": job.policy.model_copy(update={"unit_session_timeout": 0.01}),
        })
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=self._never_return))
        with patch("app.services.unit_generation.build_unit_generation_prompt", return_value="test"), patch(
            "app.services.unit_generation.create_chat_model", return_value=model,
        ):
            with self.assertRaises(UnitGenerationInfrastructureError) as caught:
                await generate_unit_candidate_once(job, settings=_settings())
        self.assertEqual(caught.exception.stage, "unit_session")
        self.assertEqual(caught.exception.failure.code, "UNIT_GENERATION_INFRASTRUCTURE_FAILURE")

    async def _never_return(self, _prompt: str) -> None:
        """让测试中的 Unit Session 自身截止。"""

        await asyncio.Event().wait()

    async def test_sdk_boundary_reaches_ag_ui_and_durable_with_one_code(self) -> None:
        """实际 SDK 响应异常经 Unit、Issue、Dag、Runtime 和持久化保持同码。"""

        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        response = httpx.Response(429, request=request)
        sdk_error = openai.APIStatusError(
            "Authorization: Bearer secret-token", response=response,
            body={"code": "rate_limit"},
        )
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=sdk_error))
        with patch("app.services.unit_generation.build_unit_generation_prompt", return_value="test"), patch(
            "app.services.unit_generation.create_chat_model", return_value=model,
        ):
            with self.assertRaises(UnitGenerationInfrastructureError) as caught:
                await generate_unit_candidate_once(_job(), settings=_settings())
        issue = _infrastructure_issue(caught.exception)
        failed = transitions.fail(ready(transitions.begin_generation(run(), at=AT)), issue, at=AT)
        planning_error = DagPlanningError((failed.failure,), failed)
        evidence = failure_evidence_from_issue(failed.failure)
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(planning_error.code, MODEL_HTTP_ERROR)
        self.assertEqual(evidence.code, MODEL_HTTP_ERROR)
        self.assertEqual(evidence.http_status, 429)
        self.assertEqual(evidence.provider_error_code, "rate_limit")

        class FailingGraph:
            """将已确认的规划主失败交给真实 Workflow AG-UI Runtime。"""

            async def astream(self, _state, *, config, stream_mode):
                """在首个节点更新之前传播同一个 DagPlanningError。"""

                del config, stream_mode
                raise planning_error
                yield  # pragma: no cover

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "app.protocols.workflow.runtime.resolve_current_node_entry_boundary",
                new=AsyncMock(return_value=SimpleNamespace(target_node="prepare_build_tasks")),
            ):
                frames = [frame async for frame in build_workflow_ag_ui_stream(
                    graph=FailingGraph(),
                    payload={
                        "threadId": failed.thread_id,
                        "runId": failed.workflow_run_id,
                        "messages": [{"role": "user", "content": "生成执行计划"}],
                        "forwardedProps": {"workspaceRoot": directory, "sessionId": "session-1"},
                    },
                    accept="text/event-stream",
                )]
            public_frames = "".join(frames)
            self.assertIn('"type":"RUN_ERROR"', public_frames)
            self.assertIn('"type":"STATE_SNAPSHOT"', public_frames)
            self.assertIn('"name":"workflow-run"', public_frames)
            self.assertIn(MODEL_HTTP_ERROR, public_frames)
            self.assertIn('"httpStatus":429', public_frames)
            self.assertNotIn("secret-token", public_frames)
            emitted = [
                json.loads(line.removeprefix("data: "))
                for frame in frames for line in frame.splitlines()
                if line.startswith("data: ")
            ]
            custom = next(item for item in reversed(emitted) if item.get("type") == "CUSTOM" and item.get("name") == "workflow-run")
            snapshot = next(item for item in reversed(emitted) if item.get("type") == "STATE_SNAPSHOT")
            run_error = next(item for item in emitted if item.get("type") == "RUN_ERROR")
            self.assertEqual(custom["value"]["summary"]["errorCode"], MODEL_HTTP_ERROR)
            self.assertEqual(snapshot["snapshot"]["workflow"]["summary"]["errorCode"], MODEL_HTTP_ERROR)
            self.assertEqual(run_error["code"], MODEL_HTTP_ERROR)
            self.assertEqual(run_error["message"], "模型服务返回 HTTP 429。")
            record = await get_execution(directory, failed.workflow_run_id)
            self.assertIsNotNone(record)
            assert record is not None
            projected = recovery_failure_diagnostic(record)
            self.assertEqual(projected, public_failure_diagnostic(
                record.failure, source_run_id=failed.workflow_run_id,
            ))
            self.assertEqual(projected["code"], MODEL_HTTP_ERROR)
            self.assertNotIn("secret-token", json.dumps(projected))

    async def test_new_run_does_not_publish_previous_planning_failure(self) -> None:
        """R2 的异常若错误携带 R1 Snapshot，也不能发布 R1 的模型码。"""

        wrapped = UnitGenerationInfrastructureError(
            identity=_job().identity, stage="model_invoke",
            cause=RuntimeError("R1 private diagnostic"),
        )
        issue = _infrastructure_issue(wrapped)
        failed_r1 = transitions.fail(ready(transitions.begin_generation(run(), at=AT)), issue, at=AT)
        old_error = DagPlanningError((failed_r1.failure,), failed_r1)

        class StaleGraph:
            """模拟 R2 执行过程中错误传播的 R1 规划快照。"""

            async def astream(self, _state, *, config, stream_mode):
                """直接向 Runtime 传播错误的旧 Run 快照。"""

                del config, stream_mode
                raise old_error
                yield  # pragma: no cover

        frames = [frame async for frame in build_workflow_ag_ui_stream(
            graph=StaleGraph(),
            payload={
                "threadId": failed_r1.thread_id, "runId": "workflow-R2",
                "messages": [{"role": "user", "content": "重试"}],
            },
            accept="text/event-stream",
        )]
        events = [
            json.loads(line.removeprefix("data: "))
            for frame in frames for line in frame.splitlines() if line.startswith("data: ")
        ]
        run_error = next(item for item in events if item.get("type") == "RUN_ERROR")
        self.assertEqual(run_error["code"], "WORKFLOW_RUN_FAILED")
        self.assertNotIn(MODEL_CALL_FAILED, "".join(frames))


__all__ = ["UnitModelFailureTests"]
