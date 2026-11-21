"""验证初次开发状态、目录校准与全部产物测试门禁的真实持久化边界。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage, ApplicationLifecycleStatus, PendingInteractionType,
    WorkbenchExecutionStatus,
)
# 这些函数名以 test_ 开头，pytest 会把模块级同名符号当作测试收集，故统一取别名。
from app.graph.nodes.lifecycle import test_phase_confirmation as run_test_phase_confirmation
from app.graph.subgraphs.testing import integration_test
from app.protocols.workflow.lifecycle import begin_workflow_lifecycle
from app.services.application_lifecycle import (
    application_lifecycle_payload, create_application_lifecycle, end_workbench_execution,
    load_application_lifecycle, persist_workbench_interaction_submission,
    start_workbench_execution, update_workbench_execution, write_application_lifecycle,
)
from app.domain.development_artifacts import DevelopmentArtifactTarget
from app.services.development_artifacts import (
    DevelopmentArtifactsIncompleteError, complete_initial_development,
    refresh_development_artifacts, require_test_entry, test_entry_gate as read_test_entry_gate,
)
from app.services.entity_development_skip import skip_entity_development


class DevelopmentArtifactsTests(unittest.TestCase):
    """每个测试使用独立的当前规划与 lifecycle，不推断历史完成状态。"""

    def setUp(self) -> None:
        """准备两个页面和一个接口，实体场景按测试显式加入。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.plans = self.workspace / ".devagentstudio/plans"
        self.plans.mkdir(parents=True)
        self.product = {"confirmation_status": "confirmed", "pages": [{"pageId": "one"}, {"pageId": "two"}]}
        self.technical = {
            "confirmation_status": "confirmed", "entities": [],
            "api_contracts": [{"id": "api", "endpoints": [{"id": "get"}]}],
        }
        self.write_plans()
        self.write_change_snapshot()
        state = create_application_lifecycle(application_id="test", application_name="测试")
        state.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
        state.initialization.status = ApplicationLifecycleStatus.COMPLETED
        write_application_lifecycle(self.workspace, state)

    def tearDown(self) -> None:
        """关闭协议读取创建的真实 SQLite 连接，避免回归结束后工作线程仍占用临时目录。"""
        from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace

        asyncio.run(close_workflow_checkpointer_for_workspace(workspace=str(self.workspace)))

    def write_plans(self) -> None:
        """写入已确认的正式目录，用于模拟目录变化。"""

        for name, value in (("product-plan", self.product), ("technical-plan", self.technical)):
            (self.plans / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")

    def write_change_snapshot(self, *, pages=None, endpoints=None, entities=None) -> None:
        """写入本轮变更快照；默认全部未变更，用例按需覆盖。"""

        payload = {
            "schemaVersion": 1,
            "pages": pages if pages is not None else {"one": "unchanged", "two": "unchanged"},
            "endpoints": endpoints if endpoints is not None else {"api": {"get": "unchanged"}},
            "entities": entities or {},
        }
        (self.workspace / ".devagentstudio/development-change-snapshot.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def start(self, target: str, *, run_id: str | None = None, replaces: str | None = None, initial: bool = True) -> str:
        """通过真实 lifecycle 服务登记单个目标的初次开发或续接。"""

        run_id = run_id or f"run-{target}"
        start_workbench_execution(
            self.workspace, scope="endpoint" if target == "get" else "page",
            target_id=target, page_id=None if target == "get" else target,
            api_contract_id="api" if target == "get" else None,
            thread_id=f"thread-{target}", run_id=run_id,
            phase="api_design_readiness_gate", initial_development_entry=initial,
            replaces_run_id=replaces,
        )
        return run_id

    def finish(self, target: str) -> None:
        """模拟服务端已经通过 Build 与单测后提交当前目标。"""

        run_id = self.start(target)
        complete_initial_development(self.workspace, run_id=run_id)

    def test_initial_catalog_is_gray(self) -> None:
        """目录初始化不因实体数量或规划文件存在而标记开发完成。"""

        state = refresh_development_artifacts(self.workspace)
        gate = read_test_entry_gate(state)
        self.assertEqual((gate.total, gate.completed, gate.pending), (3, 0, 3))
        self.assertFalse(gate.allowed)
        self.assertNotIn("testEntryGate", json.loads((self.workspace / ".devagentstudio/application-lifecycle.json").read_text()))
        self.assertEqual(application_lifecycle_payload(state)["testEntryGate"]["total"], 3)

    def test_snapshot_source_prefers_display_plan_after_strip(self) -> None:
        """`technical_plan` 被换成干净版后，快照必须仍从展示版取到删除结论。"""

        from app.services.development_change_snapshot import (
            change_snapshot_path, write_change_snapshot,
        )
        from app.services.technical_plan_merger import strip_change_status

        display = {"pages": [
            {"pageId": "one", "_change_status": "unchanged"},
            {"pageId": "two", "_change_status": "deleted"},
        ]}
        # 确认节点手里可能只剩剥离后的版本；展示版由 `technical_plan_diff` 单独保有。
        stripped = strip_change_status(display)
        state = {"technical_plan": stripped, "technical_plan_diff": display}
        chosen = state.get("technical_plan_diff") or state.get("technical_plan")

        target = self.workspace / ".devagentstudio/display-source"
        target.mkdir(parents=True, exist_ok=True)
        write_change_snapshot(target, technical_plan=chosen)
        self.assertEqual(
            json.loads(change_snapshot_path(target).read_text())["pages"]["two"], "deleted"
        )

    def test_unmarked_plan_is_rejected_instead_of_writing_all_unchanged(self) -> None:
        """没有逐项标记的计划不能生成快照：全 unchanged 会让改过的产物被静默跳过。"""

        from app.services.development_change_snapshot import (
            change_snapshot_path, write_change_snapshot,
        )

        target = self.workspace / ".devagentstudio/marked-plan"
        target.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(ValueError):
            write_change_snapshot(target, technical_plan={"pages": [{"pageId": "one"}]})
        self.assertFalse(change_snapshot_path(target).exists())
        # 带标记时正常写出，且删除结论如实保留。
        write_change_snapshot(
            target,
            technical_plan={"pages": [
                {"pageId": "one", "_change_status": "unchanged"},
                {"pageId": "two", "_change_status": "deleted"},
            ]},
        )
        self.assertEqual(json.loads(change_snapshot_path(target).read_text())["pages"]["two"], "deleted")

    def test_deleted_page_remains_pending_until_cleanup_completes(self) -> None:
        """计划删除不是源码清理完成；删除目标仍参与开发门禁并需本轮完成证据。"""

        from app.services.development_artifacts import complete_scope_target_development
        self.finish("one")
        self.finish("two")
        self.finish("get")
        self.write_change_snapshot(pages={"one": "unchanged", "two": "deleted"})
        state = refresh_development_artifacts(self.workspace)
        self.assertIn("two", state.development_artifacts.pages)
        self.assertEqual(read_test_entry_gate(state).total, 3)
        self.assertFalse(read_test_entry_gate(state).allowed)
        self.assertIn(DevelopmentArtifactTarget(type="page", pageId="two"), read_test_entry_gate(state).blockers)
        state = complete_scope_target_development(
            self.workspace, scope={"type": "page", "targetId": "two"},
            run_id="delete-page", thread_id="delete-page-thread",
        )
        self.assertTrue(read_test_entry_gate(state).allowed)
        self.assertTrue(read_test_entry_gate(refresh_development_artifacts(self.workspace)).allowed)

    def test_deleted_endpoint_blocks_tests_after_page_cleanup(self) -> None:
        """删除页面不会完成独立的接口删除任务，继承的旧完成记录不能放行测试。"""

        from app.services.development_artifacts import complete_scope_target_development
        for target in ("one", "two", "get"):
            self.finish(target)
        self.write_change_snapshot(
            pages={"one": "unchanged", "two": "deleted"}, endpoints={"api": {"get": "deleted"}},
        )
        complete_scope_target_development(
            self.workspace, scope={"type": "page", "targetId": "two"},
            run_id="delete-page", thread_id="delete-page-thread",
        )
        gate = read_test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.blockers, [DevelopmentArtifactTarget(type="endpoint", apiContractId="api", endpointId="get")])
        state = complete_scope_target_development(
            self.workspace, scope={"type": "endpoint", "targetId": "get", "apiContractId": "api"},
            run_id="delete-endpoint", thread_id="delete-endpoint-thread",
        )
        self.assertTrue(read_test_entry_gate(state).allowed)

    def test_deletion_confirm_handoff_enters_tests_without_dag_or_api_mapping(self) -> None:
        """真实 AG-UI/SQLite 跨线程确认删除结果后只进入测试，不重生成 DAG 或重新配置 API。"""

        from langgraph.graph import START, END
        from app.graph.state import ProjectState
        from app.graph.workflow import route_test_phase_confirmation, route_workflow_start
        from app.persistence.checkpoints import workflow_checkpointer
        from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
        from app.services.stage_artifacts import ArtifactStateGraph
        from app.graph.nodes.development_readiness import delete_development_page
        for target in ("one", "two", "get"):
            self.finish(target)
        self.write_change_snapshot(pages={"one": "unchanged", "two": "deleted"})
        root = self.workspace / ".devagentstudio"
        (root / "application.json").write_text('{}')
        (root / "specs").mkdir(exist_ok=True)
        (root / "specs/requirement-spec.json").write_text(json.dumps({"confirmation_status": "confirmed"}))
        observed = []

        def test_probe(state: ProjectState) -> dict:
            """只替换实际测试命令，确认前置读取及路由保留真实生产实现。"""
            observed.append("integration_test")
            self.assertEqual(state["deleted_page_result"], {"pageId": "two", "pageLabel": "two", "directories": [], "routeEntries": [], "referencedBy": [], "completed": True})
            return {"phase": "integration_test", "status": "requires_user_input",
                    "clarification": {"mode": "unit_test_confirmation", "status": "requires_user_input", "questions": []}}

        async def collect() -> list[dict]:
            """使用真实删除、阶段读取、原生服务端续接、执行记录与两个独立线程。"""
            saver = await workflow_checkpointer(workspace=str(self.workspace))
            builder = ArtifactStateGraph(ProjectState)
            builder.add_node("entry", lambda state: {})
            builder.add_node("delete_development_page", delete_development_page)
            builder.add_node("test_phase_confirmation", run_test_phase_confirmation)
            builder.add_node("integration_test", test_probe)
            builder.add_node("api_design_readiness_gate", lambda state: (_ for _ in ()).throw(AssertionError("不能回到 API 映射")))
            builder.add_edge(START, "entry")
            builder.add_conditional_edges("entry", route_workflow_start, {
                "delete_development_page": "delete_development_page", "test_phase_confirmation": "test_phase_confirmation",
                "api_design_readiness_gate": "api_design_readiness_gate",
            })
            builder.add_edge("delete_development_page", END)
            builder.add_conditional_edges("test_phase_confirmation", route_test_phase_confirmation, {"integration_test": "integration_test"})
            builder.add_edge("integration_test", END)
            graph = builder.compile(checkpointer=saver)
            payload = {"threadId": "delete-thread", "runId": "delete-run", "request": "删除页面 two", "forwardedProps": {
                "workspaceRoot": str(self.workspace), "selectedPageId": "two", "buildExecutionScope": {"type": "page", "targetId": "two"},
            }}
            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload=payload)]
            source = await graph.aget_state({"configurable": {"thread_id": "delete-thread"}})
            self.assertEqual(source.values.get("phase"), "test_phase_confirmation", "\n".join(frames))
            confirm = {"threadId": "test-thread", "runId": "test-run", "message": "进入测试阶段", "forwardedProps": {
                "workspaceRoot": str(self.workspace), "selectedPageId": "two",
                "resumeReference": {"runId": "delete-run", "threadId": "delete-thread"}, "resumeExecutionRunId": "delete-run",
                "clarificationAnswers": {"test_phase_confirmation": {"action": "confirm"}},
            }}
            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload=confirm)]
            return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

        # 本组最小规划仅用于生命周期；完整业务合同校验已有规划回归覆盖。
        with (
            patch("app.protocols.workflow.request._project_plan_start_values", return_value={}),
            patch("app.services.page_implementation_contract.materialize_technical_plan_runtime", side_effect=lambda plan, *_args: plan),
        ):
            events = asyncio.run(collect())
        self.assertEqual(observed, ["integration_test"])
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual(events[-1]["result"]["workflow"]["state"]["clarification"]["mode"], "unit_test_confirmation")

    def test_modified_completed_artifact_must_be_redone(self) -> None:
        """已完成但本轮改过的产物要重做，否则测试门禁会错误放行。"""

        self.finish("one")
        self.finish("two")
        self.finish("get")
        self.assertTrue(read_test_entry_gate(refresh_development_artifacts(self.workspace)).allowed)

        # 本轮技术规划修改了页面 two：它必须回到"待重做"，门禁随之关闭。
        self.write_change_snapshot(pages={"one": "unchanged", "two": "modified"})
        state = refresh_development_artifacts(self.workspace)
        progress = state.development_artifacts.pages["two"]
        self.assertEqual(progress.initial_development_status, "completed")
        self.assertEqual(progress.change_status, "changed")
        self.assertFalse(read_test_entry_gate(state).allowed)
        self.assertIn(
            DevelopmentArtifactTarget(type="page", pageId="two"),
            read_test_entry_gate(state).blockers,
        )

    def test_missing_change_snapshot_closes_test_entry(self) -> None:
        """无法判断"改没改"时宁可拒绝测试，也不能静默按未变更放行。"""

        for target in ("one", "two", "get"):
            self.finish(target)
        (self.workspace / ".devagentstudio/development-change-snapshot.json").unlink()
        gate = read_test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertFalse(gate.allowed)
        self.assertIn("变更快照", gate.reason or "")

    def test_entity_skip_persists_without_confirming_formal_binding(self) -> None:
        """显式跳过仅移除实体门禁，冷读仍保留理由，正式技术规划保持原样。"""

        self.technical["entities"] = [{"id": "entity"}]
        self.write_plans()
        original = (self.plans / "technical-plan.json").read_bytes()
        for target in ("one", "two", "get"):
            self.finish(target)
        state = skip_entity_development(self.workspace, entity_id="entity", reason="当前应用不需要实体开发")
        self.assertTrue(read_test_entry_gate(state).allowed)
        self.assertEqual(read_test_entry_gate(state).total, 3)
        refreshed = refresh_development_artifacts(self.workspace)
        entity = refreshed.development_artifacts.entities["entity"]
        self.assertEqual(entity.initial_development_status, "skipped")
        self.assertEqual(entity.skip_reason, "当前应用不需要实体开发")
        self.assertIsNotNone(entity.skipped_at)
        self.assertEqual((self.plans / "technical-plan.json").read_bytes(), original)

    def test_entity_skip_does_not_skip_other_entities_or_pages(self) -> None:
        """实体是否跳过均不影响门禁，未完成页面和接口仍阻止进入测试。"""

        self.technical["entities"] = [{"id": "entity"}, {"id": "other"}]
        self.write_plans()
        state = skip_entity_development(self.workspace, entity_id="entity", reason="只跳过该实体")
        gate = read_test_entry_gate(state)
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.total, 3)
        self.assertNotIn("other", [target.entity_id for target in gate.blockers])
        self.assertNotIn("entity", [target.entity_id for target in gate.blockers])
        with self.assertRaisesRegex(ValueError, "不存在"):
            skip_entity_development(self.workspace, entity_id="unknown", reason="无效目标")

    def test_entity_skip_requires_binding_execution_to_end(self) -> None:
        """运行或等待确认的实体绑定不能被跳过动作并发修改。"""

        self.technical["entities"] = [{"id": "entity"}]
        self.write_plans()
        start_workbench_execution(
            self.workspace, scope="data_source", target_id="entity", page_id=None,
            thread_id="entity-thread", run_id="entity-run", phase="entity_source_binding",
        )
        with self.assertRaisesRegex(ValueError, "先结束"):
            skip_entity_development(self.workspace, entity_id="entity", reason="不再需要")
        end_workbench_execution(self.workspace, run_id="entity-run")
        state = skip_entity_development(self.workspace, entity_id="entity", reason="不再需要")
        revision = state.revision
        self.assertEqual(skip_entity_development(self.workspace, entity_id="entity", reason="再次请求").revision, revision)

    def test_entity_confirmation_does_not_affect_test_entry(self) -> None:
        """实体绑定状态独立保留，页面和 Endpoint 完成后即可进入测试。"""

        self.technical["entities"] = [{"id": "entity"}]
        self.write_plans()
        for target in ("one", "two", "get"):
            self.finish(target)
        state = refresh_development_artifacts(self.workspace)
        self.assertEqual((read_test_entry_gate(state).completed, read_test_entry_gate(state).total), (3, 3))
        self.assertTrue(read_test_entry_gate(require_test_entry(self.workspace)).allowed)
        self.assertEqual(read_test_entry_gate(state).blockers, [])
        start_workbench_execution(
            self.workspace, scope="data_source", target_id="entity", page_id=None,
            thread_id="entity-thread", run_id="entity-run", phase="entity_source_binding",
        )
        state = update_workbench_execution(
            self.workspace, run_id="entity-run", phase="entity_source_binding",
            status=WorkbenchExecutionStatus.AWAITING_USER,
            pending_type=PendingInteractionType.ENTITY_SOURCE_BINDING,
        )
        self.assertEqual(state.development_artifacts.entities["entity"].initial_development_status, "in_progress")
        state = update_workbench_execution(
            self.workspace, run_id="entity-run", phase="entity_source_binding",
            status=WorkbenchExecutionStatus.FAILED,
        )
        self.assertEqual(state.development_artifacts.entities["entity"].initial_development_status, "pending")
        start_workbench_execution(
            self.workspace, scope="data_source", target_id="entity", page_id=None,
            thread_id="entity-thread", run_id="entity-retry", phase="entity_source_binding",
            replaces_run_id="entity-run",
        )
        self.assertEqual(refresh_development_artifacts(self.workspace).development_artifacts.entities["entity"].initial_development_status, "in_progress")
        directory = self.plans / "entities"
        directory.mkdir()
        path = directory / "entity--entity.json"
        path.write_text(json.dumps({"entity_id": "entity", "status": "pending_user_confirmation"}))
        self.assertTrue(read_test_entry_gate(refresh_development_artifacts(self.workspace)).allowed)
        path.write_text(json.dumps({"entity_id": "entity", "status": "confirmed"}))
        state = refresh_development_artifacts(self.workspace)
        self.assertEqual(state.development_artifacts.entities["entity"].initial_development_status, "completed")
        self.assertTrue(read_test_entry_gate(state).allowed)
        path.write_text("invalid json")
        self.assertTrue(read_test_entry_gate(refresh_development_artifacts(self.workspace)).allowed)
        self.technical["entities"] = []
        self.write_plans()
        state = refresh_development_artifacts(self.workspace)
        self.assertEqual(state.development_artifacts.entities, {})
        self.assertEqual(read_test_entry_gate(state).total, 3)

    def test_start_wait_fail_retry_and_complete(self) -> None:
        """状态跟随初次执行，等待确认保持紫色，失败灰色且重试可完成。"""

        run_id = self.start("one")
        state = update_workbench_execution(self.workspace, run_id=run_id, phase="unit_test",
            status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=PendingInteractionType.UNIT_TEST_CONFIRMATION)
        self.assertEqual(read_test_entry_gate(state).in_progress, 1)
        state = update_workbench_execution(self.workspace, run_id=run_id, phase="build", status=WorkbenchExecutionStatus.FAILED)
        self.assertEqual(read_test_entry_gate(state).pending, 3)
        retry = self.start("one", run_id="retry", replaces=run_id, initial=False)
        state = complete_initial_development(self.workspace, run_id=retry)
        self.assertEqual(state.development_artifacts.pages["one"].initial_development_status, "completed")
        self.assertEqual(state.active_executions[retry].development_purpose, "initial")

    def test_page_without_api_execution_evidence_does_not_complete_endpoint(self) -> None:
        """缺少同轮接口执行证据时仍独立计数，最后完成项先写盘再解锁。"""

        self.technical["entities"] = [{"id": "Order", "fields": [{"name": "number"}]}]
        self.technical["api_contracts"][0]["entity_ids"] = ["Order"]
        self.write_plans()
        original = (self.plans / "technical-plan.json").read_bytes()
        self.finish("one")
        self.finish("two")
        state = refresh_development_artifacts(self.workspace)
        self.assertEqual(state.development_artifacts.endpoints["api"]["get"].initial_development_status, "pending")
        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            require_test_entry(self.workspace)
        self.finish("get")
        self.assertTrue(read_test_entry_gate(require_test_entry(self.workspace)).allowed)
        state = start_workbench_execution(
            self.workspace, scope="application", target_id="test", page_id=None,
            thread_id="test-thread", run_id="test-run", phase="integration_test",
        )
        self.assertIn("test-run", state.active_executions)
        self.assertEqual(state.development_artifacts.entities["Order"].initial_development_status, "pending")
        self.assertEqual((self.plans / "technical-plan.json").read_bytes(), original)

    def test_endpoint_build_scope_cannot_unlock_test_at_two_of_three(self) -> None:
        """接口 Build 只覆盖自身时，另一个未完成页面仍阻止确认与测试启动。"""

        for target in ("one", "get"):
            self.finish(target)
        build_plan = self.plans / "build-task-plan.json"
        build_plan.write_text(json.dumps({"build_units": {
            "backend:endpoint:api:get": {"input_fingerprint": "current-endpoint"},
            "page:one": {}, "page:two": {},
        }}), encoding="utf-8")
        gate = read_test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertEqual((gate.completed, gate.total), (2, 3))
        self.assertFalse(gate.allowed)
        self.assertEqual([target.page_id for target in gate.blockers], ["two"])
        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            require_test_entry(self.workspace)
        state = {"workspace": str(self.workspace), "active_run_id": "run-get",
                 "build_execution_scope": {"type": "endpoint", "targetId": "get",
                                           "apiContractId": "api"},
                 "build_summary": {"status": "completed"}, "unit_test_gate_passed": True}
        confirmation = run_test_phase_confirmation(state)
        self.assertEqual(confirmation["status"], "completed")
        self.assertEqual(confirmation["clarification"], {})
        self.assertEqual(confirmation["integration_next_action"], "end")

    def test_redeveloped_changed_page_unlocks_test_confirmation(self) -> None:
        """本轮唯一的变更页面重新开发完成后，测试确认门必须放行而不是结束本轮。

        变更快照会把已完成但本轮改过的产物重新标为 changed；如果开发完成后
        这个标记没有被清除，门禁会永远把它算作缺口（例如 9/10），
        开发阶段就只能以"任务已完成"收尾，走不到测试阶段。
        """

        self.finish("one")
        self.write_change_snapshot(pages={"one": "modified", "two": "unchanged"})
        self.finish("two")
        self.finish("get")
        # 本轮只剩变更页面 one 待重做；接口 get 也已初次完成，不构成缺口。
        gate = read_test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertEqual([target.page_id for target in gate.blockers], ["one"])
        self.assertFalse(gate.allowed)

        state = {
            "workspace": str(self.workspace),
            "active_run_id": "run-one-redo",
            "active_thread_id": "thread-one-redo",
            "build_execution_scope": {"type": "page", "targetId": "one"},
            "build_summary": {"status": "completed", "failed": 0},
            "unit_test_gate_passed": True,
        }
        confirmation = run_test_phase_confirmation(state)
        self.assertEqual(confirmation["status"], "requires_user_input")
        self.assertEqual(confirmation["clarification"]["mode"], "test_phase_confirmation")
        # 重新读取服务端事实：变更标记必须真正落盘，而不是只在本次返回值里放行。
        gate = read_test_entry_gate(require_test_entry(self.workspace))
        self.assertTrue(gate.allowed)
        self.assertEqual((gate.completed, gate.total), (3, 3))

    def test_debug_restart_and_retry_project_active_target_without_granting_completion(self) -> None:
        """调试重启及失败重试均投影紫色，但不改变修订执行的首次完成资格。"""

        for target in ("one", "get"):
            with self.subTest(target=target):
                scope = (
                    {"type": "endpoint", "targetId": target, "apiContractId": "api"}
                    if target == "get" else {"type": "page", "targetId": target}
                )
                values = {"build_execution_scope": scope}
                inputs = {"workspace": str(self.workspace), "workflow_debug_enabled": True,
                          "resume_values": values}
                debug_run = f"debug-{target}"
                begin_workflow_lifecycle(inputs, thread_id=f"thread-{target}",
                    run_id=debug_run, phase="prepare_build_tasks")
                state = load_application_lifecycle(self.workspace)
                self.assertEqual(state.active_executions[debug_run].development_purpose, "revision")
                self.assertEqual(read_test_entry_gate(state).in_progress, 1)
                update_workbench_execution(self.workspace, run_id=debug_run,
                    phase="build", status=WorkbenchExecutionStatus.FAILED)
                self.assertEqual(read_test_entry_gate(load_application_lifecycle(self.workspace)).in_progress, 0)

                retry_run = f"retry-{target}"
                payload = begin_workflow_lifecycle({
                    "workspace": str(self.workspace), "workflow_action": "retry_failed_tasks",
                    "resume_values": {**values, "resume_execution_run_id": debug_run},
                }, thread_id=f"thread-{target}", run_id=retry_run, phase="build")
                artifacts = payload["developmentArtifacts"]
                progress = (artifacts["endpoints"]["api"]["get"]
                            if target == "get" else artifacts["pages"]["one"])
                self.assertEqual(progress["initialDevelopmentStatus"], "in_progress")
                other = (artifacts["pages"]["one"] if target == "get"
                         else artifacts["endpoints"]["api"]["get"])
                self.assertEqual(other["initialDevelopmentStatus"], "pending")
                self.assertEqual(payload["testEntryGate"]["inProgress"], 1)
                for status in (WorkbenchExecutionStatus.AWAITING_USER, WorkbenchExecutionStatus.STOPPING):
                    state = update_workbench_execution(self.workspace, run_id=retry_run,
                        phase="build", status=status)
                    self.assertEqual(read_test_entry_gate(state).in_progress, 1)
                state = complete_initial_development(self.workspace, run_id=retry_run)
                self.assertEqual(read_test_entry_gate(state).completed, 0)
                self.assertFalse(read_test_entry_gate(state).allowed)
                state = end_workbench_execution(self.workspace, run_id=retry_run)
                self.assertEqual(read_test_entry_gate(state).in_progress, 0)

    def test_build_and_unit_test_evidence_are_required(self) -> None:
        """真实节点仅在 Build 与单测门均通过后标绿，随后结束当前开发轮次。"""

        run_id = self.start("one")
        state = {"workspace": str(self.workspace), "active_run_id": run_id,
                 "build_execution_scope": {"type": "page", "targetId": "one"}}
        # 当前节点会回到缺失证据的原阶段，不能把“尚未完成”误认成已失败或已完成。
        result = run_test_phase_confirmation(state)
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["integration_next_action"], "build")
        state["build_summary"] = {"status": "completed"}
        result = run_test_phase_confirmation(state)
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["integration_next_action"], "unit_test")
        self.assertEqual(read_test_entry_gate(refresh_development_artifacts(self.workspace)).completed, 0)
        state["unit_test_gate_passed"] = True
        state["test_phase_confirmation"] = {"action": "confirm"}
        result = run_test_phase_confirmation(state)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"], {})
        self.assertEqual(result["integration_next_action"], "end")
        self.assertEqual(read_test_entry_gate(refresh_development_artifacts(self.workspace)).completed, 1)

    def test_revision_and_late_failure_cannot_reset_completion(self) -> None:
        """二次修改与旧运行失败不能覆盖首次完成证据。"""

        self.finish("one")
        before = refresh_development_artifacts(self.workspace).development_artifacts.pages["one"]
        revision = self.start("one", run_id="revision")
        state = update_workbench_execution(self.workspace, run_id=revision, phase="build", status=WorkbenchExecutionStatus.FAILED)
        self.assertEqual(state.active_executions[revision].development_purpose, "revision")
        state = update_workbench_execution(self.workspace, run_id="run-one", phase="build", status=WorkbenchExecutionStatus.FAILED)
        self.assertEqual(state.development_artifacts.pages["one"], before)
        complete_initial_development(self.workspace, run_id=revision)
        self.assertEqual(refresh_development_artifacts(self.workspace).development_artifacts.pages["one"], before)

    def test_noninitial_execution_cannot_mark_pending_target_complete(self) -> None:
        """普通执行及修订入口不会替未开发目标补记完成。"""

        run_id = self.start("one", initial=False)
        state = complete_initial_development(self.workspace, run_id=run_id)
        self.assertEqual(read_test_entry_gate(state).completed, 0)

    def test_parallel_writers_and_duplicate_completion(self) -> None:
        """并行完成不同目标不丢记录，重复完成不重复递增版本。"""

        runs = [self.start(target) for target in ("one", "two", "get")]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda run: complete_initial_development(self.workspace, run_id=run), runs))
        before = require_test_entry(self.workspace)
        after = complete_initial_development(self.workspace, run_id=runs[0])
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(read_test_entry_gate(after).completed, 3)

    def test_one_failed_parallel_execution_does_not_clear_other_progress(self) -> None:
        """同目标仍有初次执行时，另一个执行停止不把圆点改成灰色。"""

        first = self.start("one")
        second = self.start("one", run_id="second")
        state = end_workbench_execution(self.workspace, run_id=first)
        self.assertEqual(read_test_entry_gate(state).in_progress, 1)
        state = end_workbench_execution(self.workspace, run_id=second)
        self.assertEqual(read_test_entry_gate(state).in_progress, 0)

    def test_catalog_add_remove_rename_and_late_completion(self) -> None:
        """新 ID 待开发、同 ID 改名保留完成、删除目标不被迟到事件复活。"""

        self.finish("one")
        old_run = self.start("two")
        self.product["pages"] = [{"pageId": "one", "name": "新名称"}, {"pageId": "three"}]
        self.write_plans()
        state = complete_initial_development(self.workspace, run_id=old_run)
        self.assertEqual(set(state.development_artifacts.pages), {"one", "three"})
        self.assertEqual(read_test_entry_gate(state).completed, 1)

    def test_draft_corrupt_and_empty_catalog_fail_closed(self) -> None:
        """未确认草稿保留已完成事实，损坏目录和空目录不放行。"""

        self.finish("one")
        self.product["confirmation_status"] = "pending"
        self.product["pages"] = [{"pageId": "new"}]
        self.write_plans()
        state = refresh_development_artifacts(self.workspace)
        self.assertIn("one", state.development_artifacts.pages)
        self.assertNotIn("new", state.development_artifacts.pages)
        self.assertFalse(read_test_entry_gate(state).allowed)
        (self.plans / "product-plan.json").write_text("broken", encoding="utf-8")
        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            require_test_entry(self.workspace)
        self.product = {"confirmation_status": "confirmed", "pages": []}
        self.technical["api_contracts"] = []
        self.write_plans()
        gate = read_test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertEqual(gate.total, 0)
        self.assertFalse(gate.allowed)

    def test_blocked_confirmation_does_not_consume_interaction(self) -> None:
        """全量门禁拒绝后保留原执行及其待确认令牌。"""

        self.finish("one")
        state = update_workbench_execution(self.workspace, run_id="run-one", phase="test_phase_confirmation",
            status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=PendingInteractionType.TEST_PHASE_CONFIRMATION)
        pending = state.active_executions["run-one"].pending_interaction
        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            persist_workbench_interaction_submission(self.workspace, run_id="run-one",
                interaction_id=pending.id, based_on_revision=pending.based_on_revision)
        after = load_application_lifecycle(self.workspace)
        self.assertIsNone(after.active_executions["run-one"].pending_interaction.submitted_at)

    def test_direct_test_start_and_graph_entry_are_guarded(self) -> None:
        """直接指定集成测试节点不能登记执行或启动子图。"""

        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            begin_workflow_lifecycle({"workspace": str(self.workspace), "resume_values": {}},
                thread_id="test-thread", run_id="test-run", phase="integration_test")
        self.assertNotIn("test-run", load_application_lifecycle(self.workspace).active_executions)
        with patch("app.graph.subgraphs.testing._testing_subgraph.invoke") as invoke:
            with self.assertRaises(DevelopmentArtifactsIncompleteError):
                integration_test({"workspace": str(self.workspace)})
            invoke.assert_not_called()

    def test_write_failure_never_publishes_completion(self) -> None:
        """完成落盘失败时，重读仍未完成且不会返回允许测试的快照。"""

        run_id = self.start("one")
        with patch("app.services.application_lifecycle.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                complete_initial_development(self.workspace, run_id=run_id)
        self.assertEqual(read_test_entry_gate(load_application_lifecycle(self.workspace)).completed, 0)

    def test_test_handoff_write_failure_preserves_source_confirmation(self) -> None:
        """测试 execution 原子接替失败时，原确认令牌与原运行仍可重试。"""

        for target in ("one", "two", "get"):
            self.finish(target)
        state = update_workbench_execution(self.workspace, run_id="run-one", phase="test_phase_confirmation",
            status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=PendingInteractionType.TEST_PHASE_CONFIRMATION)
        pending = state.active_executions["run-one"].pending_interaction
        inputs = {"workspace": str(self.workspace), "resume_values": {
            "selectedPageId": "one", "build_execution_scope": {"type": "page", "targetId": "one"},
            "test_phase_confirmation": {"action": "confirm"},
            "resume_execution_run_id": "run-one",
            "lifecycle_interaction_submission": {
                "runId": "run-one", "id": pending.id, "basedOnRevision": pending.based_on_revision,
            },
        }}
        with patch("app.services.application_lifecycle.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                begin_workflow_lifecycle(inputs, thread_id="test-thread", run_id="test-run", phase="test_phase_confirmation")
        after = load_application_lifecycle(self.workspace)
        self.assertNotIn("test-run", after.active_executions)
        self.assertEqual(after.active_executions["run-one"].pending_interaction, pending)
        result = begin_workflow_lifecycle(inputs, thread_id="test-thread", run_id="test-run", phase="test_phase_confirmation")
        self.assertTrue(result["testEntryGate"]["allowed"])
        self.assertNotIn("run-one", result["activeExecutions"])

    def test_gate_rejection_emits_complete_ag_ui_lifecycle(self) -> None:
        """真实 AG-UI 运行被门禁拦截时，输出结构化错误、快照与正常终帧。"""

        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from app.graph.state import ProjectState
        from app.protocols.workflow import build_workflow_ag_ui_stream

        def guarded_node(state: ProjectState) -> dict:
            """在模型或测试命令执行前触发真实门禁。"""

            require_test_entry(state["workspace"])
            return {"status": "completed"}

        builder = StateGraph(ProjectState)
        builder.add_node("api_design_readiness_gate", guarded_node)
        builder.add_edge(START, "api_design_readiness_gate")
        builder.add_edge("api_design_readiness_gate", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        async def collect() -> list[dict]:
            """消费全部 AG-UI 帧，验证异常也能收口完整生命周期。"""

            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "threadId": "stream-thread", "runId": "stream-run",
                "messages": [{"role": "user", "content": "开发页面"}],
                "forwardedProps": {"workspaceRoot": str(self.workspace), "selectedPageId": "one"},
            })]
            return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

        with patch("app.protocols.workflow.request._project_plan_start_values", return_value={}):
            events = asyncio.run(collect())
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "RUN_STARTED")
        self.assertEqual(types[-1], "RUN_FINISHED")
        self.assertNotIn("RUN_ERROR", types)
        self.assertIn("STATE_SNAPSHOT", types)
        self.assertIn("TEXT_MESSAGE_CONTENT", types)
        final = events[-1]["result"]["workflow"]
        self.assertEqual(final["summary"]["errorCode"], "development_artifacts_incomplete")
        self.assertFalse(final["state"]["lifecycle"]["testEntryGate"]["allowed"])
        self.assertEqual(load_application_lifecycle(self.workspace).active_executions["stream-run"].status, "awaiting_user")

    def test_last_completion_reaches_ag_ui_before_user_test_confirmation(self) -> None:
        """真实节点与协议联动：最后一个目标标绿后等待确认，不自动开始测试。"""

        self._assert_development_completion_stream(all_completed=True)

    def test_partial_completion_finishes_run_and_releases_input(self) -> None:
        """真实 AG-UI 完成帧释放单目标执行和锁，后续新任务可正常登记。"""

        self._assert_development_completion_stream(all_completed=False)
        self.start("two")

    def _assert_development_completion_stream(self, *, all_completed: bool) -> None:
        """使用真实节点、路由和 Lifecycle 检查完成通知及最终确认两种边界。"""

        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from app.graph.state import ProjectState
        from app.graph.workflow import route_test_phase_confirmation
        from app.protocols.workflow import build_workflow_ag_ui_stream

        self.product["pages"] = [{"pageId": "one"}] if all_completed else [{"pageId": "one"}, {"pageId": "two"}]
        self.technical["api_contracts"] = []
        self.write_plans()

        def successful_development(state: ProjectState) -> dict:
            """以确定性测试节点提供服务端 Build 和单测完成证据。"""

            return {"phase": "build", "status": "completed", "build_summary": {"status": "completed"},
                    "unit_test_gate_passed": True}

        builder = StateGraph(ProjectState)
        builder.add_node("api_design_readiness_gate", successful_development)
        builder.add_node("test_phase_confirmation", run_test_phase_confirmation)
        builder.add_edge(START, "api_design_readiness_gate")
        builder.add_edge("api_design_readiness_gate", "test_phase_confirmation")
        builder.add_conditional_edges("test_phase_confirmation", route_test_phase_confirmation,
                                      {"completed": END, "await_user_input": END})
        graph = builder.compile(checkpointer=InMemorySaver())

        async def collect() -> list[dict]:
            """消费完成帧并检查当前 lifecycle，而不是客户端推断的状态。"""

            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "threadId": "complete-thread", "runId": "complete-run",
                "messages": [{"role": "user", "content": "开发页面"}],
                "forwardedProps": {"workspaceRoot": str(self.workspace), "selectedPageId": "one"},
            })]
            return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

        with patch("app.protocols.workflow.request._project_plan_start_values", return_value={}):
            events = asyncio.run(collect())
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        lifecycle_events = [event["value"] for event in events if event.get("name") == "application-lifecycle"]
        self.assertEqual(lifecycle_events[0]["developmentArtifacts"]["pages"]["one"]["initialDevelopmentStatus"], "in_progress")
        self.assertEqual(lifecycle_events[-1]["testEntryGate"]["allowed"], all_completed)
        workflow = events[-1]["result"]["workflow"]
        if all_completed:
            self.assertEqual(lifecycle_events[-1]["activeExecutions"]["complete-run"]["status"], "awaiting_user")
        else:
            self.assertEqual(lifecycle_events[-1]["activeExecutions"], {})
            self.assertFalse(any(lifecycle_events[-1]["resourceLocks"].values()))
            self.assertEqual(workflow["summary"]["status"], "completed")
            self.assertEqual(workflow["state"]["clarification"], {})
        self.assertEqual(events[-1]["result"]["workflow"]["state"]["lifecycle"]["testEntryGate"]["completed"], 1)


class RedevelopmentCompletionRecordTests(unittest.TestCase):
    """重新开发一个已完成目标时，本轮运行也要留下"最近一次完成"的痕迹。

    回归：此前重新开发只清除变更标记，`completedRunId/completedThreadId` 仍是首次
    开发那一次，前端因此认不出"本轮已完成"，不展示继续开发引导，用户看不到
    剩余待开发目标（例如另一个接口）的入口。
    """

    def setUp(self) -> None:
        """复用产物测试的最小工作区与计划。"""

        self.fixture = DevelopmentArtifactsTests(methodName="test_initial_catalog_is_gray")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.workspace = self.fixture.workspace

    def test_redevelopment_records_latest_run_without_touching_first_completion(self) -> None:
        """重新开发后：归属证据保持首次值，最近完成指向本轮运行。"""

        self.fixture.finish("one")
        first = refresh_development_artifacts(self.workspace).development_artifacts.pages["one"]

        start_workbench_execution(
            self.workspace, scope="page", target_id="one", page_id="one",
            thread_id="thread-one-redo", run_id="run-one-redo",
            phase="api_design_readiness_gate", initial_development_entry=True,
        )
        complete_initial_development(self.workspace, run_id="run-one-redo")
        redeveloped = refresh_development_artifacts(self.workspace).development_artifacts.pages["one"]

        # 首次完成证据不被覆盖：归属标签仍指向最初那一轮。
        self.assertEqual(redeveloped.completed_run_id, first.completed_run_id)
        self.assertEqual(redeveloped.completed_thread_id, first.completed_thread_id)
        self.assertEqual(redeveloped.completed_branch_name, first.completed_branch_name)
        # 最近完成指向本轮：前端据此识别"这次运行完成了该目标"。
        self.assertEqual(redeveloped.last_completed_run_id, "run-one-redo")
        self.assertEqual(redeveloped.last_completed_thread_id, "thread-one-redo")
