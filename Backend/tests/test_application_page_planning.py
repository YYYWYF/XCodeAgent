from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.agents.main import requirements_analyzer
from app.graph.application_planning_workflow import (
    _project_planning,
    _route_requirements,
    _route_start,
)
from app.protocols.application_page_planning import application_page_planning_capabilities
from app.services.application_planning_persistence import confirm_application_planning_artifacts


def _confirmed_state(workspace: Path) -> dict[str, object]:
    """构造包含已确认 RequirementSpec、ProjectPlan 和 API 契约的最小状态。"""

    requirement_path = workspace / ".xcodeagent" / "specs" / "requirement-spec.md"
    project_plan_path = workspace / ".xcodeagent" / "plans" / "project-plan.md"
    requirement_path.parent.mkdir(parents=True, exist_ok=True)
    project_plan_path.parent.mkdir(parents=True, exist_ok=True)
    requirement_path.write_text("# RequirementSpec\n\n任务中心需求。\n", encoding="utf-8")
    project_plan_path.write_text("# ProjectPlan\n\n任务中心计划。\n", encoding="utf-8")
    state = {
        "workspace": str(workspace),
        "requirement_spec_path": str(requirement_path),
        "project_plan_path": str(project_plan_path),
        "requirement_spec": {
            "confirmation_status": "confirmed",
            "summary": "任务中心需求。",
            "app_info": {"name": "任务中心"},
        },
        "project_plan": {
            "confirmation_status": "confirmed",
            "frontend_pages": [{
                "id": "tasks",
                "name": "任务列表",
                "path": "/tasks",
                "description": "查看并完成任务。",
                "permissions": ["user"],
            }],
            "api_contracts": [{
                "id": "tasks",
                "base_path": "/api/tasks",
                "authentication": {"required": True, "roles": ["user"]},
                "schemas": {"Task": {"type": "object"}},
                "endpoints": [{
                    "id": "tasks.update",
                    "method": "PATCH",
                    "path": "/api/tasks/{id}",
                    "summary": "完成任务",
                    "request_schema_ref": "Task",
                    "response_schema_ref": "Task",
                    "error_codes": ["NOT_FOUND"],
                }],
            }],
            "data_sources": [{
                "id": "tasks_source",
                "name": "任务数据",
                "type": "db",
                "entities": ["Task"],
                "schema_refs": ["Task"],
                "seed_strategy": "demo_records",
            }],
        },
    }
    requirement_path.with_suffix(".json").write_text(
        json.dumps(state["requirement_spec"], ensure_ascii=False), encoding="utf-8"
    )
    project_plan_path.with_suffix(".json").write_text(
        json.dumps(state["project_plan"], ensure_ascii=False), encoding="utf-8"
    )
    return state


class ApplicationPagePlanningTests(unittest.TestCase):
    def test_creation_requirements_do_not_expose_clarification_tool(self) -> None:
        """新建应用两阶段门禁应直接生成 JSON，不为派生结构追加澄清。"""

        class FakeModel:
            """记录模型工具绑定与提示词，避免测试发起真实模型调用。"""

            def __init__(self) -> None:
                self.bound = False
                self.prompt = ""

            def bind_tools(self, _tools: list[object]) -> "FakeModel":
                """记录是否暴露了工具。"""

                self.bound = True
                return self

            def invoke(self, prompt: str) -> AIMessage:
                """返回最小结构化需求结果。"""

                self.prompt = prompt
                return AIMessage(content='{"app_info":{"name":"任务中心"}}')

        model = FakeModel()
        settings = type("Settings", (), {"model_name": "test-model"})()
        with (
            patch.object(requirements_analyzer.Settings, "from_env", return_value=settings),
            patch.object(requirements_analyzer, "create_chat_model", return_value=model),
        ):
            result = requirements_analyzer.analyze_requirements_with_chat_model(
                "创建任务中心",
                allow_clarification=False,
            )

        self.assertFalse(model.bound)
        self.assertIn("Do not call ask_user", model.prompt)
        self.assertEqual(result["clarification"]["status"], "clear")

    def test_routes_only_cover_two_planning_nodes(self) -> None:
        """独立创建 Graph 应从 requirements 启动并在确认门禁等待。"""

        self.assertEqual(_route_start({}), "requirements")
        self.assertEqual(_route_start({"resume_from": "project_planning"}), "project_planning")
        self.assertEqual(_route_start({"resume_from": "detail_confirmation"}), "requirements")
        self.assertEqual(
            _route_requirements({"clarification": {"status": "requires_user_input"}}),
            "await_user_input",
        )

    def test_confirmed_plan_only_validates_planning_artifacts(self) -> None:
        """项目规划确认后只应校验 specs/plans 产物，不改写 application.json。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / ".xcodeagent" / "application.json"
            target.parent.mkdir()
            target.write_text(json.dumps({"appName": "任务中心", "preserved": True}), encoding="utf-8")

            original = target.read_text(encoding="utf-8")
            confirmation = confirm_application_planning_artifacts(_confirmed_state(workspace))
            saved = json.loads(target.read_text(encoding="utf-8"))

            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertTrue(saved["preserved"])
            self.assertEqual(
                confirmation["artifacts"]["requirementSpec"]["markdown"]["path"],
                ".xcodeagent/specs/requirement-spec.md",
            )
            self.assertEqual(
                confirmation["artifacts"]["projectPlan"]["json"]["path"],
                ".xcodeagent/plans/project-plan.json",
            )
            self.assertEqual(len(confirmation["artifacts"]["projectPlan"]["markdown"]["sha256"]), 64)
            self.assertEqual(set(confirmation), {"confirmedAt", "directories", "artifacts"})

    def test_project_planning_confirmation_validates_without_detail_node(self) -> None:
        """第二步确认完成时应直接校验目录产物，不再调用细节确认节点。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = _confirmed_state(workspace)
            update = {
                "phase": "project_planning",
                "status": "completed",
                "project_plan": state["project_plan"],
                "project_plan_path": state["project_plan_path"],
            }

            with patch(
                "app.graph.application_planning_workflow.nodes.project_planning",
                return_value=update,
            ) as project_planning:
                result = _project_planning(state)

            project_planning.assert_called_once_with(state)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["workflow_scope"], "application_planning")
            self.assertIn("application_planning_confirmation", result)

    def test_artifact_gate_rejects_unconfirmed_plan_json(self) -> None:
        """plans 中未确认的 ProjectPlan JSON 不得通过工作区入口门禁。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = _confirmed_state(workspace)
            plan_json = workspace / ".xcodeagent" / "plans" / "project-plan.json"
            plan_json.write_text(json.dumps({"confirmation_status": "pending_user_confirmation"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "必须是已确认的 JSON 对象"):
                confirm_application_planning_artifacts(state)

    def test_capability_exposes_workflow_visualization_contract(self) -> None:
        """页面规划端点应声明标准 Workflow 事件和两阶段。"""

        capability = application_page_planning_capabilities()
        self.assertEqual(capability["customEventName"], "workflow-run")
        self.assertEqual(capability["phases"], ["requirements", "project_planning"])
        self.assertEqual(capability["confirmationArtifacts"], ["requirement_spec", "project_plan"])
        self.assertFalse(capability["writesApplicationJsonAfterConfirmation"])
        self.assertEqual(capability["artifactDirectories"], [".xcodeagent/specs", ".xcodeagent/plans"])
        self.assertEqual(capability["workspaceGate"], "planning-artifacts")


if __name__ == "__main__":
    unittest.main()
