"""T9.1 单 Unit 真异步模型调用、取消与超时语义回归。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.unit_generation import (
    UnitGenerationInfrastructureError,
    generate_unit_candidate_once,
)
from app.services.unit_generation_contracts import (
    UnitAttemptJob,
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from tests.test_unit_generation import FakeAsyncModel, _job, _settings
from tests.test_unit_generation_contracts import (
    _context_payload,
    _identity_payload,
    _policy_payload,
)


class AsyncUnitGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_invocation_uses_async_api_without_sync_fallback(self) -> None:
        """DAG Unit session 只 await async API，不触碰同步 invoke。"""

        raw_response = '{"tasks":[{"id":"task-orders","owner":"frontend"}]}'
        async_invoke = AsyncMock(return_value=SimpleNamespace(
            content=raw_response,
            response_metadata={"finish_reason": "stop"},
        ))
        sync_invoke = MagicMock(side_effect=AssertionError("sync invoke must not run"))
        model = SimpleNamespace(ainvoke=async_invoke, invoke=sync_invoke)

        with patch(
            "app.services.unit_generation.create_chat_model",
            return_value=model,
        ):
            result = await generate_unit_candidate_once(
                _job(),
                settings=_settings(),
            )

        async_invoke.assert_awaited_once()
        sync_invoke.assert_not_called()
        self.assertEqual(result.raw_response, raw_response)

    async def test_async_result_equals_expected_mock_contract(self) -> None:
        """异步 mock 的固定响应与既有结果 DTO 逐字段完全相等。"""

        raw_response = '{"tasks":[{"id":"task-orders","owner":"frontend"}]}'
        job = _job()
        model = FakeAsyncModel(raw_response)
        with patch(
            "app.services.unit_generation.create_chat_model",
            return_value=model,
        ):
            result = await generate_unit_candidate_once(job, settings=_settings())
        expected = UnitGenerationAttemptResult(
            identity=job.identity,
            input_fingerprint=job.context.input_fingerprint,
            raw_response=raw_response,
            tasks=({"id": "task-orders", "owner": "frontend"},),
            validation_issues=(),
            generation_metadata={
                "model": _settings().model_api_name,
                "model_max_tokens": job.policy.model_max_tokens,
                "model_max_retries": 0,
                "model_turns": 1,
                "finish_reason": "stop",
            },
        )

        self.assertEqual(result, expected)

    async def test_cancellation_propagates_without_infrastructure_reclassification(self) -> None:
        """调用方取消会穿透 Unit session，不会被包装成 infrastructure failure。"""

        invocation_started = asyncio.Event()

        async def wait_until_cancelled(_prompt: str) -> SimpleNamespace:
            """保持模型调用 pending，直到测试任务从外部取消。"""

            invocation_started.set()
            await asyncio.Event().wait()
            raise AssertionError("cancelled model invocation must not resume")

        async_invoke = AsyncMock(side_effect=wait_until_cancelled)
        model = SimpleNamespace(ainvoke=async_invoke)
        with patch(
            "app.services.unit_generation.create_chat_model",
            return_value=model,
        ):
            task = asyncio.create_task(generate_unit_candidate_once(
                _job(),
                settings=_settings(),
            ))
            await invocation_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        async_invoke.assert_awaited_once()

    async def test_async_parse_failure_invokes_model_exactly_once(self) -> None:
        """内容解析失败也只 await 一次模型，不在 session 内隐藏重试。"""

        async_invoke = AsyncMock(return_value=SimpleNamespace(
            content='{"tasks":[',
            response_metadata={"finish_reason": "stop"},
        ))
        model = SimpleNamespace(ainvoke=async_invoke)
        with patch(
            "app.services.unit_generation.create_chat_model",
            return_value=model,
        ) as model_factory:
            result = await generate_unit_candidate_once(
                _job(),
                settings=_settings(),
            )

        model_factory.assert_called_once()
        async_invoke.assert_awaited_once()
        self.assertEqual(len(result.validation_issues), 1)

    async def test_async_session_timeout_is_classified_as_infrastructure(self) -> None:
        """异步 session 超时保持 model_invoke/infrastructure 分类且不重试。"""

        async def never_complete(_prompt: str) -> SimpleNamespace:
            """模拟不会自行返回的异步 provider 调用。"""

            await asyncio.Event().wait()
            raise AssertionError("timed-out model invocation must not resume")

        policy = UnitGenerationPolicy(**{
            **_policy_payload(),
            "unit_session_timeout": 0.01,
        })
        job = UnitAttemptJob(
            identity=_identity_payload(),
            context=_context_payload(),
            policy=policy,
        )
        async_invoke = AsyncMock(side_effect=never_complete)
        model = SimpleNamespace(ainvoke=async_invoke)
        with patch(
            "app.services.unit_generation.create_chat_model",
            return_value=model,
        ) as model_factory:
            with self.assertRaises(UnitGenerationInfrastructureError) as raised:
                await generate_unit_candidate_once(job, settings=_settings())

        model_factory.assert_called_once()
        async_invoke.assert_awaited_once()
        self.assertEqual(raised.exception.category, "infrastructure")
        self.assertEqual(raised.exception.stage, "model_invoke")
        self.assertEqual(raised.exception.cause_type, "TimeoutError")
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)


if __name__ == "__main__":
    unittest.main()
