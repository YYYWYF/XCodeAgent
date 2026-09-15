"""T10.4 单 Unit Agent Contract Tool Session 回归。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.frozen_contract_reader import FROZEN_CONTRACT_READER_TOOL_NAME
from app.services.frozen_contract_store import FrozenContractStore
from app.services.unit_generation import (
    UnitGenerationPlatformError,
    generate_unit_candidate_once,
)
from app.services.unit_generation_contracts import (
    UnitAttemptJob,
    UnitGenerationPolicy,
)
from tests.test_frozen_contract_store import _formal_inputs
from tests.test_unit_generation import _settings
from tests.test_unit_generation_contracts import (
    _context_payload,
    _identity_payload,
    _policy_payload,
)


class FakeToolModel:
    """记录工具绑定和每轮消息，并按顺序返回预设 AIMessage。"""

    def __init__(self, responses: list[AIMessage]) -> None:
        """保存预设响应以及供断言使用的绑定/调用记录。"""

        self.responses = list(responses)
        self.bound_tools: tuple[object, ...] = ()
        self.invocations: list[object] = []

    def bind_tools(self, tools: list[object]) -> "FakeToolModel":
        """模拟 ChatModel 只绑定调用方提供的工具集合。"""

        self.bound_tools = tuple(tools)
        return self

    async def ainvoke(self, messages: object) -> AIMessage:
        """记录本轮完整输入并返回下一条预设消息。"""

        self.invocations.append(
            tuple(messages) if isinstance(messages, list) else messages
        )
        if not self.responses:
            raise AssertionError("model turn 超出测试预设响应数量")
        return self.responses.pop(0)


def _tool_call(ref_id: str, selector: str, call_id: str) -> AIMessage:
    """构造一次标准 Frozen Contract Reader ToolCall 响应。"""

    return AIMessage(
        content="",
        tool_calls=[{
            "name": FROZEN_CONTRACT_READER_TOOL_NAME,
            "args": {"ref_id": ref_id, "selector": selector},
            "id": call_id,
            "type": "tool_call",
        }],
        response_metadata={"finish_reason": "tool_calls"},
    )


def _two_tool_calls(ref_id: str) -> AIMessage:
    """在同一模型轮中请求两次读取，用于验证共享 read-count 预算。"""

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": FROZEN_CONTRACT_READER_TOOL_NAME,
                "args": {"ref_id": ref_id, "selector": "/pageId"},
                "id": "read-1",
                "type": "tool_call",
            },
            {
                "name": FROZEN_CONTRACT_READER_TOOL_NAME,
                "args": {"ref_id": ref_id, "selector": "/requiredEndpointIds"},
                "id": "read-2",
                "type": "tool_call",
            },
        ],
        response_metadata={"finish_reason": "tool_calls"},
    )


def _final(task_id: str = "task-orders") -> AIMessage:
    """构造只含完整 tasks envelope 的最终模型响应。"""

    return AIMessage(
        content=json.dumps({"tasks": [{"id": task_id, "owner": "frontend"}]}),
        response_metadata={"finish_reason": "stop"},
    )


def _store_and_job(
    *,
    model_turn_limit: int = 4,
    max_reads: int = 5,
    max_total_bytes: int = 10000,
    max_bytes_per_read: int = 2000,
) -> tuple[FrozenContractStore, UnitAttemptJob, str]:
    """构造 catalog 精确指向当前 PlanningRun Store 的测试 Job。"""

    store = FrozenContractStore.create(
        planning_run_id="planning-run-1",
        formal_inputs=_formal_inputs(),
    )
    page = next(
        contract for contract in store.contracts.values()
        if contract.kind == "page_contract"
    )
    context = _context_payload()
    context["contract_catalog"] = [{
        "ref_id": page.ref_id,
        "kind": "page_contract",
        "selectors": ["/pageId", "/requiredEndpointIds"],
    }]
    policy = _policy_payload()
    policy["model_turn_limit"] = model_turn_limit
    policy["frozen_contract_read_limits"] = {
        "max_reads": max_reads,
        "max_total_bytes": max_total_bytes,
        "max_bytes_per_read": max_bytes_per_read,
    }
    return store, UnitAttemptJob(
        identity=_identity_payload(),
        context=context,
        policy=UnitGenerationPolicy(**policy),
    ), page.ref_id


class UnitGenerationToolSessionTests(unittest.IsolatedAsyncioTestCase):
    """验证一个 Local Attempt 内零次或多次合同读取仍只产生一个最终结果。"""

    async def _generate(
        self,
        model: FakeToolModel,
        *,
        store: FrozenContractStore,
        job: UnitAttemptJob,
    ):
        """注入假模型并执行一次带 Store 的公开生成 API。"""

        with patch(
            "app.services.unit_generation.create_chat_model",
            return_value=model,
        ):
            return await generate_unit_candidate_once(
                job,
                settings=_settings(),
                frozen_contract_store=store,
            )

    async def test_no_tool_returns_final_candidate_in_one_turn(self) -> None:
        """模型无需合同正文时，一轮即可返回最终 Candidate，Reader 预算不消耗。"""

        store, job, _ = _store_and_job()
        model = FakeToolModel([_final()])
        result = await self._generate(model, store=store, job=job)

        self.assertEqual(result.tasks[0]["id"], "task-orders")
        self.assertEqual(result.generation_metadata["model_turns"], 1)
        self.assertEqual(result.generation_metadata["contract_reads"], 0)
        self.assertEqual(len(model.bound_tools), 1)
        self.assertEqual(model.bound_tools[0].name, FROZEN_CONTRACT_READER_TOOL_NAME)
        self.assertIsInstance(model.invocations[0][0], HumanMessage)

    async def test_one_read_feeds_tool_result_into_second_model_turn(self) -> None:
        """一次读取以标准 ToolMessage 回传后，同一 Attempt 在下一轮产出最终结果。"""

        store, job, ref_id = _store_and_job()
        model = FakeToolModel([_tool_call(ref_id, "/pageId", "read-1"), _final()])
        result = await self._generate(model, store=store, job=job)

        self.assertEqual(result.identity, job.identity)
        self.assertEqual(result.generation_metadata["model_turns"], 2)
        self.assertEqual(result.generation_metadata["contract_reads"], 1)
        second_messages = model.invocations[1]
        self.assertIsInstance(second_messages[-2], AIMessage)
        self.assertIsInstance(second_messages[-1], ToolMessage)
        payload = json.loads(second_messages[-1].content)
        self.assertEqual(payload["sourceRef"], ref_id)
        self.assertEqual(json.loads(payload["content"]), "orders")

    async def test_multiple_reads_share_one_attempt_and_return_one_candidate(self) -> None:
        """多轮读取共享 Reader/Attempt 预算，最终只封装一个 Candidate result。"""

        store, job, ref_id = _store_and_job()
        model = FakeToolModel([
            _tool_call(ref_id, "/pageId", "read-1"),
            _tool_call(ref_id, "/requiredEndpointIds", "read-2"),
            _final("task-after-two-reads"),
        ])
        result = await self._generate(model, store=store, job=job)

        self.assertEqual(result.identity.attempt_id, job.identity.attempt_id)
        self.assertEqual(result.tasks[0]["id"], "task-after-two-reads")
        self.assertEqual(result.generation_metadata["model_turns"], 3)
        self.assertEqual(result.generation_metadata["contract_reads"], 2)
        self.assertEqual(len(model.invocations), 3)

    async def test_turn_limit_is_retryable_content_failure_without_extra_invoke(self) -> None:
        """最后可用 turn 仍请求工具时停止循环，并把当前 Local attempt 判为内容失败。"""

        store, job, ref_id = _store_and_job(model_turn_limit=1)
        model = FakeToolModel([_tool_call(ref_id, "/pageId", "read-1")])
        result = await self._generate(model, store=store, job=job)

        self.assertEqual(len(model.invocations), 1)
        self.assertEqual(result.tasks, ())
        self.assertEqual(
            result.validation_issues[0].code,
            "UNIT_GENERATION_MODEL_TURN_LIMIT_EXCEEDED",
        )
        self.assertTrue(result.validation_issues[0].retryable)

    async def test_read_limit_is_retryable_content_failure(self) -> None:
        """同一轮的第二次读取越过 max_reads 时不继续调用模型，也不返回部分 Candidate。"""

        store, job, ref_id = _store_and_job(max_reads=1)
        model = FakeToolModel([_two_tool_calls(ref_id)])
        result = await self._generate(model, store=store, job=job)

        self.assertEqual(len(model.invocations), 1)
        self.assertEqual(result.tasks, ())
        issue = result.validation_issues[0]
        self.assertEqual(issue.code, "UNIT_GENERATION_CONTRACT_READ_BUDGET_EXHAUSTED")
        self.assertEqual(
            issue.details["reader_error"]["code"],
            "FROZEN_CONTRACT_READ_COUNT_EXCEEDED",
        )
        self.assertEqual(result.generation_metadata["contract_reads"], 1)

    async def test_final_response_after_read_must_be_complete_tasks_envelope(self) -> None:
        """Reader 之后的最终合法 tasks envelope 才会被解析为当前 Attempt 的完整结果。"""

        store, job, ref_id = _store_and_job()
        model = FakeToolModel([
            _tool_call(ref_id, "/pageId", "read-1"),
            _final("complete-final-task"),
        ])
        result = await self._generate(model, store=store, job=job)

        self.assertEqual(result.raw_response, _final("complete-final-task").content)
        self.assertEqual(tuple(task["id"] for task in result.tasks), ("complete-final-task",))
        self.assertEqual(result.validation_issues, ())

    async def test_accumulated_byte_policy_exhaustion_is_content_failure(self) -> None:
        """累计字节预算耗尽属于 Local 内容失败，不提升为基础设施或平台异常。"""

        store, job, ref_id = _store_and_job(
            max_reads=3,
            max_total_bytes=4,
            max_bytes_per_read=4,
        )
        model = FakeToolModel([
            _tool_call(ref_id, "/pageId", "read-1"),
            _tool_call(ref_id, "/requiredEndpointIds", "read-2"),
        ])
        result = await self._generate(model, store=store, job=job)

        issue = result.validation_issues[0]
        self.assertEqual(issue.code, "UNIT_GENERATION_CONTRACT_READ_BUDGET_EXHAUSTED")
        self.assertEqual(
            issue.details["reader_error"]["code"],
            "FROZEN_CONTRACT_ACCUMULATED_SIZE_EXCEEDED",
        )
        self.assertTrue(issue.retryable)
        self.assertEqual(result.generation_metadata["contract_bytes"], 4)

    async def test_broken_frozen_source_is_platform_fatal_before_model_dispatch(self) -> None:
        """Store/Run 或 catalog ref 错配时直接抛 platform fatal，不消耗内容重试预算。"""

        _, job, _ = _store_and_job()
        broken_store = FrozenContractStore.create(
            planning_run_id="planning-run-other",
            formal_inputs=_formal_inputs(),
        )
        model = FakeToolModel([_final()])
        with patch(
            "app.services.unit_generation.create_chat_model",
            return_value=model,
        ) as model_factory, self.assertRaises(UnitGenerationPlatformError) as caught:
            await generate_unit_candidate_once(
                job,
                settings=_settings(),
                frozen_contract_store=broken_store,
            )

        model_factory.assert_not_called()
        self.assertEqual(caught.exception.category, "platform")
        self.assertEqual(caught.exception.stage, "contract_reader_setup")
        self.assertEqual(caught.exception.identity, job.identity)


if __name__ == "__main__":
    unittest.main()
