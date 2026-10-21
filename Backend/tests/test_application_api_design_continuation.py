"""验证应用修订共用映射门禁与 DAG 正式输入的衔接。"""

import tempfile
import unittest

from app.graph.nodes.api_design import api_design_readiness_gate
from app.graph.nodes.tasks import _resolve_build_context
from app.graph.nodes.task_planning_inputs import mainline_formal_contract_inputs
from app.services.api_design import ApiDesignError
from app.protocols.workflow.request import workflow_run_inputs
from app.services.planning_frozen import plain_json
from app.workspace.endpoint_design_documents import write_endpoint_design
from tests.test_api_design_node import _plan, _write_plan, _empty_design


class ApplicationApiDesignContinuationTests(unittest.TestCase):
    """覆盖技术规划变化、映射确认及生成前重新读取。"""

    def test_application_gate_rechecks_designs_and_loads_current_dag_inputs(self) -> None:
        """失效与缺失映射阻断，全部重新确认后完整映射进入 DAG 冻结输入。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            write_endpoint_design(workspace, _empty_design(workspace, "orders.list"))
            plan["version"] = "revised"
            _write_plan(workspace, plan)
            scope = {"type": "application", "targetId": "application"}
            state = {"workspace": workspace, "project_plan": plan, "build_execution_scope": scope}
            waiting = api_design_readiness_gate(state)
            self.assertEqual(waiting["status"], "requires_user_input")
            self.assertEqual(waiting["clarification"]["developmentTarget"]["type"], "application")
            self.assertEqual([item["status"] for item in waiting["clarification"]["apiDesigns"]], ["stale", "pending"])
            action = {"action": "refresh", "targetType": "application", "targetId": "application"}
            self.assertEqual(api_design_readiness_gate({**state, "api_design_gate_action": action})["status"], "requires_user_input")
            for endpoint_id in ("orders.list", "orders.create"):
                write_endpoint_design(workspace, _empty_design(workspace, endpoint_id))
            # 再次校验已有效时结束应用级配置交接，必须逐个选择开发目标。
            self.assertEqual(api_design_readiness_gate(state)["clarification"]["mode"], "development_target_selection")
            self.assertEqual(api_design_readiness_gate({**state, "api_design_gate_action": action})["status"], "completed")
            context = _resolve_build_context(state, plan, scope, {})
            formal = mainline_formal_contract_inputs({"product_plan": {"artifact_type": "product-plan"}}, plan, context)
            self.assertEqual({plain_json(item.content)["endpointId"] for item in formal.endpoint_api_designs}, {"orders.list", "orders.create"})
            # 门禁通过后上游再变化，生成前的正式加载必须再次拒绝旧映射。
            plan["version"] = "changed-after-gate"
            _write_plan(workspace, plan)
            with self.assertRaises(ApiDesignError):
                _resolve_build_context(state, plan, scope, {})

    def test_application_without_endpoints_passes_without_empty_confirmation(self) -> None:
        """纯静态应用不需要配置或确认空接口列表。"""

        state = {"workspace": "/unused", "project_plan": {"api_contracts": []}, "build_execution_scope": {"type": "application", "targetId": "application"}}
        self.assertEqual(api_design_readiness_gate(state)["status"], "completed")

    def test_application_refresh_clears_stale_client_target(self) -> None:
        """应用级门禁确认沿原范围续行，不让会话旧页面/接口裁剪新 DAG。"""

        inputs = workflow_run_inputs({"request": "确认映射", "forwardedProps": {
            "selectedPageId": "old-page",
            "buildExecutionScope": {"type": "page", "targetId": "old-page"},
            "clarificationAnswers": {"api_design_gate": {"action": "refresh", "targetType": "application", "targetId": "application"}},
        }})
        self.assertEqual(inputs["resume_from"], "api_design_readiness_gate")
        self.assertEqual(inputs["resume_values"]["build_execution_scope"], {"type": "application", "targetId": "application"})
        self.assertNotIn("selectedPageId", inputs["resume_values"])

    def test_gate_rejects_application_action_for_page_owner(self) -> None:
        """门禁动作不得把原页面目标替换为整个应用。"""

        with self.assertRaisesRegex(ApiDesignError, "不一致"):
            api_design_readiness_gate({"workspace": "/unused", "project_plan": _plan(), "selectedPageId": "orders", "api_design_gate_action": {"action": "refresh", "targetType": "application", "targetId": "application"}})
