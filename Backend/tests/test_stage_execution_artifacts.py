"""阶段入口只复用同范围真实终态，单测通过不能跨源码或计划漂移复用。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.stage_execution_artifacts import bind_unit_test_report, load_stage_execution_artifacts
from app.workspace.task_documents import (
    build_task_plan_sha256, build_run_task_plan_json_path,
    write_build_execution_record, write_build_run_task_plan_json,
)


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

    def _write_referenced_build(self):
        """使用生产写入器保存超阈值计划与进度，避免纯内联夹具遗漏引用读取问题。"""
        task = {"id": "task", "status": "completed", "build_context": {"text": "业务材料" * 1500}}
        self.plan["task_registry"] = {"task": task}
        path = write_build_run_task_plan_json(
            self.state, build_run_id=self.record["build_run_id"], build_task_plan=self.plan,
        )
        self.record.update(build_run_plan_path=path, build_run_plan_sha256=build_task_plan_sha256(self.plan))
        self.record["progress"].update({
            key: self.record[key]
            for key in ("build_run_id", "build_run_plan_path", "build_run_plan_sha256")
        })
        self.record["progress"]["tasks"] = [task]
        write_build_execution_record(self.state, self.record)
        raw = json.loads(Path(path).read_text())
        reference = raw["task_registry"]["task"]["build_context"]["$artifact"]
        return self.root / ".devagentstudio/artifacts/sha256" / (reference["artifactId"] + ".json")

    def test_referenced_plan_and_progress_retain_completed_build(self):
        """三个下游入口均按展开后的计划校验，并恢复完整任务材料及原 Build 绑定。"""
        self._write_referenced_build()
        for node in ("unit_test", "unit_test_repair", "test_phase_confirmation"):
            with self.subTest(node=node):
                fields = load_stage_execution_artifacts(self.state, self.plan, node)
                self.assertEqual(fields["build_summary"], {"status": "completed"})
                self.assertEqual(fields["build_run_id"], self.record["build_run_id"])
                self.assertEqual(fields["tasks"], self.record["progress"]["tasks"])

    def test_skip_unit_tests_with_references_reaches_test_confirmation(self):
        """真实跳过分支写报告后仍保留磁盘 Build 事实，不回退构建且继续等待测试确认。"""
        from app.domain.development_artifacts import TestEntryGate
        from app.graph.nodes.lifecycle import test_phase_confirmation
        from app.graph.subgraphs.unit_testing import unit_test
        from app.graph.workflow import route_test_phase_confirmation
        from app.services.application_lifecycle import create_application_lifecycle

        self._write_referenced_build()
        state = {**self.state, "unit_test_decision": "skip"}
        state.update(load_stage_execution_artifacts(state, self.plan, "unit_test"))
        state.update(unit_test(state))
        self.assertTrue(state["unit_test_gate_passed"])
        state.update(load_stage_execution_artifacts(state, self.plan, "test_phase_confirmation"))
        self.assertEqual(state["build_run_id"], self.record["build_run_id"])
        self.assertTrue(state["unit_test_gate_passed"])
        # 这里只隔离目标完成登记；跳过、报告写读及确认路由均运行真实实现。
        with patch("app.graph.nodes.lifecycle.complete_initial_development", return_value=
                   create_application_lifecycle(application_id="test", application_name="测试")), patch(
            "app.graph.nodes.lifecycle.test_entry_gate", return_value=TestEntryGate(
                allowed=True, total=1, completed=1, pending=0, inProgress=0, blockers=[],
            ),
        ):
            result = test_phase_confirmation(state)
        self.assertEqual(result["clarification"]["mode"], "test_phase_confirmation")
        self.assertEqual(route_test_phase_confirmation({**state, **result}), "await_user_input")

    def test_missing_or_corrupt_material_invalidates_completed_build(self):
        """缺失或摘要不符的引用仍失效，不能借用旧完成事实或修改损坏文件。"""
        material = self._write_referenced_build()
        original = material.read_bytes()
        material.rename(material.with_suffix(".test-backup"))
        for content in (None, b"{}"):
            with self.subTest(content=content):
                if content is not None:
                    material.write_bytes(content)
                fields = load_stage_execution_artifacts(self.state, self.plan, "unit_test")
                self.assertEqual(fields["build_summary"], {})
                self.assertEqual(fields["build_run_id"], "")
                if content is not None:
                    self.assertEqual(material.read_bytes(), content)
        material.write_bytes(original)
        self.assertEqual(load_stage_execution_artifacts(self.state, self.plan, "unit_test")["build_summary"],
                         {"status": "completed"})

    def test_repair_reads_latest_failure_report_without_granting_confirmation(self):
        """修复入口读取最新失败报告并保留返修证据，不能投影为单测通过。"""
        report = bind_unit_test_report(self.state, {"passed": False, "revision_requests": [{"reason": "编译失败"}]})
        path = self.root / ".devagentstudio/reports/unit-test-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report))
        fields = load_stage_execution_artifacts({**self.state, "unit_test_report": {"stale": True}}, self.plan, "unit_test_repair")
        self.assertEqual(fields["unit_test_report"], report)
        self.assertFalse(fields["unit_test_gate_passed"])
        self.assertFalse(fields["unit_test_quality_gate_passed"])

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
