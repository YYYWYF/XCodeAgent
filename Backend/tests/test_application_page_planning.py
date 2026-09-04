from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.main import requirements_analyzer
from app.graph.application_planning_workflow import (
    _technical_planning,
    _requirements,
    _route_requirements,
    _route_start,
    _route_ui_confirmation,
)
from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_revision import RevisionImpact, RevisionTarget
from app.protocols.application_page_planning import (
    application_page_planning_capabilities,
    build_application_page_planning_ag_ui_stream,
)
from app.protocols.workflow.projection import _workflow_confirmation_artifact
from app.services.application_planning_persistence import confirm_application_planning_artifacts
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    create_application_lifecycle,
    load_application_lifecycle,
    transition_application_lifecycle,
    write_application_lifecycle,
)
from app.services.application_revision_lifecycle import (
    register_revision_impact,
    submit_revision_impact,
)
from app.services.requirement_spec import create_requirement_spec
from app.services.product_plan import create_product_plan
from app.workspace.spec_documents import (
    write_requirement_spec_draft_document,
)


def _write_application_config(workspace: Path, datasource_type: str = "database") -> None:
    """为应用规划草稿保存测试写入最小的权威数据源配置。"""

    application_dir = workspace / ".xcodeagent"
    application_dir.mkdir(parents=True, exist_ok=True)
    (application_dir / "application.json").write_text(
        json.dumps({"schemaVersion": 2, "datasource": {"type": datasource_type}}),
        encoding="utf-8",
    )


def _confirmed_state(workspace: Path) -> dict[str, object]:
    """构造包含四阶段当前正式产物的最小状态。"""

    requirement_path = workspace / ".xcodeagent" / "specs" / "requirement-spec.md"
    product_plan_path = workspace / ".xcodeagent" / "plans" / "product-plan.md"
    technical_plan_path = workspace / ".xcodeagent" / "plans" / "technical-plan.md"
    requirement_path.parent.mkdir(parents=True, exist_ok=True)
    product_plan_path.parent.mkdir(parents=True, exist_ok=True)
    requirement_path.write_text("# RequirementSpec\n\n任务中心需求。\n", encoding="utf-8")
    product_plan_path.write_text("# ProductPlan\n\n任务中心产品规划。\n", encoding="utf-8")
    technical_plan_path.write_text("# TechnicalPlan\n\n任务中心技术规划。\n", encoding="utf-8")
    requirement_spec = create_requirement_spec("创建任务中心")
    requirement_spec["confirmation_status"] = "confirmed"
    product_plan = create_product_plan(requirement_spec)
    product_plan["confirmation_status"] = "confirmed"
    technical_plan = {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "architecture": {},
        "engineering_design": {"module_boundaries": [], "data_models": []},
        "api_contracts": [],
        "pages": [],
    }
    state = {
        "workspace": str(workspace),
        "requirement_spec_path": str(requirement_path),
        "product_plan_path": str(product_plan_path),
        "technical_plan_path": str(technical_plan_path),
        "requirement_spec": requirement_spec,
        "product_plan": product_plan,
        "ui_designs": {"confirmation_status": "confirmed", "pages": []},
        "technical_plan": technical_plan,
    }
    requirement_path.with_suffix(".json").write_text(
        json.dumps(state["requirement_spec"], ensure_ascii=False), encoding="utf-8"
    )
    product_plan_path.with_suffix(".json").write_text(
        json.dumps(state["product_plan"], ensure_ascii=False), encoding="utf-8"
    )
    (workspace / ".xcodeagent" / "specs" / "ui-designs.json").write_text(
        json.dumps(state["ui_designs"], ensure_ascii=False), encoding="utf-8"
    )
    technical_plan_path.with_suffix(".json").write_text(
        json.dumps(state["technical_plan"], ensure_ascii=False), encoding="utf-8"
    )
    return state


def _write_planning_stage_entry_lifecycle(
    workspace: Path,
    *,
    initialization_thread_id: str | None = None,
) -> None:
    """把测试生命周期推进到等待进入规划阶段，模拟已确认或已跳过 UI。"""

    state = create_application_lifecycle(
        application_id=workspace.name,
        application_name="业务管理应用",
        initialization_thread_id=initialization_thread_id,
    )
    for stage, status in (
        (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, ApplicationLifecycleStatus.RUNNING),
        (
            ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
            ApplicationLifecycleStatus.RUNNING,
        ),
        (
            ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
            ApplicationLifecycleStatus.AWAITING_USER,
        ),
        (ApplicationLifecycleStage.GENERATING_UI_DESIGNS, ApplicationLifecycleStatus.RUNNING),
        (
            ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
            ApplicationLifecycleStatus.AWAITING_USER,
        ),
    ):
        state = transition_application_lifecycle(state, stage=stage, status=status)
    write_application_lifecycle(workspace, state)


class ApplicationPagePlanningTests(unittest.TestCase):
    def test_checkpoint_recovery_projects_confirmation_without_running_graph(self) -> None:
        """冷启动恢复只读取 checkpoint，并重新投影需求确认卡。"""

        class RecoveryGraph:
            """提供只读 checkpoint 接口，并在错误调用执行方法时失败。"""

            async def aget_state(self, config: dict[str, object]):
                """返回同一 thread 的待确认需求状态。"""

                self.config = config
                return type("Snapshot", (), {"values": self.values})()

            async def astream(self, *_args, **_kwargs):
                """禁止恢复动作执行 Graph。"""

                raise AssertionError("恢复动作不应执行 Graph。")

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / ".xcodeagent" / "drafts" / "specs" / "requirement-spec.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("# RequirementSpec\n\n待确认需求。\n", encoding="utf-8")
            graph = RecoveryGraph()
            graph.values = {
                "active_run_id": "original-run",
                "phase": "requirements",
                "status": "requires_user_input",
                "timeline": ["requirements"],
                "requirement_spec": {
                    "confirmation_status": "pending_user_confirmation",
                    "app_info": {"name": "任务中心"},
                },
                # 待确认阶段应恢复 checkpoint 指向的草稿工件。
                "requirements_confirmed": False,
                "requirement_spec_path": str(artifact),
                "clarification": {
                    "mode": "requirement_document_confirmation",
                    "status": "requires_user_input",
                    "questions": [],
                },
            }
            stream = build_application_page_planning_ag_ui_stream(
                graph=graph,
                payload={
                    "threadId": "planning-thread",
                    "runId": "recovery-run",
                    "forwardedProps": {
                        "applicationPlanningRecovery": {
                            "action": "get",
                            "workspaceRoot": directory,
                            "applicationId": "app-1",
                        }
                    },
                },
            )

            async def collect() -> str:
                """消费只读恢复事件流。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect())

        self.assertEqual(
            graph.config,
            {"configurable": {"thread_id": "planning-thread"}},
        )
        self.assertIn("workflow-run", frames)
        self.assertIn("requirement_document_confirmation", frames)
        self.assertIn("confirmationArtifact", frames)
        self.assertIn("待确认需求", frames)
        self.assertIn("RUN_FINISHED", frames)

    def test_requirement_spec_draft_save_uses_ag_ui_without_running_graph(self) -> None:
        """草稿保存应返回完整 AG-UI 生命周期，并保持需求处于待确认状态。"""

        spec = create_requirement_spec("创建任务中心")
        spec["confirmation_status"] = "pending_user_confirmation"
        edited = {
            **spec,
            "app_info": {**spec["app_info"], "name": "协作任务中心"},
        }

        with tempfile.TemporaryDirectory() as directory:
            _write_application_config(Path(directory))
            write_requirement_spec_draft_document({"workspace": directory}, spec)
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
                (Path(directory) / ".xcodeagent/drafts/specs/requirement-spec.json").read_text(
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
                    # 即使工具调用消息夹带了与需求无关的页面 JSON，也不应在提问阶段采用。
                    content=json.dumps(
                        {
                            "feature_modules": [
                                {
                                    "id": "core_management",
                                    "name": "核心业务管理",
                                }
                            ],
                            "pages": [
                                {
                                    "pageId": "dashboard_page",
                                    "name": "概览页",
                                    "path": "/page/home",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
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
        self.assertEqual(result["requirement_spec"]["pages"], [])
        self.assertEqual(result["requirement_spec"]["feature_modules"], [])
        self.assertEqual(result["requirement_spec"]["entities"], [])
        self.assertEqual(result["requirement_spec"]["business_flows"], [])

    def test_streaming_requirements_merge_chunked_ask_user_tool_call(self) -> None:
        """流式模型拆分 ask_user 参数时仍应恢复成可展示的澄清问题。"""

        class FakeStreamingModel:
            """模拟工具调用参数分块到达的聊天模型。"""

            def bind_tools(self, _tools: list[object]) -> "FakeStreamingModel":
                """保持与 LangChain ChatModel bind_tools 接口兼容。"""

                return self

            def stream(self, _prompt: str):
                """返回分块的 ask_user 工具调用。"""

                yield AIMessageChunk(
                    content="",
                    tool_call_chunks=[{
                        "id": "call-stream-1",
                        "name": "ask_user",
                        "args": '{"questions":[{"header":"角色",',
                        "index": 0,
                    }],
                )
                yield AIMessageChunk(
                    content="",
                    tool_call_chunks=[{
                        "id": None,
                        "name": None,
                        "args": '"question":"主要使用者是谁？","type":"text"}]}',
                        "index": 0,
                    }],
                )

        settings = type("Settings", (), {"model_name": "test-model"})()
        with (
            patch.object(requirements_analyzer.Settings, "from_env", return_value=settings),
            patch.object(requirements_analyzer, "create_chat_model", return_value=FakeStreamingModel()),
        ):
            result = requirements_analyzer.analyze_requirements_with_chat_model(
                "创建任务中心",
                on_token=lambda _token: None,
            )

        self.assertEqual(result["clarification"]["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["questions"][0]["header"], "角色")
        self.assertEqual(result["clarification"]["questions"][0]["question"], "主要使用者是谁？")

    def test_routes_cover_design_and_independent_planning_stages(self) -> None:
        """独立创建 Graph 应把技术规划放在设计与开发之间的独立规划阶段。"""

        self.assertEqual(_route_start({}), "requirements")
        with self.assertRaisesRegex(
            ApplicationLifecycleConflictError,
            "不支持恢复入口",
        ):
            _route_start({"resume_from": "project_planning"})
        self.assertEqual(_route_start({"resume_from": "product_planning"}), "product_planning")
        self.assertEqual(_route_start({"resume_from": "ui_confirmation"}), "ui_confirmation")
        self.assertEqual(
            _route_start({"resume_from": "planning_stage_entry"}),
            "planning_stage_entry",
        )
        with self.assertRaisesRegex(
            ApplicationLifecycleConflictError,
            "不支持恢复入口",
        ):
            _route_start({"resume_from": "detail_confirmation"})
        self.assertEqual(
            _route_ui_confirmation({"clarification": {"status": "clear", "skipped": True}}),
            "planning_stage_entry",
        )
        self.assertEqual(
            _route_requirements(
                {
                    "clarification": {
                        "status": "requires_user_input",
                        "mode": "ask_user_question",
                    }
                }
            ),
            "requirements_review",
        )
        self.assertEqual(
            _route_requirements(
                {
                    "clarification": {
                        "status": "requires_user_input",
                        "mode": "requirement_document_confirmation",
                    }
                }
            ),
            "product_planning",
        )

    def test_existing_planning_lifecycle_without_resume_fails_closed(self) -> None:
        """已有规划生命周期缺少恢复动作时不能静默回到 requirements。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = transition_application_lifecycle(
                lifecycle,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
            )
            write_application_lifecycle(directory, lifecycle)

            with self.assertRaisesRegex(
                ApplicationLifecycleConflictError,
                "缺少明确的 interaction 或 resume_from",
            ):
                _route_start({"workspace": directory, "resume_from": ""})

    def test_stale_requirements_resume_is_rejected_during_technical_planning(self) -> None:
        """TechnicalPlan 阶段即使显式携带旧 requirements 入口也必须拒绝。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            for stage, status in (
                (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, ApplicationLifecycleStatus.RUNNING),
                (ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT, ApplicationLifecycleStatus.RUNNING),
                (
                    ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
                (ApplicationLifecycleStage.GENERATING_UI_DESIGNS, ApplicationLifecycleStatus.RUNNING),
                (
                    ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
                (
                    ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
                (ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN, ApplicationLifecycleStatus.RUNNING),
                (
                    ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
            ):
                lifecycle = transition_application_lifecycle(
                    lifecycle,
                    stage=stage,
                    status=status,
                )
            write_application_lifecycle(directory, lifecycle)

            with self.assertRaisesRegex(
                ApplicationLifecycleConflictError,
                "恢复入口与 lifecycle 不匹配",
            ):
                _route_start(
                    {"workspace": directory, "resume_from": "requirements"}
                )

    def test_requirement_failure_and_retry_update_lifecycle(self) -> None:
        """需求生成失败应持久化错误，同一阶段重试后可恢复到待澄清。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {
                "workspace": directory,
                "project_id": "app-1",
                "application_name": "任务中心",
                "active_thread_id": "thread-1",
                "active_run_id": "run-1",
            }
            with patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                side_effect=RuntimeError("model unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                    _requirements(state)
            failed = load_application_lifecycle(directory)
            assert failed is not None and failed.error is not None
            self.assertEqual(failed.initialization.status.value, "failed")

            with patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                return_value={
                    "phase": "requirements",
                    "status": "requires_user_input",
                    "requirement_spec": {
                        "confirmation_status": "pending_user_input",
                    },
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "questions": [{"id": "role", "header": "角色"}],
                    },
                },
            ):
                retried = _requirements({**state, "active_run_id": "run-2"})

            self.assertEqual(
                retried["lifecycle"]["initialization"]["stage"],
                "awaiting_requirement_clarification",
            )
            self.assertIsNone(retried["lifecycle"]["error"])

    def test_requirement_confirmation_status_drives_lifecycle_mapping(self) -> None:
        """只有 RequirementSpec 待确认时才能展示需求文档已生成。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {
                "workspace": directory,
                "project_id": "app-1",
                "application_name": "任务中心",
            }
            with patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                return_value={
                    "phase": "requirements",
                    "status": "requires_user_input",
                    "requirement_spec": {
                        "confirmation_status": "pending_user_confirmation",
                    },
                    "clarification": {
                        "mode": "requirement_document_confirmation",
                        "status": "requires_user_input",
                        "questions": [{"id": "confirmation", "header": "需求确认"}],
                    },
                },
            ):
                result = _requirements(state)

            self.assertEqual(
                result["lifecycle"]["initialization"]["stage"],
                "awaiting_requirement_document_confirmation",
            )
            self.assertNotIn("pendingInteraction", result["lifecycle"])

    def test_requirement_cancellation_is_recoverable(self) -> None:
        """取消需求生成应保留阶段并写入 cancelled，而不是伪装成完成。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {
                "workspace": directory,
                "project_id": "app-1",
                "application_name": "任务中心",
            }
            with patch(
                "app.graph.application_planning_workflow.nodes.requirements",
                side_effect=asyncio.CancelledError,
            ):
                with self.assertRaises(asyncio.CancelledError):
                    _requirements(state)
            cancelled = load_application_lifecycle(directory)
            assert cancelled is not None
            self.assertEqual(cancelled.initialization.status.value, "cancelled")

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
                confirmation["artifacts"]["technicalPlan"]["json"]["path"],
                ".xcodeagent/plans/technical-plan.json",
            )
            self.assertEqual(len(confirmation["artifacts"]["technicalPlan"]["markdown"]["sha256"]), 64)
            self.assertEqual(set(confirmation), {"confirmedAt", "directories", "artifacts"})

    def test_technical_planning_confirmation_validates_without_detail_node(self) -> None:
        """技术规划确认完成时应直接校验四阶段正式产物。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = _confirmed_state(workspace)
            _write_planning_stage_entry_lifecycle(workspace)
            state["application_planning_interaction"] = {
                "action": "enter_planning",
                "artifact": "ui_designs",
            }
            update = {
                "phase": "technical_planning",
                "status": "completed",
                "technical_plan": state["technical_plan"],
                "technical_plan_path": state["technical_plan_path"],
            }

            with patch(
                "app.graph.application_planning_workflow.nodes.project_planning",
                return_value=update,
            ) as project_planning:
                result = _technical_planning({**state, "workflow_scope": "application_planning"})

            project_planning.assert_called_once_with({**state, "workflow_scope": "application_planning"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["workflow_scope"], "application_planning")
            self.assertIn("application_planning_confirmation", result)
            self.assertEqual(result["revision_continuation"], {})
            lifecycle = load_application_lifecycle(workspace)
            assert lifecycle is not None
            self.assertEqual(
                lifecycle.initialization.stage,
                ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            )

    def test_old_confirmed_plan_does_not_advance_lifecycle_before_regeneration(self) -> None:
        """进入规划时旧 confirmed TechnicalPlan 不能提前触发待确认生命周期。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = _confirmed_state(workspace)
            _write_planning_stage_entry_lifecycle(workspace)
            state["application_planning_interaction"] = {
                "action": "enter_planning",
                "artifact": "ui_designs",
            }
            observed_stages: list[ApplicationLifecycleStage] = []

            def project_planning(_state: dict[str, object]) -> dict[str, object]:
                """记录模型节点执行时的生命周期，并返回本轮新待确认计划。"""

                current = load_application_lifecycle(workspace)
                assert current is not None
                observed_stages.append(current.initialization.stage)
                return {
                    "phase": "technical_planning",
                    "status": "requires_user_input",
                    "technical_plan": {
                        **state["technical_plan"],
                        "confirmation_status": "pending_user_confirmation",
                    },
                    "clarification": {
                        "status": "requires_user_input",
                        "mode": "technical_plan_confirmation",
                    },
                }

            with patch(
                "app.graph.application_planning_workflow.nodes.project_planning",
                side_effect=project_planning,
            ):
                _technical_planning(
                    {**state, "workflow_scope": "application_planning"}
                )

            self.assertEqual(
                observed_stages,
                [ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN],
            )
            lifecycle = load_application_lifecycle(workspace)
            assert lifecycle is not None
            self.assertEqual(
                lifecycle.initialization.stage,
                ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            )

    def test_design_revision_technical_plan_issues_continuation_without_template_stage(self) -> None:
        """二次修改确认 TechnicalPlan 后应直接续接 Build，不能再次生成应用模板。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = _confirmed_state(workspace)
            _write_planning_stage_entry_lifecycle(
                workspace,
                initialization_thread_id="planning-thread",
            )
            register_revision_impact(
                workspace,
                interaction_id="impact-design",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="把订单页改成双列布局",
                target=RevisionTarget(type="page", pageId="orders"),
                impact=RevisionImpact(
                    formalBranch="design_stage_revision",
                    revisionType="ui_visual_change",
                    earliestArtifact="ui-design",
                    affectedArtifacts=["ui-design", "technical-plan"],
                    affectedResources=["page:orders"],
                    reason="页面视觉目标变化",
                ),
            )
            active = submit_revision_impact(
                workspace,
                interaction_id="impact-design",
                decision="approved",
            )
            assert active is not None
            state["application_planning_interaction"] = {
                "action": "enter_planning",
                "artifact": "ui_designs",
            }
            update = {
                "phase": "technical_planning",
                "status": "completed",
                "technical_plan": state["technical_plan"],
                "technical_plan_path": state["technical_plan_path"],
            }

            with patch(
                "app.graph.application_planning_workflow.nodes.project_planning",
                return_value=update,
            ):
                result = _technical_planning(
                    {**state, "workflow_scope": "application_planning"}
                )

            continuation = result["revision_continuation"]
            self.assertEqual(continuation["action"], "continue_revision_build")
            self.assertEqual(continuation["changeId"], active.change_id)
            lifecycle = load_application_lifecycle(workspace)
            assert lifecycle is not None
            self.assertEqual(
                lifecycle.initialization.stage,
                ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            )
            self.assertNotEqual(
                lifecycle.initialization.stage,
                ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            )
            assert lifecycle.active_formal_revision is not None
            self.assertEqual(lifecycle.active_formal_revision.status, "continuation_ready")

    def test_workbench_revision_technical_plan_issues_continuation_without_execution_binding(self) -> None:
        """独立 application_planning 完成 TechnicalPlan 后不要求工作台 execution。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = _confirmed_state(workspace)
            _write_planning_stage_entry_lifecycle(
                workspace,
                initialization_thread_id="planning-thread",
            )
            register_revision_impact(
                workspace,
                interaction_id="impact-workbench",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="修改订单接口契约",
                target=RevisionTarget(type="application"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["application"],
                    reason="接口契约变化",
                ),
            )
            active = submit_revision_impact(
                workspace,
                interaction_id="impact-workbench",
                decision="approved",
            )
            assert active is not None
            state["active_run_id"] = "planning-run"
            state["application_planning_interaction"] = {
                "action": "enter_planning",
                "artifact": "ui_designs",
            }
            update = {
                "phase": "technical_planning",
                "status": "completed",
                "technical_plan": state["technical_plan"],
                "technical_plan_path": state["technical_plan_path"],
            }

            with patch(
                "app.graph.application_planning_workflow.nodes.project_planning",
                return_value=update,
            ):
                result = _technical_planning(
                    {**state, "workflow_scope": "application_planning"}
                )

            continuation = result["revision_continuation"]
            self.assertEqual(continuation["action"], "continue_revision_build")
            self.assertEqual(continuation["changeId"], active.change_id)
            lifecycle = load_application_lifecycle(workspace)
            assert lifecycle is not None and lifecycle.active_formal_revision is not None
            self.assertEqual(
                lifecycle.active_formal_revision.status,
                "continuation_ready",
            )
            self.assertFalse(lifecycle.active_executions)

    def test_technical_planning_cannot_run_before_stage_entry(self) -> None:
        """直接调度 TechnicalPlan 时必须在模型调用前拒绝，不能绕过规划入口。"""

        with tempfile.TemporaryDirectory() as directory:
            state = _confirmed_state(Path(directory))
            with patch(
                "app.graph.application_planning_workflow.nodes.project_planning"
            ) as project_planning:
                with self.assertRaisesRegex(ValueError, "明确进入规划阶段"):
                    _technical_planning({**state, "workflow_scope": "application_planning"})
            project_planning.assert_not_called()

    def test_artifact_gate_rejects_unconfirmed_plan_json(self) -> None:
        """plans 中未确认的 TechnicalPlan JSON 不得通过工作区入口门禁。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = _confirmed_state(workspace)
            plan_json = workspace / ".xcodeagent" / "plans" / "technical-plan.json"
            plan_json.write_text(json.dumps({"confirmation_status": "pending_user_confirmation"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "必须是已确认的 JSON 对象"):
                confirm_application_planning_artifacts(state)

    def test_artifact_gate_accepts_explicitly_skipped_ui_design(self) -> None:
        """工作区入口应接受用户明确跳过后的 UI Manifest。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = _confirmed_state(workspace)
            skipped = {"confirmation_status": "skipped", "pages": []}
            state["ui_designs"] = skipped
            (workspace / ".xcodeagent" / "specs" / "ui-designs.json").write_text(
                json.dumps(skipped),
                encoding="utf-8",
            )

            confirmation = confirm_application_planning_artifacts(state)

        self.assertEqual(
            confirmation["artifacts"]["uiDesigns"]["json"]["path"],
            ".xcodeagent/specs/ui-designs.json",
        )

    def test_capability_exposes_workflow_visualization_contract(self) -> None:
        """页面规划端点应声明标准 Workflow 事件和两阶段。"""

        capability = application_page_planning_capabilities()
        self.assertEqual(capability["customEventName"], "workflow-run")
        self.assertEqual(
            capability["recoveryActionField"],
            "forwardedProps.applicationPlanningRecovery",
        )
        self.assertEqual(
            capability["phases"],
            [
                "requirements",
                "product_planning",
                "ui_confirmation",
                "planning_stage_entry",
                "technical_planning",
            ],
        )
        self.assertEqual(
            capability["clarificationModes"]["planningStageEntry"],
            "planning_stage_entry_confirmation",
        )
        self.assertEqual(
            capability["confirmationArtifacts"],
            ["requirement_spec", "product_plan", "ui_designs", "technical_plan"],
        )
        self.assertEqual(capability["artifactSchemas"]["ui_designs"], "ui-manifest.v3")
        self.assertIn("skip", capability["uiDesignActions"])
        self.assertEqual(
            capability["editableArtifacts"]["requirement_spec"]["actions"],
            ["save"],
        )
        self.assertEqual(
            capability["editableArtifacts"]["requirement_spec"]["saveActionField"],
            "forwardedProps.requirementSpecDraft",
        )
        self.assertEqual(
            capability["draftArtifacts"]["product_plan"]["writes"],
            [
                "drafts/plans/product-plan.md",
                "drafts/plans/product-plan.json",
            ],
        )
        self.assertFalse(capability["writesApplicationJsonAfterConfirmation"])
        self.assertEqual(
            capability["artifactDirectories"],
            [
                ".xcodeagent/drafts/specs",
                ".xcodeagent/drafts/plans",
                ".xcodeagent/specs",
                ".xcodeagent/plans",
            ],
        )
        self.assertEqual(capability["workspaceGate"], "planning-artifacts")
        self.assertNotIn("lifecycle", capability)

    def test_product_confirmation_projects_only_draft_markdown(self) -> None:
        """产品规划待确认时只允许把 drafts/plans 下的 Markdown 投影给前端。"""

        with tempfile.TemporaryDirectory() as directory:
            draft_path = Path(directory) / ".xcodeagent/drafts/plans/product-plan.md"
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text("# 产品规划草稿\n", encoding="utf-8")
            result = {
                "phase": "product_planning",
                "status": "requires_user_input",
                "product_plan_path": str(draft_path),
                "clarification": {
                    "status": "requires_user_input",
                    "mode": "requirement_document_confirmation",
                },
            }

            artifact = _workflow_confirmation_artifact(result)
            result["product_plan_path"] = str(
                Path(directory) / ".xcodeagent/plans/product-plan.md"
            )

        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["path"], str(draft_path))
        self.assertIsNone(_workflow_confirmation_artifact(result))

    def test_lifecycle_action_is_rejected_without_running_graph(self) -> None:
        """旧页面规划端点应以完整 AG-UI 失败流拒绝 lifecycle 动作。"""

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "app.protocols.application_page_planning.build_workflow_ag_ui_stream"
            ) as graph_stream:
                stream = build_application_page_planning_ag_ui_stream(
                    graph=object(),
                    payload={
                        "threadId": "legacy-thread",
                        "runId": "legacy-run",
                        "forwardedProps": {
                            "applicationLifecycle": {
                                "action": "create",
                                "workspaceRoot": directory,
                                "application": {
                                    "id": "app-1",
                                    "appName": "任务中心",
                                },
                            }
                        },
                    },
                )

                async def collect() -> str:
                    """消费拒绝事件流并返回全部 SSE 文本。"""

                    return "".join([frame async for frame in stream])

                frames = asyncio.run(collect())

        graph_stream.assert_not_called()
        self.assertIn("unsupported_application_lifecycle", frames)
        self.assertIn("/application-lifecycle/run", frames)
        self.assertIn("RUN_FINISHED", frames)
        self.assertIn('"status":"failed"', frames)

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
