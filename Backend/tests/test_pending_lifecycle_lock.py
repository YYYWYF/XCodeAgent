"""T5.1 Pending 生命周期锁的工作区作用域和锁顺序回归测试。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    create_application_lifecycle,
    start_workbench_execution,
    write_application_lifecycle,
)
from app.services.build_task_plan_lifecycle import abandon_pending_build_task_plan
from app.workspace.task_documents import (
    build_task_plan_lifecycle_lock,
    load_pending_build_task_plan,
    write_pending_build_task_plan_atomic,
)


class _RecordingLock:
    """用可重入底层锁记录每个线程实际取得锁的先后顺序。"""

    def __init__(self, name: str, orders: dict[int, list[str]], guard: threading.Lock) -> None:
        """初始化带线程顺序记录的测试锁代理。"""

        self._name = name
        self._orders = orders
        self._guard = guard
        self._lock = threading.RLock()

    def __enter__(self) -> "_RecordingLock":
        """取得底层锁并记录当前线程的逻辑锁名称。"""

        self._lock.acquire()
        thread_id = threading.get_ident()
        with self._guard:
            self._orders.setdefault(thread_id, []).append(self._name)
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """释放底层可重入锁。"""

        self._lock.release()


def _write_ready_lifecycle(workspace: str) -> None:
    """为 admission 并发测试写入最小可启动 lifecycle。"""

    state = create_application_lifecycle(application_id="app-t5-1", application_name="T5.1")
    state = state.model_copy(
        update={
            "initialization": state.initialization.model_copy(
                update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }
            )
        }
    )
    write_application_lifecycle(workspace, state)


def _write_pending(workspace: str) -> dict[str, str]:
    """为 admission 与 Abandon 竞争测试写入合法的最小 PendingPlan。"""

    write_pending_build_task_plan_atomic(
        {"workspace": workspace},
        {
            "schema_version": "build-dag.v4",
            "status": "ready",
            "task_graph": {"validation": {"is_valid": True, "errors": []}},
        },
        owner_session_id="session-pending-owner",
        planning_run_id="planning-t5-1",
        workflow_run_id="workflow-t5-1",
        base_confirmed_plan_digest=None,
        input_fingerprint="a" * 64,
        build_execution_scope={"type": "application", "targetId": "application"},
        created_at="2026-09-15T00:00:00Z",
        planning_provenance={
            "schema_version": "planning-provenance.v2",
            "review_task_ids": [],
            "new_task_ids": [],
            "reused_task_ids": [],
        },
    )
    pending = load_pending_build_task_plan({"workspace": workspace})
    assert pending is not None
    identity = pending["draft_identity"]
    return {
        "planning_run_id": identity["planning_run_id"],
        "draft_digest": identity["draft_digest"],
    }


class PendingLifecycleLockTests(unittest.TestCase):
    """验证 Pending lifecycle 锁的作用域、互斥和获取顺序。"""

    def test_same_workspace_confirm_and_recovery_share_one_lock(self) -> None:
        """同一工作区的两个线程必须共享同一把锁并严格串行。"""

        with tempfile.TemporaryDirectory() as workspace:
            lock = build_task_plan_lifecycle_lock(workspace)
            alias_lock = build_task_plan_lifecycle_lock(Path(workspace) / ".")
            self.assertIs(lock, alias_lock)

            first_entered = threading.Event()
            second_started = threading.Event()
            second_entered = threading.Event()
            release_first = threading.Event()
            errors: list[BaseException] = []

            def hold_first() -> None:
                """模拟同一工作区的 Confirm 持有 Pending 生命周期锁。"""

                try:
                    with lock:
                        first_entered.set()
                        if not release_first.wait(timeout=3):
                            raise TimeoutError("Confirm 测试线程未收到释放信号。")
                except BaseException as exc:  # pragma: no cover - 只在测试自身超时才触发
                    errors.append(exc)

            def wait_second() -> None:
                """模拟同一工作区 recovery/admission 等待 Confirm 完成。"""

                try:
                    second_started.set()
                    with alias_lock:
                        second_entered.set()
                except BaseException as exc:  # pragma: no cover - 只在测试自身失败才触发
                    errors.append(exc)

            first = threading.Thread(target=hold_first, daemon=True)
            second = threading.Thread(target=wait_second, daemon=True)
            first.start()
            self.assertTrue(first_entered.wait(timeout=3))
            second.start()
            self.assertTrue(second_started.wait(timeout=3))
            self.assertFalse(second_entered.is_set())

            release_first.set()
            first.join(timeout=3)
            second.join(timeout=3)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertTrue(second_entered.is_set())
            self.assertEqual(errors, [])

    def test_different_workspaces_do_not_block_each_other(self) -> None:
        """一个工作区持锁时，另一个工作区的 Pending 操作仍必须进入临界区。"""

        with tempfile.TemporaryDirectory() as workspace_a, tempfile.TemporaryDirectory() as workspace_b:
            lock_a = build_task_plan_lifecycle_lock(workspace_a)
            lock_b = build_task_plan_lifecycle_lock(workspace_b)
            entered_a = threading.Event()
            entered_b = threading.Event()
            release_a = threading.Event()
            errors: list[BaseException] = []

            def hold_workspace_a() -> None:
                """模拟 Workspace A 长时间持有 Pending lifecycle lock。"""

                try:
                    with lock_a:
                        entered_a.set()
                        if not release_a.wait(timeout=3):
                            raise TimeoutError("Workspace A 测试线程未收到释放信号。")
                except BaseException as exc:  # pragma: no cover - 只在测试自身超时才触发
                    errors.append(exc)

            def operate_workspace_b() -> None:
                """模拟 Workspace B 的独立 Pending 操作。"""

                try:
                    with lock_b:
                        entered_b.set()
                except BaseException as exc:  # pragma: no cover - 只在测试自身失败才触发
                    errors.append(exc)

            thread_a = threading.Thread(target=hold_workspace_a, daemon=True)
            thread_b = threading.Thread(target=operate_workspace_b, daemon=True)
            thread_a.start()
            self.assertTrue(entered_a.wait(timeout=3))
            thread_b.start()
            self.assertTrue(entered_b.wait(timeout=1), "Workspace B 被 Workspace A 的锁阻塞。")

            release_a.set()
            thread_a.join(timeout=3)
            thread_b.join(timeout=3)
            self.assertFalse(thread_a.is_alive())
            self.assertFalse(thread_b.is_alive())
            self.assertEqual(errors, [])

    def test_admission_and_abandon_finish_with_pending_before_application_order(self) -> None:
        """并发 admission/Abandon 必须完成，且所有线程先取得 Pending 再取得 Application。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_lifecycle(workspace)
            request = _write_pending(workspace)
            orders: dict[int, list[str]] = {}
            orders_guard = threading.Lock()
            pending_lock = _RecordingLock("pending", orders, orders_guard)
            application_lock = _RecordingLock("application", orders, orders_guard)
            outcomes: list[str] = []
            errors: list[BaseException] = []
            outcome_guard = threading.Lock()
            start_gate = threading.Barrier(3)

            def run_admission() -> None:
                """并发执行一次新的 DAG admission/recovery。"""

                try:
                    start_gate.wait(timeout=3)
                    start_workbench_execution(
                        workspace,
                        scope="application",
                        target_id="application",
                        page_id=None,
                        thread_id="thread-admission",
                        run_id="run-admission",
                        phase="prepare_build_tasks",
                        owner_session_id="session-admission",
                    )
                    outcome = "started"
                except ApplicationLifecycleConflictError:
                    outcome = "conflict"
                except BaseException as exc:  # pragma: no cover - 只在生产逻辑异常时触发
                    errors.append(exc)
                    return
                with outcome_guard:
                    outcomes.append(outcome)

            def run_abandon() -> None:
                """并发执行一次当前 PendingPlan 的 Abandon。"""

                try:
                    start_gate.wait(timeout=3)
                    result = abandon_pending_build_task_plan(
                        {"workspace": workspace},
                        **request,
                    )
                    outcome = result.status
                except BaseException as exc:  # pragma: no cover - 只在生产逻辑异常时触发
                    errors.append(exc)
                    return
                with outcome_guard:
                    outcomes.append(outcome)

            with (
                patch(
                    "app.workspace.task_documents.build_task_plan_lifecycle_lock",
                    return_value=pending_lock,
                ),
                patch(
                    "app.services.application_lifecycle._application_lifecycle_lock",
                    return_value=application_lock,
                ),
            ):
                admission = threading.Thread(target=run_admission, daemon=True)
                abandon = threading.Thread(target=run_abandon, daemon=True)
                admission.start()
                abandon.start()
                start_gate.wait(timeout=3)
                admission.join(timeout=5)
                abandon.join(timeout=5)

            self.assertFalse(admission.is_alive(), "admission 与 Abandon 发生互相等待。")
            self.assertFalse(abandon.is_alive(), "Abandon 与 admission 发生互相等待。")
            self.assertEqual(errors, [])
            expected_outcomes = (
                ["abandoned", "conflict"]
                if "conflict" in outcomes
                else ["abandoned", "started"]
            )
            self.assertCountEqual(outcomes, expected_outcomes)
            for order in orders.values():
                if "application" in order:
                    self.assertEqual(order[0], "pending", order)


if __name__ == "__main__":
    unittest.main()
