"""验证确认控制不覆盖业务问题，也不借用不匹配的执行。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.workflow_business_request import control_business_request


class WorkflowBusinessRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_gate_confirmation_keeps_revision_request_instead_of_old_requirement(self):
        """真实请求适配后的 API 确认沿用 checkpoint 本轮提问，不采用客户端旧需求。"""
        from app.protocols.workflow.request import workflow_run_inputs

        question = "本轮二次修改：pageSize 从 7 改为 8，确认技术规划后继续开发"
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={
            "request": question, "active_run_id": "source",
        })))
        for target in (
            {"targetType": "application", "targetId": "application"},
            {"targetType": "page", "targetId": "items"},
            {"targetType": "endpoint", "targetId": "item_api.list", "apiContractId": "item_api"},
        ):
            with self.subTest(target=target["targetType"]):
                inputs = workflow_run_inputs({
                    "request": "确认当前 API 映射并开始检测。",
                    "originalRequest": "创建应用时的旧需求，pageSize 默认 10",
                    "clarificationAnswers": {"api_design_gate": {"action": "refresh", **target}},
                })
                self.assertIn("创建应用时的旧需求", inputs["request"])
                self.assertEqual(inputs["resume_from"], "api_design_readiness_gate")
                self.assertEqual(await control_business_request(graph, "", "thread", inputs["resume_values"]), question)

    async def test_same_thread_plan_confirmation_preserves_original_question(self):
        """任务计划确认和重新生成均读取原问题，而非按钮文本。"""

        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={"request": "原问题", "active_run_id": "source"})))
        for action in ("confirm", "regenerate"):
            result = await control_business_request(graph, "workspace", "thread", {"build_task_plan_confirmation": {"action": action}})
            self.assertEqual(result, "原问题")

    async def test_phase_handoff_reads_exact_source_thread(self):
        """跨阶段交接从明确来源执行读取问题，不借用当前新会话。"""

        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={"request": "开发原问题", "active_run_id": "source"})))
        with patch("app.services.workflow_business_request.get_execution", AsyncMock(return_value=SimpleNamespace(thread_id="source-thread"))):
            result = await control_business_request(graph, "workspace", "new-thread", {"test_phase_confirmation": {"action": "confirm"}, "resume_execution_run_id": "source"})
        self.assertEqual(result, "开发原问题")
        graph.aget_state.assert_awaited_once_with({"configurable": {"thread_id": "source-thread"}})

    async def test_stale_or_unreadable_source_does_not_block_or_borrow(self):
        """来源不匹配或读取异常仅不继承问题，正常新请求不读取 checkpoint。"""

        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={"request": "别的问题", "active_run_id": "other"})))
        with patch("app.services.workflow_business_request.get_execution", AsyncMock(return_value=None)):
            self.assertEqual(await control_business_request(graph, "workspace", "thread", {"build_task_plan_confirmation": {"action": "confirm"}, "resume_execution_run_id": "source"}), "")
        graph.aget_state.side_effect = RuntimeError("unavailable")
        self.assertEqual(await control_business_request(graph, "workspace", "thread", {"review_phase_confirmation": {"action": "confirm"}}), "")
        graph.aget_state.reset_mock()
        self.assertEqual(await control_business_request(graph, "workspace", "thread", {}), "")
        graph.aget_state.assert_not_awaited()
