"""T6.1 串行单 Unit Local retry 的端到端编排回归。"""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.config import Settings
from app.services import planning_run as transitions
from app.services.planning_run_controller import PlanningRunController
from app.services.unit_generation import (
    UnitGenerationInfrastructureError,
    UnitGenerationPlatformError,
)
from app.services.unit_generation_contracts import UnitGenerationPolicy
from app.services.unit_generation_orchestrator import run_unit_generation_round
from tests.planning_run_fixtures import AT, UNIT, run
from tests.test_unit_candidate_validator import _context, _task
from tests.test_unit_generation_contracts import _policy_payload


class _Model:
    """按测试输入返回一次固定模型结果，并记录收到的 Prompt。"""

    def __init__(
        self,
        content: str,
        *,
        finish_reason: str = "stop",
        error: Exception | None = None,
    ) -> None:
        """配置响应正文、结束原因或传输异常。"""

        self.content = content
        self.finish_reason = finish_reason
        self.error = error
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        """记录唯一模型 turn，并返回 LangChain 风格响应。"""

        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            content=self.content,
            response_metadata={"finish_reason": self.finish_reason},
        )


def _response(task: dict) -> str:
    """把单个 Candidate Task 编码为严格模型 envelope。"""

    return json.dumps({"tasks": [task]}, ensure_ascii=False)


def _settings() -> Settings:
    """构造不访问环境变量的测试模型设置。"""

    return Settings(
        model_base_url="https://example.com/v1",
        model_api_key="test-key",
        model_name="test-model [display]",
    )


class SequentialUnitGenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        """创建已进入 generation phase 的单 Unit Run 与独立持久化目录。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        initial = transitions.begin_generation(run(), at=AT)
        self.controller = PlanningRunController(initial, {"workspace": directory.name})
        self.context = _context().model_copy(update={
            "planning_run_id": initial.planning_run_id,
            "input_fingerprint": initial.input_fingerprint,
            "base_confirmed_plan_digest": initial.base_confirmed_plan_digest,
            "build_execution_scope": initial.build_execution_scope,
        })
        self.policy = UnitGenerationPolicy(**_policy_payload())

    async def _run(self, *models: _Model):
        """依次为每个 Attempt 提供独立模型实例并返回最终 Unit 状态。"""

        with patch(
            "app.services.unit_generation.create_chat_model",
            side_effect=models,
        ) as factory:
            state = await run_unit_generation_round(
                self.controller,
                self.context,
                self.policy,
                settings=_settings(),
                now=lambda: AT,
            )
        self.assertEqual(factory.call_count, sum(len(model.prompts) for model in models))
        return state

    async def test_valid_first_stops_after_one_attempt(self) -> None:
        """首次 Candidate 合法时立即 ready，不创建多余 Attempt。"""

        models = (_Model(_response(_task())),)
        state = await self._run(*models)

        self.assertEqual(state.generation_status, "candidate_ready")
        self.assertEqual(state.attempt_in_round, 1)
        self.assertEqual(state.total_attempts, 1)
        self.assertEqual(len(self.controller.snapshot.candidates), 1)

    async def test_invalid_then_valid_uses_two_attempts(self) -> None:
        """一次 Local validation 失败后只重试当前 Unit，并在第二次成功。"""

        invalid = _task()
        invalid["owner"] = "backend"
        models = (_Model(_response(invalid)), _Model(_response(_task())))
        state = await self._run(*models)

        self.assertEqual(state.generation_status, "candidate_ready")
        self.assertEqual(state.attempt_in_round, 2)
        self.assertEqual(
            [
                candidate.status
                for candidate in self.controller.snapshot.candidates.values()
            ],
            ["invalid", "valid"],
        )

    async def test_two_invalid_then_valid_only_sends_latest_local_feedback(self) -> None:
        """第三次 Prompt 只携带第二次的相关 Issues，不累计第一次 Local 历史。"""

        wrong_owner = _task()
        wrong_owner["owner"] = "backend"
        missing_title = deepcopy(_task())
        missing_title.pop("title")
        models = (
            _Model(_response(wrong_owner)),
            _Model(_response(missing_title)),
            _Model(_response(_task())),
        )
        state = await self._run(*models)

        self.assertEqual(state.generation_status, "candidate_ready")
        self.assertEqual(state.attempt_in_round, 3)
        self.assertIn("CANDIDATE_OWNER_MISMATCH", models[1].prompts[0])
        self.assertIn("CANDIDATE_TASK_FIELD_MISSING", models[2].prompts[0])
        self.assertNotIn("CANDIDATE_OWNER_MISMATCH", models[2].prompts[0])

    async def test_three_invalid_attempts_end_round_exhausted(self) -> None:
        """连续三次内容失败精确耗尽本轮，且不会创建第四次模型调用。"""

        invalid = _task()
        invalid["owner"] = "backend"
        models = tuple(_Model(_response(invalid)) for _ in range(3))
        state = await self._run(*models)

        self.assertEqual(state.generation_status, "round_exhausted")
        self.assertEqual(state.attempt_in_round, 3)
        self.assertEqual(state.total_attempts, 3)
        self.assertEqual(len(self.controller.snapshot.candidates), 3)
        self.assertTrue(
            all(
                candidate.status == "invalid"
                for candidate in self.controller.snapshot.candidates.values()
            )
        )

    async def test_transport_error_is_immediately_fatal(self) -> None:
        """传输异常终止 PlanningRun，不转换为 Local issue 或发起下一 Attempt。"""

        models = (
            _Model("", error=httpx.ReadError("connection reset")),
            _Model(_response(_task())),
        )
        with patch(
            "app.services.unit_generation.create_chat_model",
            side_effect=models,
        ) as factory:
            with self.assertRaises(UnitGenerationInfrastructureError):
                await run_unit_generation_round(
                    self.controller,
                    self.context,
                    self.policy,
                    settings=_settings(),
                    now=lambda: AT,
                )

        self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.controller.snapshot.status, "failed")
        self.assertEqual(self.controller.snapshot.failure.category, "infrastructure")
        self.assertEqual(self.controller.snapshot.failure.retry_unit_ids, ())
        self.assertEqual(len(models[1].prompts), 0)

    async def test_broken_frozen_source_is_platform_fatal_without_local_retry(self) -> None:
        """Reader/Store 损坏提交不可重试平台失败，不分配第二个 Local Attempt。"""

        calls = []

        async def fail_with_broken_source(job, **_kwargs):
            """模拟默认 generation session 在 Reader 初始化时发现冻结来源损坏。"""

            calls.append(job.identity)
            raise UnitGenerationPlatformError(
                identity=job.identity,
                stage="contract_reader_setup",
                cause=ValueError("broken frozen ref"),
            )

        with self.assertRaises(UnitGenerationPlatformError):
            await run_unit_generation_round(
                self.controller,
                self.context,
                self.policy,
                settings=_settings(),
                generate_once=fail_with_broken_source,
                now=lambda: AT,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.controller.snapshot.status, "failed")
        self.assertEqual(self.controller.snapshot.failure.category, "platform")
        self.assertEqual(self.controller.snapshot.failure.retry_unit_ids, ())
        self.assertEqual(
            self.controller.snapshot.failure.code,
            "UNIT_GENERATION_FROZEN_CONTRACT_SOURCE_INVALID",
        )

    async def test_finish_length_is_local_failure_then_retries(self) -> None:
        """provider length 截断即使正文合法也作为 Local failure 消耗一次并重试。"""

        models = (
            _Model(_response(_task()), finish_reason="length"),
            _Model(_response(_task())),
        )
        state = await self._run(*models)

        self.assertEqual(state.generation_status, "candidate_ready")
        self.assertEqual(state.attempt_in_round, 2)
        candidates = list(self.controller.snapshot.candidates.values())
        self.assertEqual(
            candidates[0].validation_issues[0].code,
            "UNIT_CANDIDATE_OUTPUT_TRUNCATED",
        )
        self.assertEqual(candidates[1].status, "valid")


if __name__ == "__main__":
    unittest.main()
