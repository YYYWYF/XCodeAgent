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
from app.services.planning_issues import ValidationIssue
from app.services.unit_model_failure import (
    LEGACY_INFRASTRUCTURE_FAILURE, MODEL_CALL_FAILED, MODEL_CONNECTION_FAILED,
    MODEL_HTTP_ERROR, MODEL_REQUEST_TIMEOUT, MODEL_RESPONSE_INVALID,
    MODEL_SETUP_FAILED, classify_unit_model_failure,
    failure_evidence_from_issue, unit_session_timeout_failure,
)
from tests.planning_run_fixtures import AT, ready, run
from tests.test_unit_generation import _job, _settings


class UnitModelFailureTests(unittest.IsolatedAsyncioTestCase):
    """覆盖真实 SDK 类型、明确边界和同一失败的公开投影。"""

    def test_sdk_types_produce_only_supported_facts(self) -> None:
        """超时优先于连接，HTTP 状态和响应协议错误只保留 SDK 证据。"""

        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        failed_response = httpx.Response(429, request=request)
        valid_response = httpx.Response(200, request=request)
        missing_response = openai.APIConnectionError(request=request)
        cases = (
            (openai.APITimeoutError(request=request), MODEL_REQUEST_TIMEOUT, None),
            (openai.APIConnectionError(request=request), MODEL_CONNECTION_FAILED, None),
            (openai.APIStatusError("secret-token", response=failed_response, body={"code": "rate_limit"}), MODEL_HTTP_ERROR, 429),
            (openai.APIResponseValidationError(valid_response, body={"secret": "secret-token"}), MODEL_RESPONSE_INVALID, 200),
            (anthropic.APITimeoutError(request=request), MODEL_REQUEST_TIMEOUT, None),
            (httpx.ConnectTimeout("late"), MODEL_REQUEST_TIMEOUT, None),
            (missing_response, MODEL_CONNECTION_FAILED, None),
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
        self.assertIsNone(classify_unit_model_failure(spoofed, stage="model_invoke").http_status)

    def test_http_200_sdk_stream_errors_are_call_failures(self) -> None:
        """用项目安装的 OpenAI 与 Anthropic SDK 验证 HTTP 200 流内错误形状。"""

        request = httpx.Request("POST", "https://example.invalid/v1/messages")
        anthropic_client = anthropic.Anthropic(api_key="test-token")
        anthropic_response = httpx.Response(
            200,
            request=request,
            content=(
                b'event: error\ndata: '
                b'{"type":"error","error":{"type":"api_error",'
                b'"message":"private-stream-body"}}\n\n'
            ),
        )
        anthropic_stream = anthropic.Stream(
            cast_to=dict, response=anthropic_response, client=anthropic_client,
        )
        try:
            with self.assertRaises(anthropic.APIStatusError) as anthropic_error:
                next(anthropic_stream)
        finally:
            anthropic_client.close()

        anthropic_fact = classify_unit_model_failure(
            anthropic_error.exception, stage="model_invoke",
        )
        self.assertEqual(anthropic_fact.code, MODEL_CALL_FAILED)
        self.assertEqual(anthropic_fact.http_status, 200)
        self.assertEqual(anthropic_fact.provider_error_code, "api_error")
        self.assertNotIn("HTTP 200", anthropic_fact.message)
        self.assertNotIn("private-stream-body", anthropic_fact.message)

        openai_client = openai.OpenAI(api_key="test-token")
        openai_response = httpx.Response(
            200,
            request=request,
            content=(
                b'data: {"error":{"code":"stream_error",'
                b'"message":"private-openai-stream-body"}}\n\n'
            ),
        )
        openai_stream = openai.Stream(
            cast_to=dict, response=openai_response, client=openai_client,
        )
        try:
            with self.assertRaises(openai.APIError) as openai_error:
                next(openai_stream)
        finally:
            openai_client.close()

        openai_fact = classify_unit_model_failure(
            openai_error.exception, stage="model_invoke",
        )
        self.assertNotIsInstance(openai_error.exception, openai.APIStatusError)
        self.assertEqual(openai_fact.code, MODEL_CALL_FAILED)
        self.assertIsNone(openai_fact.http_status)
        self.assertEqual(openai_fact.provider_error_code, "stream_error")
        self.assertNotIn("HTTP 200", openai_fact.message)
        self.assertNotIn("private-openai-stream-body", openai_fact.message)

    def test_legacy_infrastructure_code_is_not_always_a_timeout(self) -> None:
        """旧通用码只在可信 Unit Session deadline 阶段显示超时。"""

        for stage in ("model_setup", "model_invoke"):
            issue = ValidationIssue(
                code=LEGACY_INFRASTRUCTURE_FAILURE,
                level="system",
                category="infrastructure",
                retryable=False,
                message="旧记录曾使用的摘要",
                details={"stage": stage},
            )
            evidence = failure_evidence_from_issue(issue)
            self.assertIsNotNone(evidence)
            assert evidence is not None
            self.assertEqual(evidence.stage, stage)
            self.assertNotIn("超时", evidence.diagnostic_message or "")

        no_stage_issue = ValidationIssue(
            code=LEGACY_INFRASTRUCTURE_FAILURE,
            level="system",
            category="infrastructure",
            retryable=False,
            message="旧记录原摘要",
        )
        old_evidence = failure_evidence_from_issue(no_stage_issue)
        self.assertIsNotNone(old_evidence)
        assert old_evidence is not None
        self.assertIsNone(old_evidence.stage)
        self.assertNotIn("超时", old_evidence.diagnostic_message or "")
        self.assertIn("超时", unit_session_timeout_failure().message)

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
        self.assertIn("超时", caught.exception.failure.message)
        session_issue = _infrastructure_issue(caught.exception)
        session_evidence = failure_evidence_from_issue(session_issue)
        self.assertIsNotNone(session_evidence)
        assert session_evidence is not None
        self.assertEqual(session_evidence.stage, "unit_session")
        self.assertIn("超时", session_evidence.diagnostic_message or "")

    async def _never_return(self, _prompt: str) -> None:
        """让测试中的 Unit Session 自身截止。"""

        await asyncio.Event().wait()

    async def test_sdk_boundary_reaches_ag_ui_and_durable_with_one_code(self) -> None:
        """实际 Anthropic HTTP 200 流内错误穿过 Issue、AG-UI 与持久化保持同一事实。"""

        request = httpx.Request("POST", "https://example.invalid/v1/messages")
        sdk_client = anthropic.Anthropic(api_key="test-token")
        sdk_response = httpx.Response(
            200,
            request=request,
            content=(
                b'event: error\ndata: '
                b'{"type":"error","error":{"type":"api_error",'
                b'"message":"private-provider-body"}}\n\n'
            ),
        )
        sdk_stream = anthropic.Stream(
            cast_to=dict, response=sdk_response, client=sdk_client,
        )
        try:
            with self.assertRaises(anthropic.APIStatusError) as raised:
                next(sdk_stream)
        finally:
            sdk_client.close()
        sdk_error = raised.exception

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
        self.assertEqual(planning_error.code, MODEL_CALL_FAILED)
        self.assertEqual(evidence.code, MODEL_CALL_FAILED)
        self.assertEqual(evidence.stage, "model_invoke")
        self.assertEqual(evidence.http_status, 200)
        self.assertEqual(evidence.provider_error_code, "api_error")
        self.assertEqual(evidence.diagnostic_message, "模型调用失败，未能确定具体原因。")

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
            self.assertIn(MODEL_CALL_FAILED, public_frames)
            self.assertIn('"httpStatus":200', public_frames)
            self.assertIn('"stage":"model_invoke"', public_frames)
            self.assertNotIn("HTTP 200", public_frames)
            self.assertNotIn("private-provider-body", public_frames)
            emitted = [
                json.loads(line.removeprefix("data: "))
                for frame in frames for line in frame.splitlines()
                if line.startswith("data: ")
            ]
            custom = next(item for item in reversed(emitted) if item.get("type") == "CUSTOM" and item.get("name") == "workflow-run")
            snapshot = next(item for item in reversed(emitted) if item.get("type") == "STATE_SNAPSHOT")
            run_error = next(item for item in emitted if item.get("type") == "RUN_ERROR")
            custom_diagnostic = custom["value"]["summary"]["failureDiagnostic"]
            snapshot_diagnostic = snapshot["snapshot"]["workflow"]["summary"]["failureDiagnostic"]
            self.assertEqual(custom["value"]["summary"]["errorCode"], MODEL_CALL_FAILED)
            self.assertEqual(snapshot["snapshot"]["workflow"]["summary"]["errorCode"], MODEL_CALL_FAILED)
            self.assertEqual(custom_diagnostic, snapshot_diagnostic)
            self.assertEqual(custom_diagnostic["stage"], "model_invoke")
            self.assertEqual(custom_diagnostic["httpStatus"], 200)
            self.assertEqual(custom_diagnostic["message"], "模型调用失败，未能确定具体原因。")
            self.assertEqual(run_error["code"], MODEL_CALL_FAILED)
            self.assertEqual(run_error["message"], "模型调用失败，未能确定具体原因。")
            record = await get_execution(directory, failed.workflow_run_id)
            self.assertIsNotNone(record)
            assert record is not None
            projected = recovery_failure_diagnostic(record)
            self.assertEqual(projected, public_failure_diagnostic(
                record.failure, source_run_id=failed.workflow_run_id,
            ))
            self.assertEqual(projected["code"], MODEL_CALL_FAILED)
            self.assertEqual(projected["stage"], "model_invoke")
            self.assertEqual(projected["httpStatus"], 200)
            for field in ("code", "stage", "httpStatus", "providerErrorCode", "message"):
                self.assertEqual(projected[field], custom_diagnostic[field], field)
            self.assertNotIn("private-provider-body", json.dumps(projected))

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
