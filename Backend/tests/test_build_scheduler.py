from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from app.services.build_scheduler import (
    classify_task_result,
    hydrate_missing_failed_results,
    normalize_task_results,
    reset_failed_tasks_for_retry,
    retryable_failed_task_ids,
    select_ready_build_batch,
    summarize_build_runtime,
    verify_task_file_changes,
)


class BuildSchedulerTests(unittest.TestCase):
    def test_selects_dependency_ready_lock_compatible_batch(self) -> None:
        tasks = [
            {
                "id": "api",
                "owner": "backend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Backend/app/api.py"}],
            },
            {
                "id": "api-test",
                "owner": "backend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Backend/app/api.py"}],
            },
            {
                "id": "page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Frontend/src/Page.tsx"}],
            },
        ]

        selection = select_ready_build_batch(tasks)

        self.assertEqual(selection["ready_task_ids"], ["api", "page"])

    def test_failed_dependency_blocks_downstream(self) -> None:
        tasks = [
            {"id": "api", "status": "failed", "dependencies": []},
            {"id": "page", "status": "pending", "dependencies": ["api"]},
        ]

        selection = select_ready_build_batch(tasks)

        self.assertEqual(selection["ready_tasks"], [])
        self.assertEqual(selection["blocked_tasks"][0]["id"], "page")
        self.assertEqual(selection["blocked_tasks"][0]["failed_dependencies"], ["api"])

    def test_database_tasks_gate_backend_even_without_explicit_dependency(self) -> None:
        """数据库任务未成功完成前，后端任务不能靠缺失依赖同批执行。"""

        selection = select_ready_build_batch(
            [
                {
                    "id": "db",
                    "owner": "database",
                    "status": "pending",
                    "dependencies": [],
                    "change_scope": [{"path": "Backend/db/schema.sql"}],
                },
                {
                    "id": "api",
                    "owner": "backend",
                    "status": "pending",
                    "dependencies": [],
                    "change_scope": [{"path": "Backend/app/api.py"}],
                },
            ]
        )

        self.assertEqual(selection["ready_task_ids"], ["db"])

    def test_completed_database_task_unblocks_backend_without_explicit_dependency(self) -> None:
        """数据库任务完成后，兼容旧计划中未显式声明依赖的后端任务。"""

        selection = select_ready_build_batch(
            [
                {
                    "id": "db",
                    "owner": "database",
                    "status": "completed",
                    "dependencies": [],
                },
                {
                    "id": "api",
                    "owner": "backend",
                    "status": "pending",
                    "dependencies": [],
                },
            ]
        )

        self.assertEqual(selection["ready_task_ids"], ["api"])

    def test_normalizes_missing_runner_result_as_protocol_failure(self) -> None:
        task = {"id": "page", "owner": "frontend"}

        results = normalize_task_results(dispatched_tasks=[task], raw_results=[])

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["failure_category"], "runner_protocol_error")
        self.assertEqual(
            results[0]["scheduler_decision"]["action"],
            "terminal_failure",
        )

    def test_classifies_implementation_failure_as_repair(self) -> None:
        decision = classify_task_result(
            {"task_id": "page", "status": "failed", "failure_category": "test_failure"}
        )

        self.assertEqual(decision["action"], "repair")

    def test_classifies_invalid_structured_response_as_retry(self) -> None:
        """损坏且无法恢复的 Agent 终态报告应进入受控重试分类。"""

        decision = classify_task_result(
            {
                "task_id": "page",
                "status": "failed",
                "failure_category": "invalid_structured_response",
            }
        )

        self.assertEqual(decision["action"], "retry")

    def test_business_acceptance_blocked_does_not_create_repair(self) -> None:
        """Verifier blocked 属于验证能力或正式来源问题，不应修改业务实现。"""

        decision = classify_task_result(
            {
                "task_id": "api",
                "status": "failed",
                "failure_category": "business_acceptance_blocked",
            }
        )

        self.assertEqual(decision["action"], "terminal_failure")

    def test_retries_only_failed_tasks_with_retry_classification(self) -> None:
        """显式重试只恢复 runner/tool 类失败，不会重跑实现失败任务。"""

        tasks = [
            {
                "id": "runner-task",
                "status": "failed",
                "failure_category": "runner_crash",
            },
            {
                "id": "compile-task",
                "status": "failed",
                "failure_category": "compile_error",
            },
            {"id": "downstream", "status": "pending", "dependencies": ["runner-task"]},
        ]
        build_results = [
            {
                "task_id": "runner-task",
                "status": "failed",
                "failure_category": "runner_crash",
            },
            {
                "task_id": "compile-task",
                "status": "failed",
                "failure_category": "compile_error",
            },
        ]

        retry_ids = retryable_failed_task_ids(tasks, build_results)
        reset = reset_failed_tasks_for_retry(tasks, retry_ids)

        self.assertEqual(retry_ids, {"runner-task"})
        self.assertEqual(reset[0]["status"], "pending")
        self.assertEqual(reset[0]["scheduler"]["last_action"], "retry_failed_tasks")
        self.assertEqual(reset[1]["status"], "failed")
        summary = summarize_build_runtime(tasks, build_results)
        self.assertEqual(summary["retryable_failures"], 1)
        self.assertFalse(summary["retry_available"])

    def test_repaired_parent_ignores_stale_failed_result(self) -> None:
        """修复成功关闭父任务后，旧失败结果不应再次触发修复并阻塞下游。"""

        tasks = [
            {
                "id": "api",
                "status": "completed",
                "completed_by_repair": True,
                "dependencies": [],
            },
            {
                "id": "repair:api:acceptance",
                "kind": "repair",
                "status": "completed",
                "dependencies": [],
            },
            {"id": "page", "status": "pending", "dependencies": ["api"]},
        ]
        build_results = [
            {
                "task_id": "api",
                "status": "failed",
                "failure_category": "acceptance_verification_failed",
            },
            {"task_id": "repair:api:acceptance", "status": "completed"},
        ]

        summary = summarize_build_runtime(tasks, build_results)
        selection = select_ready_build_batch(tasks)

        self.assertEqual(summary["repairable_failures"], 0)
        self.assertEqual(summary["status"], "in_progress")
        self.assertEqual(selection["ready_task_ids"], ["page"])

    def test_hydrates_failed_result_from_persisted_task_state(self) -> None:
        """checkpoint 丢失结果流时，任务注册表仍能恢复修复分类所需的失败证据。"""

        results = hydrate_missing_failed_results(
            [
                {
                    "id": "backend-task",
                    "owner": "backend",
                    "status": "failed",
                    "failure_category": "acceptance_verification_failed",
                    "failure_reason": "缺少 Schema JSON 映射字段。",
                    "failure_detail": {
                        "scheduler_decision": {
                            "action": "repair",
                            "reason": "acceptance_verification_failed",
                        }
                    },
                }
            ],
            [],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["task_id"], "backend-task")
        self.assertEqual(results[0]["scheduler_decision"]["action"], "repair")

    def test_already_satisfied_ignores_agent_claim_and_verifies_all_checks(self) -> None:
        """已满足必须由磁盘与工程检查证明，Agent 自报证据不参与裁决。"""

        task = {
            "id": "page",
            "owner": "frontend",
            "target_files": ["frontend/src/pages/Dashboard/index.tsx"],
            "change_scope": [
                {
                    "operation": "modify",
                    "path": "frontend/src/pages/Dashboard/index.tsx",
                }
            ],
            "acceptance_criteria": ["页面可编译", "标题可见"],
        }
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / task["target_files"][0]
            target.parent.mkdir(parents=True)
            target.write_text("export default function Dashboard() {}", encoding="utf-8")
            results = verify_task_file_changes(
                results=[
                    {
                        "task_id": "page",
                        "status": "already_satisfied",
                        "satisfaction_evidence": {"claimed": "passed"},
                    }
                ],
                code_change_set=None,
                tasks=[task],
                workspace_root=workspace,
            )

        self.assertEqual(results[0]["status"], "already_satisfied")
        self.assertEqual(results[0]["scheduler_decision"]["action"], "complete")
        self.assertEqual(len(results[0]["acceptance_evidence"]), 2)
        self.assertTrue(
            all(item["status"] == "passed" for item in results[0]["acceptance_evidence"])
        )

    def test_completed_requires_every_exact_file_operation(self) -> None:
        """Agent 只修改部分目标文件时，completed 必须被确定性验收拒绝。"""

        task = {
            "id": "page",
            "owner": "frontend",
            "change_scope": [
                {"operation": "add", "path": "frontend/src/pages/Page/index.tsx"},
                {"operation": "add", "path": "frontend/src/apis/pageApi.ts"},
            ],
        }
        results = verify_task_file_changes(
            results=[{"task_id": "page", "status": "completed"}],
            code_change_set={
                "files": [
                    {
                        "path": "frontend/src/pages/Page/index.tsx",
                        "changeType": "added",
                    }
                ]
            },
            tasks=[task],
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["failure_category"], "acceptance_verification_failed")
        self.assertIn("frontend/src/apis/pageApi.ts", results[0]["failure_reason"])

    def test_completed_rejects_wrong_change_type_and_batch_scope_violation(self) -> None:
        """文件操作类型不符或批次越权时，任务不得标记完成。"""

        task = {
            "id": "page",
            "owner": "frontend",
            "change_scope": [
                {"operation": "add", "path": "frontend/src/pages/Page/index.tsx"}
            ],
        }
        results = verify_task_file_changes(
            results=[{"task_id": "page", "status": "completed"}],
            code_change_set={
                "files": [
                    {
                        "path": "frontend/src/pages/Page/index.tsx",
                        "changeType": "modified",
                    }
                ]
            },
            tasks=[task],
            batch_unauthorized_paths=["frontend/vite.config.ts"],
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("预期差异类型 added", results[0]["failure_reason"])
        self.assertIn("frontend/vite.config.ts", results[0]["failure_reason"])

    def test_similar_file_cannot_satisfy_missing_exact_target(self) -> None:
        """语义相似但路径不同的文件不得通过已满足校验。"""

        task = {
            "id": "page",
            "owner": "frontend",
            "target_files": ["frontend/src/pages/DashboardPage/index.tsx"],
            "change_scope": [
                {
                    "operation": "add",
                    "path": "frontend/src/pages/DashboardPage/index.tsx",
                }
            ],
            "acceptance_criteria": ["目标文件存在"],
        }
        with tempfile.TemporaryDirectory() as workspace:
            similar = Path(workspace) / "frontend/src/pages/Dashboard/index.tsx"
            similar.parent.mkdir(parents=True)
            similar.write_text("export default function Dashboard() {}", encoding="utf-8")
            results = verify_task_file_changes(
                results=[
                    {
                        "task_id": "page",
                        "status": "already_satisfied",
                        "satisfaction_evidence": {
                            "target_files": task["target_files"],
                            "acceptance_criteria": [
                                {
                                    "criterion": "目标文件存在",
                                    "status": "passed",
                                    "evidence": "发现 Dashboard 页面",
                                }
                            ],
                        },
                    }
                ],
                code_change_set=None,
                tasks=[task],
                workspace_root=workspace,
            )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["failure_category"], "acceptance_verification_failed")

    def test_already_satisfied_dependency_unblocks_downstream_task(self) -> None:
        """结构化已满足是终态，必须和 completed 一样解除下游依赖。"""

        selection = select_ready_build_batch(
            [
                {"id": "layout", "status": "already_satisfied", "dependencies": []},
                {"id": "data", "status": "pending", "dependencies": ["layout"]},
            ]
        )

        self.assertEqual(selection["ready_task_ids"], ["data"])


if __name__ == "__main__":
    unittest.main()
