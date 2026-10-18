"""阶段入口只复用同范围真实终态，单测通过不能跨源码或计划漂移复用。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.stage_execution_artifacts import bind_unit_test_report, load_stage_execution_artifacts
from app.workspace.task_documents import build_task_plan_sha256, build_run_task_plan_json_path, write_build_execution_record


class StageExecutionArtifactTests(unittest.TestCase):
    def test_missing_upstream_facts_reexecutes_business_node_instead_of_retry_trap(self):
        """缺少上游通过事实时去执行真实 Build/单测，不反复失败在同一确认节点。"""
        from app.graph.nodes.lifecycle import test_phase_confirmation
        from app.graph.workflow import route_test_phase_confirmation
        build = test_phase_confirmation({})
        self.assertEqual(route_test_phase_confirmation(build), "build")
        unit = test_phase_confirmation({"build_summary": {"status": "completed"}})
        self.assertEqual(route_test_phase_confirmation(unit), "unit_test")
        from app.graph.nodes.code_review import review_phase_confirmation, acceptance_phase_confirmation
        from app.graph.workflow import route_review_phase_confirmation, route_acceptance_phase_confirmation
        self.assertEqual(route_review_phase_confirmation(review_phase_confirmation({})), "integration_test")
        self.assertEqual(route_acceptance_phase_confirmation(acceptance_phase_confirmation({})), "code_review")

    def setUp(self):
        """构造已完成收尾的磁盘 Build 与真实源码文件。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.scope = {"type": "page", "targetId": "page"}
        self.plan = {"task_registry": {}, "build_units": {}, "build_execution_scope": self.scope}
        self.state = {"workspace": directory.name, "active_thread_id": "thread", "build_execution_scope": self.scope, "build_task_plan": self.plan,
                      "unit_test_generation_context": {"source_files": ["frontend/src/Page.tsx"]}}
        source = self.root / "frontend/src/Page.tsx"
        source.parent.mkdir(parents=True)
        source.write_text("export const Page = () => <div>current</div>")
        build_id = "build-" + "a" * 32
        bound = build_run_task_plan_json_path(self.state, build_id)
        bound.parent.mkdir(parents=True)
        bound.write_text(json.dumps(self.plan))
        self.record = {"execution_run_id": "source", "thread_id": "thread", "build_execution_scope": self.scope, "execution_stage": "finalized",
                       "build_run_id": build_id, "build_run_plan_path": str(bound), "build_run_plan_sha256": build_task_plan_sha256(self.plan),
                       "progress": {"build_summary": {"status": "completed"}, "tasks": [], "build_results": []}}
        write_build_execution_record(self.state, self.record)

    def test_fresh_stage_loads_finalized_build_and_bound_unit_report(self):
        """新节点无需旧 State 即可读取已完成 Build 和当前文件绑定的单测结果。"""
        report = bind_unit_test_report(self.state, {"passed": True})
        report_path = self.root / ".devagentstudio/reports/unit-test-report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report))
        fields = load_stage_execution_artifacts(self.state, self.plan, "test_phase_confirmation")
        self.assertEqual(fields["build_summary"]["status"], "completed")
        self.assertTrue(fields["unit_test_gate_passed"])
        (self.root / "frontend/src/Page.tsx").write_text("changed source")
        self.assertFalse(load_stage_execution_artifacts(self.state, self.plan, "test_phase_confirmation")["unit_test_gate_passed"])

    def test_unfinished_newer_build_invalidates_old_success(self):
        """最新记录未完成收尾时不借用旧成功，也不把批次完成当作 Build 完成。"""
        write_build_execution_record(self.state, {**self.record, "execution_run_id": "newer", "execution_stage": "batch_committed"})
        self.assertEqual(load_stage_execution_artifacts(self.state, self.plan, "unit_test")["build_summary"], {})

    def test_other_thread_scope_and_plan_are_not_borrowed(self):
        """其他会话、范围和正式任务合同的结果不能授权当前阶段。"""
        self.assertEqual(load_stage_execution_artifacts({**self.state, "active_thread_id": "other"}, self.plan, "unit_test")["build_summary"], {})
        self.assertEqual(load_stage_execution_artifacts({**self.state, "build_execution_scope": {"type": "application"}}, self.plan, "unit_test")["build_summary"], {})
        self.assertEqual(load_stage_execution_artifacts(self.state, {**self.plan, "changed": True}, "unit_test")["build_summary"], {})

    def test_bad_bound_snapshot_clears_old_state_and_reenters_build(self):
        """绑定快照缺失或损坏不阻断节点，也不能沿用 checkpoint 里的旧 Build 通过状态。"""
        from app.graph.nodes.lifecycle import test_phase_confirmation
        from app.graph.workflow import route_test_phase_confirmation
        bound = build_run_task_plan_json_path(self.state, self.record["build_run_id"])
        bound.rename(bound.with_suffix(".test-backup"))
        for content in (None, b"{", b"[]", b"\xff"):
            if content is not None:
                bound.write_bytes(content)
            for _ in range(2):
                fields = load_stage_execution_artifacts(self.state, self.plan, "test_phase_confirmation")
                stale = {**self.state, "build_summary": {"status": "completed"},
                    "unit_test_gate_passed": True, "unit_test_quality_gate_passed": True, **fields}
                self.assertEqual(route_test_phase_confirmation(test_phase_confirmation(stale)), "build")
            if content is not None:
                self.assertEqual(bound.read_bytes(), content)

    def test_bad_report_retains_valid_build_and_reenters_unit_test(self):
        """坏报告不伪造通过，也不阻断重新单测；有效 Build 无需重复执行。"""
        from app.graph.nodes.lifecycle import test_phase_confirmation
        from app.graph.workflow import route_test_phase_confirmation
        path = self.root / ".devagentstudio/reports/unit-test-report.json"
        path.parent.mkdir(parents=True)
        for content in ("{", "[]", '{"artifact_context":[1]}', '{"artifact_context":{"files":[1]}}'):
            path.write_text(content)
            for _ in range(2):
                fields = load_stage_execution_artifacts(self.state, self.plan, "test_phase_confirmation")
                stale = {**self.state, "unit_test_gate_passed": True,
                    "unit_test_quality_gate_passed": True, **fields}
                self.assertEqual(route_test_phase_confirmation(test_phase_confirmation(stale)), "unit_test")
            self.assertEqual(path.read_text(), content)

    def test_bad_latest_record_does_not_borrow_older_success(self):
        """最新提交损坏时失效缓存，不能从更旧记录找一个成功冒充当前 Build。"""
        import os
        directory = self.root / ".devagentstudio/plans/build-runs"
        latest = directory / "execution-damaged.json"
        latest.write_text("{")
        os.utime(latest, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
        fields = load_stage_execution_artifacts(self.state, self.plan, "unit_test")
        self.assertEqual(fields["build_summary"], {})
