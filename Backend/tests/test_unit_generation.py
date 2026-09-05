"""T3.4 单次 Unit Candidate Generation Session 回归。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from app.config import Settings
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation import (
    UnitGenerationInfrastructureError,
    generate_unit_candidate_once,
)
from app.services.unit_generation_contracts import (
    UnitAttemptJob,
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from tests.test_unit_generation_contracts import (
    _context_payload,
    _identity_payload,
    _policy_payload,
)


class FakeAsyncModel:
    """记录一次异步模型 session 的 Prompt 与调用次数。"""

    def __init__(
        self,
        content: str,
        *,
        exception: Exception | None = None,
        finish_reason: str = "stop",
    ) -> None:
        """配置固定响应、finish reason 或需要向上抛出的传输异常。"""

        self.content = content
        self.exception = exception
        self.finish_reason = finish_reason
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        """记录 Prompt，并模拟一次无内部重试的模型调用。"""

        self.prompts.append(prompt)
        if self.exception is not None:
            raise self.exception
        return SimpleNamespace(
            content=self.content,
            response_metadata={"finish_reason": self.finish_reason},
        )


def _settings(*, dag_unit_max_tokens: int = 4096) -> Settings:
    """构造不读取环境变量的模型设置。"""

    return Settings(
        model_base_url="https://example.com/v1",
        model_api_key="test-key",
        model_name="test-model [display]",
        dag_unit_max_tokens=dag_unit_max_tokens,
    )


def _job(*, model_max_tokens: int = 4096) -> UnitAttemptJob:
    """构造身份、Context 与 Policy 一致的单次 Worker Job。"""

    policy_payload = {**_policy_payload(), "model_max_tokens": model_max_tokens}
    return UnitAttemptJob(
        identity=_identity_payload(),
        context=_context_payload(),
        policy=UnitGenerationPolicy(**policy_payload),
    )


def _issue(code: str, message: str, *, level: str = "unit") -> ValidationIssue:
    """构造可注入 Prompt 的结构化生成反馈。"""

    return ValidationIssue(
        code=code,
        level=level,
        category="generation",
        unit_ids=("page:orders",),
        retry_unit_ids=("page:orders",),
        retryable=True,
        message=message,
    )


class UnitGenerationOnceTests(unittest.IsolatedAsyncioTestCase):
    async def _generate(
        self,
        model: FakeAsyncModel,
        *,
        job: UnitAttemptJob | None = None,
        global_feedback: tuple[ValidationIssue, ...] = (),
        local_feedback: tuple[ValidationIssue, ...] = (),
    ) -> tuple[UnitGenerationAttemptResult, MagicMock]:
        """用 mock model 执行一次 API，并返回结果及 factory mock。"""

        active_job = job or _job()
        with patch(
            "app.services.unit_generation.create_chat_model",
            return_value=model,
        ) as model_factory:
            result = await generate_unit_candidate_once(
                active_job,
                global_feedback=global_feedback,
                local_feedback=local_feedback,
                unit_kind_rules=("只实现当前页面职责。",),
                settings=_settings(
                    dag_unit_max_tokens=active_job.policy.model_max_tokens
                ),
            )
        return result, model_factory

    async def test_successful_response_returns_raw_parsed_attempt_result(self) -> None:
        """合法响应保留原文和 Task，但尚未生成 Candidate status。"""

        raw_response = '{"tasks":[{"id":"task-orders","owner":"frontend"}]}'
        result, _ = await self._generate(FakeAsyncModel(raw_response))

        self.assertEqual(result.raw_response, raw_response)
        self.assertEqual(result.tasks[0]["id"], "task-orders")
        self.assertEqual(result.validation_issues, ())
        self.assertEqual(result.identity.attempt_id, _identity_payload()["attempt_id"])
        self.assertEqual(result.input_fingerprint, "input-digest")
        self.assertNotIn("status", result.model_dump())
        self.assertNotIn("candidate_id", result.model_dump())
        self.assertEqual(result.generation_metadata["finish_reason"], "stop")

    async def test_malformed_response_returns_parser_issues_without_partial_tasks(self) -> None:
        """非法 JSON 消耗本次内容 attempt，并以结构化 Issue 返回。"""

        result, _ = await self._generate(FakeAsyncModel('{"tasks":['))

        self.assertEqual(result.raw_response, '{"tasks":[')
        self.assertEqual(result.tasks, ())
        self.assertEqual(len(result.validation_issues), 1)
        self.assertEqual(
            result.validation_issues[0].code,
            "RAW_CANDIDATE_JSON_MALFORMED",
        )
        self.assertEqual(result.validation_issues[0].category, "generation")

    async def test_length_finish_reason_rejects_even_valid_json_candidate(self) -> None:
        """长度截断是内容失败，即使响应恰好为合法 JSON 也不得返回 Task。"""

        raw_response = '{"tasks":[{"id":"task-orders","owner":"frontend"}]}'
        result, _ = await self._generate(
            FakeAsyncModel(raw_response, finish_reason="length")
        )

        self.assertEqual(result.raw_response, raw_response)
        self.assertEqual(result.tasks, ())
        self.assertEqual(result.generation_metadata["finish_reason"], "length")
        self.assertEqual(len(result.validation_issues), 1)
        issue = result.validation_issues[0]
        self.assertEqual(issue.code, "UNIT_CANDIDATE_OUTPUT_TRUNCATED")
        self.assertEqual(issue.level, "unit")
        self.assertEqual(issue.category, "generation")
        self.assertEqual(issue.unit_ids, ("page:orders",))
        self.assertEqual(issue.retry_unit_ids, ("page:orders",))
        self.assertTrue(issue.retryable)

    async def test_transport_exception_is_classified_and_raised(self) -> None:
        """传输异常直接以 infrastructure 分类上抛，不转换为内容 Issue。"""

        model = FakeAsyncModel(
            "",
            exception=httpx.ReadError("connection reset"),
        )
        with self.assertRaises(UnitGenerationInfrastructureError) as raised:
            await self._generate(model)

        self.assertEqual(raised.exception.category, "infrastructure")
        self.assertEqual(raised.exception.stage, "model_invoke")
        self.assertEqual(raised.exception.cause_type, "ReadError")
        self.assertEqual(raised.exception.identity.attempt_id, _identity_payload()["attempt_id"])
        self.assertIsInstance(raised.exception.__cause__, httpx.ReadError)
        self.assertEqual(len(model.prompts), 1)

    async def test_dag_unit_max_token_policy_is_forwarded_as_call_override(self) -> None:
        """Policy 中由 dag_unit_max_tokens 映射的预算显式覆盖全局 Agent token。"""

        job = _job(model_max_tokens=8192)
        _, model_factory = await self._generate(
            FakeAsyncModel('{"tasks":[]}'),
            job=job,
        )

        self.assertEqual(
            model_factory.call_args.kwargs["max_tokens_override"],
            8192,
        )

    async def test_sdk_retry_is_explicitly_disabled(self) -> None:
        """每次创建 Unit 模型都显式传入 retry=0，不能回退全局配置。"""

        _, model_factory = await self._generate(FakeAsyncModel('{"tasks":[]}'))

        self.assertEqual(model_factory.call_args.kwargs["max_retries_override"], 0)

    async def test_global_and_latest_local_feedback_are_injected(self) -> None:
        """Global 与本 Unit 最新 Local feedback 分区进入同一个 Prompt。"""

        model = FakeAsyncModel('{"tasks":[]}')
        await self._generate(
            model,
            global_feedback=(
                _issue("GLOBAL_OWNER_CONFLICT", "修复全局 owner 冲突", level="global"),
            ),
            local_feedback=(
                _issue("LOCAL_DEPENDENCY_UNKNOWN", "移除未知依赖"),
            ),
        )

        self.assertEqual(len(model.prompts), 1)
        prompt = model.prompts[0]
        self.assertIn('"code": "GLOBAL_OWNER_CONFLICT"', prompt)
        self.assertIn('"code": "LOCAL_DEPENDENCY_UNKNOWN"', prompt)
        self.assertIn("### Global feedback", prompt)
        self.assertIn("### Latest local feedback", prompt)

    async def test_one_api_call_creates_and_invokes_exactly_one_model_session(self) -> None:
        """单次 API 不循环、不隐藏 retry，也不产生第二个 Candidate 响应。"""

        model = FakeAsyncModel('{"tasks":[]}')
        result, model_factory = await self._generate(model)

        model_factory.assert_called_once()
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(result.generation_metadata["model_turns"], 1)
        self.assertEqual(result.tasks, ())


if __name__ == "__main__":
    unittest.main()
