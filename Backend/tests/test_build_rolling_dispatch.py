"""验证真实 Build 调度循环的逐任务补位、锁和持久化边界。"""

from copy import deepcopy
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.graph.subgraphs.build import _restore_build_runtime, run_build_scheduler
from app.services.build_task_planner import replace_build_task_plan_tasks
from app.workspace.task_documents import load_build_execution_record
from tests.test_build_subgraph_scheduler import _ready_build_state


def task(name, dependencies=(), lock=None):
    """创建当前应用范围内具有明确写锁的任务。"""
    return {"id": name, "unit_id": "application:root", "owner": "backend",
            "status": "pending", "dependencies": list(dependencies),
            "lock_scope": [lock or name]}


def entry(workspace, tasks):
    """使用正式 DAG 夹具构造具备执行记录身份的入口。"""
    return _ready_build_state(workspace, {
        "workspace": workspace, "project_plan": {},
        "active_run_id": "rolling-run", "active_thread_id": "rolling-thread",
        "build_execution_scope": {"type": "application", "targetId": "application"},
        "build_task_plan": replace_build_task_plan_tasks({
            "schema_version": "build-dag.v4",
            "build_units": {"application:root": {"id": "application:root", "kind": "application"}},
            "unit_graph": {"nodes": ["application:root"], "edges": []},
        }, tasks),
    })


class RollingBuildTests(unittest.TestCase):
    """通过事件握手证明补位发生在其他任务结束之前。"""

    def test_repair_waits_for_independent_and_running_tasks(self):
        """修复规划读取所有独立任务收尾后的稳定状态。"""
        started = threading.Barrier(3)
        release = threading.Event()
        with tempfile.TemporaryDirectory() as workspace:
            state = entry(workspace, [task(name) for name in "ABCD"])

            def execute(snapshot, current, **kwargs):
                """D 必须在 B、C 结束前补位，A 的修复等待它们全部提交。"""
                name = current["id"]
                if name in {"A", "B", "C"}:
                    started.wait(5)
                if name in {"B", "C"}:
                    self.assertTrue(release.wait(5))
                if name == "D":
                    release.set()
                return [{"task_id": name, "owner": "backend",
                         "status": "failed" if name == "A" else "completed",
                         "failure_category": "compile_error" if name == "A" else None}], None

            def repair(**kwargs):
                """核验修复规划时已没有在途任务或未提交的独立结果。"""
                statuses = {item["id"]: item["status"] for item in kwargs["tasks"]}
                self.assertEqual(statuses, {"A": "failed", "B": "completed", "C": "completed", "D": "completed"})
                return {"decision": "terminal_failure", "tasks": []}

            with patch("app.graph.subgraphs.build._execute_task", side_effect=execute), patch(
                "app.graph.subgraphs.build.create_build_failure_repair_plan", side_effect=repair
            ) as planner:
                result = run_build_scheduler(state)
            self.assertEqual(result["status"], "failed")
            planner.assert_called_once()

    def test_cancel_drains_results_and_stops_new_work(self):
        """运行取消后保留在途结果，同时阻止补位和最终工程投影。"""
        started = threading.Barrier(3)
        release = threading.Event()
        cancelled = threading.Event()
        calls = []
        with tempfile.TemporaryDirectory() as workspace:
            state = entry(workspace, [task(name) for name in "ABCD"])

            def execute(snapshot, current, **kwargs):
                """用 A 的提交触发取消，B、C 随后正常退出。"""
                name = current["id"]
                calls.append(name)
                started.wait(5)
                if name != "A":
                    self.assertTrue(release.wait(5))
                return [{"task_id": name, "owner": "backend", "status": "completed"}], None

            def progress(event):
                """模拟控制面在首个任务结果提交后登记取消。"""
                if "scheduler:results:1" in event.get("state", {}).get("build_events", []):
                    cancelled.set()
                    release.set()

            with patch("app.graph.subgraphs.build._execute_task", side_effect=execute), patch(
                "app.services.build_dispatch.BuildDispatch.cancelled", side_effect=cancelled.is_set
            ), patch("app.graph.subgraphs.build._finalize_build") as finalize:
                result = run_build_scheduler(state, progress_writer=progress)
            self.assertCountEqual(calls, ["A", "B", "C"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["build_summary"]["completed"], 3)
            finalize.assert_not_called()

    def test_refills_dependency_after_commit_and_keeps_running_locks(self):
        """A 提交后立即启动 D，持有 B 锁的 E 必须等待 B 结束。"""
        started = threading.Barrier(3)
        release = threading.Event()
        calls = []
        active = set()
        mutex = threading.Lock()
        peak = 0
        saved_progress = []
        tasks = [task("A"), task("B", lock="shared"), task("C"),
                 task("D", ["A"]), task("E", ["A"], lock="shared")]
        with tempfile.TemporaryDirectory() as workspace:
            state = entry(workspace, tasks)

            def execute(snapshot, current, **kwargs):
                """用握手控制完成顺序，并从真实执行文件核验依赖已提交。"""
                nonlocal peak
                name = current["id"]
                with mutex:
                    calls.append(name)
                    active.add(name)
                    peak = max(peak, len(active))
                try:
                    if name in {"A", "B", "C"}:
                        started.wait(5)
                    if name in {"B", "C"}:
                        self.assertTrue(release.wait(5), "下游 D 未及时补位")
                    if name == "D":
                        progress = load_build_execution_record(state, "rolling-run")["progress"]
                        saved_progress.append(deepcopy(progress))
                        statuses = {item["id"]: item["status"] for item in progress["tasks"]}
                        self.assertEqual(statuses["A"], "completed")
                        self.assertEqual(statuses["B"], "running")
                        self.assertEqual(statuses["C"], "running")
                        self.assertNotIn("E", calls)
                        self.assertEqual(next(item for item in snapshot["tasks"] if item["id"] == "A")["status"], "completed")
                        release.set()
                    if name == "E":
                        with mutex:
                            self.assertNotIn("B", active)
                    return [{"task_id": name, "owner": "backend", "status": "completed"}], None
                finally:
                    with mutex:
                        active.remove(name)

            with patch("app.graph.subgraphs.build._execute_task", side_effect=execute), patch(
                "app.graph.subgraphs.build._finalize_build", return_value=({}, {}, None, [], None)
            ):
                result = run_build_scheduler(state)
            self.assertEqual(result["status"], "completed", result.get("build_results"))
            self.assertEqual(peak, 3)
            self.assertCountEqual(calls, ["A", "B", "C", "D", "E"])
            restored = _restore_build_runtime(state, "rolling-run", saved_progress[0], retry_failed_tasks=False)
            statuses = {item["id"]: item["status"] for item in restored["tasks"]}
            self.assertEqual(statuses["A"], "completed")
            self.assertEqual(statuses["B"], "pending")
            self.assertEqual(statuses["C"], "pending")

    def test_failure_refills_independent_task_and_blocks_dependant(self):
        """失败仅阻塞下游，独立任务在长任务仍运行时补位。"""
        started = threading.Barrier(3)
        release = threading.Event()
        calls = []
        with tempfile.TemporaryDirectory() as workspace:
            state = entry(workspace, [task("A"), task("B"), task("C"), task("D", ["A"]), task("E")])

            def execute(snapshot, current, **kwargs):
                """让 E 的启动解除 B、C 的等待。"""
                name = current["id"]
                calls.append(name)
                if name in {"A", "B", "C"}:
                    started.wait(5)
                if name in {"B", "C"}:
                    self.assertTrue(release.wait(5))
                if name == "E":
                    release.set()
                return [{"task_id": name, "owner": "backend",
                         "status": "failed" if name == "A" else "completed",
                         "failure_category": "network_error" if name == "A" else None}], None

            with patch("app.graph.subgraphs.build._execute_task", side_effect=execute):
                result = run_build_scheduler(state)
            self.assertEqual(result["status"], "failed")
            self.assertCountEqual(calls, ["A", "B", "C", "E"])
            self.assertEqual(result["build_summary"]["completed"], 3)

    def test_confirmation_drains_running_results_without_refilling(self):
        """确认失败后停止补位，已运行任务的成功仍逐个落盘。"""
        started = threading.Barrier(3)
        release = threading.Event()
        calls = []
        with tempfile.TemporaryDirectory() as workspace:
            state = entry(workspace, [task(name) for name in "ABCD"])

            def execute(snapshot, current, **kwargs):
                """A 请求确认，B、C 等待调度器确认已接收 A 的结果。"""
                name = current["id"]
                calls.append(name)
                started.wait(5)
                if name != "A":
                    self.assertTrue(release.wait(5))
                return [{"task_id": name, "owner": "backend",
                         "status": "failed" if name == "A" else "completed",
                         "failure_category": "workspace_snapshot_stale" if name == "A" else None}], None

            def progress(event):
                """结果提交事件到达后允许在途任务退出。"""
                if "scheduler:results:1" in event.get("state", {}).get("build_events", []):
                    release.set()

            with patch("app.graph.subgraphs.build._execute_task", side_effect=execute):
                result = run_build_scheduler(state, progress_writer=progress)
            self.assertEqual(result["status"], "requires_user_input")
            self.assertCountEqual(calls, ["A", "B", "C"])
            record = load_build_execution_record(state, "rolling-run")
            statuses = {item["id"]: item["status"] for item in record["progress"]["tasks"]}
            self.assertEqual(statuses, {"A": "failed", "B": "completed", "C": "completed", "D": "pending"})
