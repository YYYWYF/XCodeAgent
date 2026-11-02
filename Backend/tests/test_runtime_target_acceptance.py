"""目标验收的不可重放与当前合同回归；不启动 Electron、Runtime 或供应商。"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.services.runtime_target_acceptance import ensure_runtime_target_acceptance, target_acceptance_plan
from app.services.runtime_endpoint_dependencies import resolve_runtime_target
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from tests.test_runtime_endpoint_contracts import plan
from tests.test_runtime_endpoint_contracts import business


class RuntimeTargetAcceptanceTests(unittest.TestCase):
    """只验证关键执行边界，不用 Mock 成功冒充真实供应商或页面通过。"""

    def test_tool_endpoint_is_a_real_dependency_with_bounded_model_loop(self):
        """业务 Tool 前置进入同一范围；单次会话允许有界模型工具循环。"""
        candidate = plan()
        candidate["api_contracts"] = [business()]
        candidate["agent_contracts"][0]["agentSettings"]["tools"] = {"enabled": True,
            "bindings": [{"toolId": "lookup", "apiContractId": "order_api", "endpointId": "order.list"}]}
        formal = materialize_runtime_endpoints(candidate)
        resolved = resolve_runtime_target(formal, "agent", "order_assistant")
        self.assertIn(("order_api", "order.list"), resolved["endpoint_keys"])
        specification = target_acceptance_plan(formal, {"type": "agent", "targetId": "order_assistant"})
        self.assertEqual(specification["cases"][0]["modelRequestLimit"], 8)
        self.assertEqual(specification["conversationRunsPerCase"], 1)

    def test_three_targets_resolve_same_agent_without_reverse_consumers(self):
        """Agent、运行接口、绑定页面共用前置集合，独立 Agent 不反向扩展到页面。"""
        formal = materialize_runtime_endpoints(plan())
        agent = resolve_runtime_target(formal, "agent", "order_assistant")
        endpoint = resolve_runtime_target(formal, "endpoint", "agent.order_assistant.run", "agent.order_assistant")
        page = resolve_runtime_target(formal, "page", "orders")
        self.assertEqual(agent["required_unit_ids"], endpoint["required_unit_ids"])
        self.assertEqual(agent["required_unit_ids"], page["required_unit_ids"])
        self.assertEqual(agent["agent_ids"], ["order_assistant"])
        self.assertNotIn("page:orders", agent["required_unit_ids"])

    def test_pending_exposes_exact_request_budget_and_page_mode(self):
        """确认前公开固定问题、单次预算和期限；页面使用同一次真实发送。"""
        specification = target_acceptance_plan(materialize_runtime_endpoints(plan()), {"type": "page", "targetId": "orders"})
        self.assertEqual(specification["conversationRunsPerCase"], 1)
        self.assertEqual(specification["cases"][0]["modelRequestLimit"], 1)
        self.assertEqual(specification["automaticRetries"], 0)
        self.assertEqual(specification["cases"][0]["mode"], "page")
        self.assertEqual(specification["budgets"]["attemptSeconds"], 300)

    def test_failure_consumes_budget_before_dispatch_and_never_replays(self):
        """模拟已发送后的断开；节点恢复不能再次调用，必须明确确认新的 DAG。"""
        specification = {"cases": [{"agentId": "order_assistant"}], "scope": {"type": "agent", "targetId": "order_assistant"}}
        state = {"build_execution_scope": specification["scope"], "unit_test_gate_passed": True,
            "build_run_id": "build-current", "build_run_plan_sha256": "d" * 64,
            "build_task_plan": {"confirmation_status": "confirmed", "build_execution_scope": specification["scope"],
                "targetAcceptance": specification}}
        def dispatched_failure(*args, before_dispatch, **kwargs):
            """模拟桌面任务已签发后断开，必须保留预算消费事实。"""
            before_dispatch()
            raise ValueError("结果未知")

        with TemporaryDirectory() as directory, \
                patch("app.services.python_runtime_checks.runtime_check_plan", return_value={}), \
                patch("app.workspace.task_documents.build_task_plan_sha256", return_value="d" * 64), \
                patch("app.services.runtime_target_acceptance.target_acceptance_plan", return_value=specification), \
                patch("app.services.runtime_target_acceptance.acceptance_fingerprint", return_value="current"), \
                patch("app.services.agent_runtime_launcher.check_runtime_target", side_effect=dispatched_failure) as dispatch:
            with self.assertRaisesRegex(ValueError, "结果未知"):
                ensure_runtime_target_acceptance(Path(directory), deepcopy(state))
            with self.assertRaisesRegex(ValueError, "预算已消费"):
                ensure_runtime_target_acceptance(Path(directory), deepcopy(state))
            dispatch.assert_called_once()

    def test_preparation_failure_does_not_consume_model_budget(self):
        """跳过单测后环境准备失败仍允许用户重试，不自动调用、不消费模型预算。"""
        import json
        specification = {"cases": [{"agentId": "order_assistant"}], "scope": {"type": "agent", "targetId": "order_assistant"}}
        state = {"build_execution_scope": specification["scope"], "unit_test_gate_passed": True,
            "build_run_id": "build-current", "build_run_plan_sha256": "d" * 64,
            "build_task_plan": {"confirmation_status": "confirmed", "build_execution_scope": specification["scope"],
                                "targetAcceptance": specification}}
        with TemporaryDirectory() as directory, \
                patch("app.services.python_runtime_checks.runtime_check_plan", return_value={}), \
                patch("app.workspace.task_documents.build_task_plan_sha256", return_value="d" * 64), \
                patch("app.services.runtime_target_acceptance.target_acceptance_plan", return_value=specification), \
                patch("app.services.runtime_target_acceptance.acceptance_fingerprint", return_value="current"), \
                patch("app.services.agent_runtime_launcher.check_runtime_target", side_effect=ValueError("启动准备失败")) as dispatch:
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, "启动准备失败"):
                    ensure_runtime_target_acceptance(Path(directory), deepcopy(state))
                paths = list((Path(directory) / ".devagentstudio/runtime/acceptance").glob("*/result.json"))
                record = json.loads(paths[0].read_text())
                self.assertEqual(record["status"], "failed_before_dispatch")
                self.assertFalse(record["cases"][0]["budgetConsumed"])
            self.assertEqual(dispatch.call_count, 2)

    def test_target_launch_syncs_dependencies_without_unit_tests(self):
        """验收启动明确同步依赖，不借用单测是否执行的隐式前置条件。"""
        from app.services.agent_runtime_launcher import check_runtime_target
        with TemporaryDirectory() as directory, patch("app.services.agent_runtime_launcher._launch_runtime_locked",
                return_value={"status": "completed", "execution": {"cleanup_succeeded": True}, "targetEvidence": {"passed": True}}) as launch:
            check_runtime_target(Path(directory), Path(directory) / "logs", case={"agentId": "assistant", "modelRequestLimit": 1},
                                 specification={}, deadline=1000)
            self.assertFalse(launch.call_args.kwargs["skip_install"])

    def test_foreign_page_cannot_reference_unbound_agent(self):
        """运行 Endpoint 的正式引用不能绕过 Agent 页面归属。"""
        formal = materialize_runtime_endpoints(plan())
        formal["pages"][1]["references"] = {"endpoint_dependencies": [
            {"api_contract_id": "agent.order_assistant", "endpoint_id": "agent.order_assistant.run"}]}
        with self.assertRaisesRegex(ValueError, "未绑定自身"):
            resolve_runtime_target(formal, "page", "policy")
