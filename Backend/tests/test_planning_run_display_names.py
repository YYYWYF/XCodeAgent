"""本轮名称冻结、轻量持久化和阶段文案的边界回归。"""

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from pydantic import ValidationError

from app.domain.application_lifecycle import WorkbenchExecution
from app.services import planning_run as transitions
from app.services.build_task_progress import PlanningRunProgressPublisher, _planning_run_progress_message
from app.services.planning_frozen import plain_json
from app.services.planning_run_contracts import PlanningRunProjection
from app.services.planning_run_progress import project_planning_run_progress
from app.services.workbench_progress_projection import project_workbench_progress
from app.workspace.planning_run_documents import load_planning_run, project_planning_run, write_planning_run_atomic
from tests.dag_planning_orchestrator_fixtures import planning_inputs
from tests.planning_run_fixtures import AT, UNIT, issue, phases, ready, repair_decision, run


class PlanningRunDisplayNamesTests(unittest.TestCase):
    """展示元数据跟随本轮输入，不扩大 Scope 或加入权威判断。"""

    def test_create_run_freezes_names_from_same_inputs_and_filters_scope(self):
        """源计划修改不影响已冻结输入，名称仅覆盖本轮 Unit。"""

        plan = plain_json(planning_inputs().project_plan)
        plan["pages"][0]["name"] = "订单列表"
        inputs = planning_inputs(plan=plan, required=["page:a"])
        fingerprint = inputs.input_fingerprint()
        plan["pages"][0]["name"] = "过期外部修改"
        state = inputs.create_run(inputs.requirements(), planning_run_id="new", workflow_run_id="workflow", thread_id="thread", at=AT)
        self.assertEqual(state.unit_display_names["page:a"], "订单列表页面")
        self.assertNotIn("page:history", state.unit_display_names)
        self.assertTrue(set(state.unit_display_names).issubset(state.required_unit_ids))
        self.assertEqual(state.input_fingerprint, fingerprint)
        renamed = state.model_copy(update={"unit_display_names": {"page:a": "只改展示"}})
        self.assertEqual(renamed.input_fingerprint, fingerprint)
        self.assertNotIn("unit_display_names", inputs.model_dump())

    def test_names_are_strict_frozen_metadata_in_current_projection(self):
        """拒绝漏字段、非法类型、空名及跨 Scope 名称，导出副本不修改 Run。"""

        state = run().model_copy(update={"unit_display_names": {UNIT: "订单列表页面"}})
        with self.assertRaises(TypeError):
            state.unit_display_names[UNIT] = "changed"
        payload = project_planning_run(state)
        payload["unit_display_names"][UNIT] = "changed"
        self.assertEqual(state.unit_display_names[UNIT], "订单列表页面")
        del payload["unit_display_names"]
        with self.assertRaises(ValidationError):
            PlanningRunProjection.model_validate(payload)
        for names in ({"page:other": "其他页面"}, {UNIT: " "}, {UNIT: 1}):
            with self.subTest(names=names), self.assertRaises(ValidationError):
                state.model_copy(update={"unit_display_names": names})

    def test_transitions_keep_names_through_repair_persistence_and_terminal(self):
        """修复重开只改变生成状态，名称持续跟随本轮 Run。"""

        state = phases()["global_check"].model_copy(update={"unit_display_names": {UNIT: "订单列表页面"}})
        repaired = transitions.begin_global_repair(state, repair_decision(issue(UNIT, level="global")), at=AT)
        checking = transitions.begin_global_check(ready(repaired), at=AT)
        assembled = transitions.begin_assembly(checking, at=AT)
        validated = transitions.begin_validation(assembled, at=AT)
        persisting = transitions.begin_pending_persistence(validated, at=AT)
        for snapshot in (repaired, checking, assembled, validated, persisting,
                         transitions.fail(repaired, issue(retryable=False), at=AT), transitions.cancel(repaired, at=AT)):
            self.assertEqual(dict(snapshot.unit_display_names), {UNIT: "订单列表页面"})

    def test_repair_history_does_not_override_current_phase_message(self):
        """全局修复之后汇总、校验和保存均显示实际阶段。"""

        expected = {"preparing": "正在准备开发任务。", "assembling": "正在汇总开发任务。",
                    "validating": "正在校验执行计划。", "persisting_pending": "正在保存待确认计划。"}
        for phase, message in expected.items():
            snapshot = project_planning_run_progress(phases()[phase])
            snapshot["globalRepairRound"] = 1
            with self.subTest(phase=phase):
                self.assertEqual(_planning_run_progress_message(snapshot), message)


class PlanningRunDisplayPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """同一 Run 的发布、磁盘读取和重连均投影同一份名称。"""

    async def test_live_and_reconnect_use_same_persisted_names(self):
        """名称随轻量投影落盘，重连不读取后来变更的 TechnicalPlan。"""

        state = run().model_copy(update={"unit_display_names": {UNIT: "原订单列表页面"}})
        events = []
        publisher = PlanningRunProgressPublisher(events.append)
        with TemporaryDirectory() as directory:
            workspace = {"workspace": directory}
            write_planning_run_atomic(workspace, state)
            await publisher(project_planning_run(state))
            saved = load_planning_run(workspace)
            self.assertNotIn("candidates", saved)
            execution = WorkbenchExecution(scope="page", targetId="orders", runId=state.workflow_run_id,
                threadId=state.thread_id, ownerSessionId="session", phase="prepare_build_tasks",
                status="running", startedAt=AT, updatedAt=AT)
            with patch("app.workspace.plan_documents.load_project_plan_json", side_effect=AssertionError("禁止重读当前计划")):
                reconnect = project_workbench_progress(directory, SimpleNamespace(active_executions={execution.run_id: execution}))
        self.assertEqual(reconnect[execution.run_id]["dagGeneration"], events[0]["dag_generation"])
