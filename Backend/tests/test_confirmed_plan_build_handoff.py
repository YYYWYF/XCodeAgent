"""验证确认新计划后首次 Build 直接绑定当前计划，不依赖失败重试脱困。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.graph.nodes.task_planning_adapter import _project_confirm_result
from app.graph.subgraphs.build import _bound_build_task_plan_for_build
from app.graph.nodes.code_review import acceptance_phase_confirmation
from app.services.build_task_plan_lifecycle import ConfirmPromotionResult
from app.services.build_restart import confirmed_plan_build_handoff
from app.workspace.task_documents import build_task_plan_sha256
from tests.dag_planning_baseline_fixtures import confirmed_baseline, execution_scope, project_plan


class ConfirmedPlanBuildHandoffTests(unittest.TestCase):
    """覆盖真实确认投影与 Build 首次绑定，以及同计划恢复进度保留。"""

    def test_new_confirmed_plan_replaces_old_binding_before_first_build(self):
        """模拟同线程旧 State 合并新确认结果，首次绑定必须成功且不删除既有文件。"""
        with tempfile.TemporaryDirectory() as directory:
            scope = execution_scope(name="orders")
            formal = project_plan()
            plan = confirmed_baseline(formal, scope)
            source = Path(directory) / "existing.ts"
            source.write_text("existing completed code")
            state = {"workspace": directory, "active_thread_id": "thread", "active_run_id": "new-run",
                     "build_execution_scope": scope, "build_run_id": "old-build",
                     "build_run_plan_path": "/old/snapshot.json", "build_run_plan_sha256": "old-digest",
                     "build_results": [{"task_id": "old", "status": "completed"}],
                     "build_summary": {"status": "completed"}, "retry_source_run_id": "old-retry"}
            state["acceptance_phase_confirmation"] = {"action": "confirm"}
            context = SimpleNamespace(project_plan=formal, scope=scope, build_context={},
                                      formal_plan_path=str(Path(directory) / "plan.json"), formal_state={})
            for status in ("confirmed", "already_confirmed"):
                with self.subTest(status=status):
                    update = _project_confirm_result(ConfirmPromotionResult(status=status, confirmed_plan=plan),
                                                     state=state, context=context)
                    merged = {**state, **update}
                    self.assertEqual(merged["retry_source_run_id"], "")
                    self.assertEqual(merged["build_results"], [])
                    self.assertEqual(merged["acceptance_phase_confirmation"], {})
                    with patch("app.graph.subgraphs.build._latest_build_task_plan_for_build", return_value=(plan, [])):
                        bound, binding, errors = _bound_build_task_plan_for_build(merged)
                    self.assertEqual(errors, [])
                    self.assertEqual(bound, plan)
                    self.assertNotEqual(binding["build_run_id"], "old-build")
                    self.assertEqual(binding["build_run_plan_sha256"], build_task_plan_sha256(plan))
                    self.assertEqual(source.read_text(), "existing completed code")

    def test_same_plan_repeat_confirmation_preserves_binding_and_progress(self):
        """同计划恢复不清空既有任务进度或重试来源。"""
        plan = {"current": "plan"}
        state = {"build_run_id": "current", "build_run_plan_path": "/snapshot.json",
                 "build_run_plan_sha256": build_task_plan_sha256(plan),
                 "build_results": [{"status": "completed"}], "retry_source_run_id": "source"}
        self.assertEqual(confirmed_plan_build_handoff(state, plan, already_confirmed=True), {})
        # 即便新草稿内容相同，首次确认也必须启动本轮执行，而不是继承上一轮绑定。
        self.assertEqual(confirmed_plan_build_handoff(state, plan)["build_run_id"], "")

    def test_partial_binding_is_cleared(self):
        """残缺绑定不能因摘要相同残留到下一次 Build。"""
        plan = {"current": "plan"}
        self.assertEqual(confirmed_plan_build_handoff({"build_run_plan_sha256": build_task_plan_sha256(plan)}, plan)["build_run_plan_sha256"], "")

    def test_new_plan_cannot_reuse_previous_acceptance_entry_confirmation(self):
        """新计划完成审查后仍经原确认门等待用户，不复用旧点击自动进入验收。"""
        state = {"acceptance_phase_confirmation": {"action": "confirm"},
                 "code_review_result": {"status": "completed"}}
        state.update(confirmed_plan_build_handoff(state, {"new": "plan"}))
        result = acceptance_phase_confirmation(state)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["acceptance_next_action"], "await_user_input")
