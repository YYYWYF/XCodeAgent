"""验证生成阶段裁剪旧职责后，真实 Pending 确认可以提升且不丢有效任务。"""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.services.build_task_plan_lifecycle import confirm_pending_build_task_plan
from app.services.build_task_planner import create_build_task_plan
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.planning_frozen import plain_json
from app.services.scope_assembly import assemble_scope_build_task_plan
from app.workspace.task_documents import (
    build_task_plan_sha256, load_confirmed_build_task_plan,
    load_pending_build_task_plan, write_json_atomic,
    write_pending_build_task_plan_atomic,
)
from tests.dag_planning_baseline_fixtures import (
    build_context, candidate_tasks, execution_scope, project_plan, task, workspace_snapshot,
)
from tests.dag_planning_orchestrator_fixtures import planning_inputs


class RetainedPlanConfirmationTests(unittest.TestCase):
    """使用真实 compiler、Assembly 和磁盘提升复现现场的 retained Task 冲突。"""

    def setUp(self):
        """构造已完成但当前没有物理连接职责的基础任务及仍有效的其他任务。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name}
        self.plan = project_plan()
        self.scope = execution_scope()
        self.context = build_context(self.plan, self.scope)
        backend_tasks = [
            task(f"orders:{stage}", "backend:module:orders-api", kind,
                 f"backend/src/main/java/example/{name}.java", "orders.list")
            for stage, kind, name in (
                ("foundation", "backend.foundation", "UpstreamClient"),
                ("business", "backend.application_service", "OrdersService"),
                ("controller", "backend.endpoint_controller", "OrdersController"),
            )
        ]
        self.baseline = create_build_task_plan(
            self.plan, agent_plan={"tasks": [*backend_tasks, *candidate_tasks(self.context)]},
            workspace_snapshot=workspace_snapshot(),
            base_build_task_plan=ensure_build_unit_skeleton(self.plan, workspace_snapshot()),
            build_context=self.context, build_execution_scope=self.scope,
        )
        self.assertEqual(self.baseline["status"], "ready")
        self.baseline["confirmation_status"] = "confirmed"
        for item in self.baseline["task_registry"].values():
            item["status"] = "already_satisfied"
        self.inputs = planning_inputs(
            plan=self.plan, baseline=self.baseline, scope=self.scope,
            context=self.context, required=self.context["required_unit_ids"],
        )
        self.result = assemble_scope_build_task_plan(
            base_confirmed_plan=self.baseline, skeleton_plan=self.inputs.skeleton_plan,
            project_plan=self.plan, product_plan={"pages": []}, build_context=self.context,
            build_execution_scope=self.scope, reuse_facts=self.inputs.reuse_facts,
            generation_requirements_by_unit={}, candidates_by_unit={},
        )
        self.formal_path = Path(directory.name) / ".devagentstudio/plans/build-task-plan.json"
        write_json_atomic(self.formal_path, self.baseline)

    def confirm(self, draft):
        """从真实 writer 签发草稿身份，再通过公共确认服务复核并提升。"""
        write_pending_build_task_plan_atomic(
            self.state, draft, owner_session_id="session-owner", planning_run_id="planning-test",
            workflow_run_id="workflow-test",
            base_confirmed_plan_digest=build_task_plan_sha256(self.baseline),
            input_fingerprint=self.inputs.input_fingerprint(), build_execution_scope=self.scope,
            created_at="2026-11-10T00:00:00Z",
            planning_provenance={
                "schema_version": "planning-provenance.v2",
                "review_task_ids": [key for key in self.result.review_task_ids if key in draft["task_registry"]],
                "platform_task_ids": [], "new_task_ids": [],
                "reused_task_ids": [key for key in self.result.reused_task_ids if key in draft["task_registry"]],
            },
        )
        identity = load_pending_build_task_plan(self.state)["draft_identity"]
        return confirm_pending_build_task_plan(
            self.state, planning_run_id=identity["planning_run_id"],
            draft_digest=identity["draft_digest"], current_inputs=self.inputs,
        )

    def test_generated_pruned_foundation_can_be_confirmed(self):
        """生成不再需要的基础任务被裁剪后，确认不得再次要求旧全集。"""
        draft = plain_json(self.result.assembled_plan)
        self.assertEqual(draft["status"], "ready")
        self.assertNotIn("orders:foundation", draft["task_registry"])
        before = deepcopy(self.baseline)
        result = self.confirm(draft)
        self.assertEqual((result.status, result.errors), ("confirmed", ()))
        self.assertIsNone(load_pending_build_task_plan(self.state))
        confirmed = load_confirmed_build_task_plan(self.state["workspace"])
        self.assertEqual(set(confirmed["task_registry"]), set(draft["task_registry"]))
        self.assertEqual(self.baseline, before)

    def test_valid_retained_task_cannot_be_deleted(self):
        """即使草稿经过服务端重签，也必须拒绝删除仍有效的旧任务并保留原文件。"""
        draft = plain_json(self.result.assembled_plan)
        draft["task_registry"].pop("orders:api")
        before = self.formal_path.read_bytes()
        result = self.confirm(draft)
        self.assertEqual(result.status, "invalid_dag")
        self.assertIn("retained Task", "；".join(result.errors))
        self.assertEqual(self.formal_path.read_bytes(), before)
        self.assertIsNotNone(load_pending_build_task_plan(self.state))
