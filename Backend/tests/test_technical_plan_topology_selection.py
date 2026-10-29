"""验证架构选择不能被模型、确认文案或默认 Java 分支绕过。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.domain.application_planning_interaction import ApplicationPlanningInteraction
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation, ApplicationPlanningRecoveryBoundary, application_planning_boundary_payload,
)
from app.graph.application_planning_interrupts import (
    application_planning_review_payload, prepare_application_planning_review_resume,
    validate_application_planning_review_action,
)
from app.graph.application_planning_workflow import _route_technical_planning_confirm
from app.graph.nodes.application_technical_planning import technical_planning_confirm
from app.services.technical_plan_topology_selection import (
    select_technical_plan_topology, topology_selection_payload, validate_selected_topology,
)


def _plan() -> dict:
    """提供未选择架构的技术规划，业务表名保持新版完整物理名称。"""

    return {
        "artifact_type": "technical-plan", "confirmation_status": "pending_user_confirmation",
        "sourceConfigRevision": 1, "architecture": {"frontend": "React", "backend": "Java8 Springboot", "data": "MySQL8 Redis"},
        "entities": [{"id": "ChatSession", "table_name": "12ab34cd56ef_chat_session", "fields": []}],
        "api_contracts": [], "pages": [],
    }


def _config() -> dict:
    """只提供选择所需的当前应用开关，不使用历史产物或模型开关。"""

    return {"configRevision": 1, "auth": {"enable": False}, "authorization": {"enabled": False}}


class TechnicalPlanTopologySelectionTests(unittest.TestCase):
    """覆盖服务端门禁、旧卡片拒绝、候选落盘与确认边界。"""

    def test_runtime_is_visible_but_does_not_claim_generation_is_ready(self) -> None:
        """模板消费端的完成不能冒充 Runtime 全链路已完成。"""

        options = topology_selection_payload()["topologyOptions"]
        runtime = next(item for item in options if item["type"] == "agent_runtime_direct")
        self.assertFalse(runtime["available"])
        self.assertIn("DAG", runtime["reason"])
        with self.assertRaisesRegex(ValueError, "搬运尚未完成"):
            select_technical_plan_topology(_plan(), "agent_runtime_direct", _config())

    def test_unselected_plan_cannot_be_confirmed_as_java(self) -> None:
        """未选择的规划不能通过确认文字进入 Java 模板流程。"""

        with self.assertRaisesRegex(ValueError, "先明确选择"):
            validate_selected_topology(_plan(), _config())
        state = {"technical_plan": _plan(), "clarification": topology_selection_payload()}
        submission = ApplicationPlanningInteraction(gateId="gate", artifact="technical_plan", artifactRevision="rev", action="confirm")
        with self.assertRaisesRegex(ValueError, "不能跳过选择"):
            validate_application_planning_review_action(state, "technical_planning", submission)

    def test_explicit_java_selection_preserves_business_contracts(self) -> None:
        """选择只投影实现边界，不清空实体、接口或修改数据库表名。"""

        original = _plan()
        selected = select_technical_plan_topology(original, "backend_direct", _config())
        self.assertNotIn("topology", original)
        self.assertEqual(selected["entities"], original["entities"])
        self.assertEqual(selected["topology"]["serviceIds"], ["backend"])
        self.assertEqual(selected["confirmation_status"], "pending_user_confirmation")
        validate_selected_topology(selected, _config())
        selected["entities"][0]["table_name"] = "another_table"
        with self.assertRaisesRegex(ValueError, "重新选择"):
            validate_selected_topology(selected, _config())

    def test_native_gate_rejects_stale_selection(self) -> None:
        """沿现有原生 interrupt 版本检查拒绝旧卡片，不另造提交通道。"""

        state = {"technical_plan": _plan(), "clarification": topology_selection_payload()}
        payload = application_planning_review_payload(state, "technical_planning")
        submission = {"gateId": payload["gateId"], "artifact": "technical_plan", "artifactRevision": "stale",
                      "action": "select_topology", "answers": {"topologyType": "backend_direct"}}
        with patch("app.graph.application_planning_interrupts.interrupt", return_value=submission):
            with self.assertRaisesRegex(ValueError, "已经更新"):
                prepare_application_planning_review_resume(state, "technical_planning")

    def test_selection_commits_new_pending_artifact_and_returns_to_review(self) -> None:
        """选择之后回到正式确认，不能直接创建模板或签发完成状态。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = _plan()
            state = {
                "workspace": str(root), "workflow_scope": "application_planning", "request": "创建政策助手",
                "technical_plan": plan,
                "application_planning_interaction": {"action": "select_topology", "answers": {"topologyType": "backend_direct"}},
                "application_planning_recovery_boundary": application_planning_boundary_payload(
                    operation_id="selection-test", operation=ApplicationPlanningOperation.INITIAL,
                    boundary=ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED,
                    request="创建政策助手", candidate=plan,
                ),
            }
            with patch("app.graph.nodes.application_technical_planning._ensure_technical_lifecycle", return_value=(root, None)), patch(
                "app.graph.nodes.application_technical_planning.read_application_config", return_value=_config(),
            ):
                result = technical_planning_confirm(state)
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["mode"], "technical_plan_confirmation")
            self.assertEqual(_route_technical_planning_confirm(result), "technical_planning_review")
            persisted = json.loads(Path(result["technical_plan_json_path"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted["topology"]["type"], "backend_direct")
            self.assertEqual(persisted["confirmation_status"], "pending_user_confirmation")
            self.assertNotIn("application_planning_confirmation", result)
            self.assertNotIn("template_reconcile_pending", result)
