from __future__ import annotations

import asyncio
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
from app.protocols.application_page_planning import (
    application_page_planning_capabilities,
    build_application_page_planning_ag_ui_stream,
)
from app.services.application_planning_persistence import confirm_application_planning_artifacts
from app.services.requirement_spec import create_requirement_spec
from app.workspace.spec_documents import write_requirement_spec_document


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
    def test_requirement_spec_draft_save_uses_ag_ui_without_running_graph(self) -> None:
        """草稿保存应返回完整 AG-UI 生命周期，并保持需求处于待确认状态。"""

        spec = create_requirement_spec("创建任务中心")
        spec["confirmation_status"] = "pending_user_confirmation"
        edited = {
            **spec,
            "app_info": {**spec["app_info"], "name": "协作任务中心"},
        }

        with tempfile.TemporaryDirectory() as directory:
            write_requirement_spec_document({"workspace": directory}, spec)
            stream = build_application_page_planning_ag_ui_stream(
                graph=object(),
                payload={
                    "threadId": "draft-thread",
                    "runId": "draft-run",
                    "forwardedProps": {
                        "requirementSpecDraft": {
                            "action": "save",
                            "workspaceRoot": directory,
                            "spec": edited,
                        }
                    },
                },
            )

            async def collect() -> str:
                """消费测试事件流并合并为可断言文本。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect())
            saved = json.loads(
                (Path(directory) / ".xcodeagent/specs/requirement-spec.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertIn("requirement-spec-draft", frames)
        self.assertIn("RUN_STARTED", frames)
        self.assertIn("RUN_FINISHED", frames)
        self.assertEqual(saved["app_info"]["name"], "协作任务中心")
        self.assertEqual(saved["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(
            [page["pageId"] for page in saved["pages"]],
            [page["pageId"] for page in spec["pages"]],
        )

    def test_creation_requirements_expose_clarification_tool(self) -> None:
        """新建应用需求不足时应允许模型集中提出关键澄清问题。"""

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
                """模拟模型发现角色信息不足并调用澄清工具。"""

                self.prompt = prompt
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "ask_user",
                        "args": {
                            "questions": [{
                                "header": "用户角色",
                                "question": "哪些角色会使用任务中心？",
                                "type": "text",
                            }]
                        },
                        "id": "creation-clarification-1",
                        "type": "tool_call",
                    }],
                )

        model = FakeModel()
        settings = type("Settings", (), {"model_name": "test-model"})()
        with (
            patch.object(requirements_analyzer.Settings, "from_env", return_value=settings),
            patch.object(requirements_analyzer, "create_chat_model", return_value=model),
        ):
            result = requirements_analyzer.analyze_requirements_with_chat_model(
                "创建任务中心",
            )

        self.assertTrue(model.bound)
        self.assertNotIn("Do not call ask_user", model.prompt)
        self.assertIn("name and a broad scenario alone are not sufficient", model.prompt)
        self.assertEqual(result["clarification"]["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["questions"][0]["header"], "用户角色")

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
        self.assertEqual(
            capability["editableArtifacts"]["requirement_spec"]["actions"],
            ["save"],
        )
        self.assertEqual(
            capability["editableArtifacts"]["requirement_spec"]["saveActionField"],
            "forwardedProps.requirementSpecDraft",
        )
        self.assertFalse(capability["writesApplicationJsonAfterConfirmation"])
        self.assertEqual(capability["artifactDirectories"], [".xcodeagent/specs", ".xcodeagent/plans"])
        self.assertEqual(capability["workspaceGate"], "planning-artifacts")

    def test_endpoint_forces_application_planning_scope(self) -> None:
        """专用端点不能依赖前端 forwardedProps 才禁用需求澄清。"""

        sentinel = object()
        with patch(
            "app.protocols.application_page_planning.build_workflow_ag_ui_stream",
            return_value=sentinel,
        ) as stream:
            result = build_application_page_planning_ag_ui_stream(
                graph=object(),
                payload={"workflowScope": "unexpected", "forwardedProps": {}},
            )

        self.assertIs(result, sentinel)
        self.assertEqual(stream.call_args.kwargs["payload"]["workflowScope"], "application_planning")


if __name__ == "__main__":
    unittest.main()
