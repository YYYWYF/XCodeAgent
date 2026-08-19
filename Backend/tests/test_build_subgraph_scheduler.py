from __future__ import annotations

import os
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.graph.subgraphs.build import _execute_ready_tasks, build, run_build_scheduler
from app.services.build_task_planner import replace_build_task_plan_tasks
from app.services.build_scheduler import attribute_task_file_changes


def _write_workspace_file(workspace: str | None, rel_path: str) -> None:
    """模拟 agent 向工作区写入文件，使 capture_workspace_changes 能检测到变更。"""
    if not workspace:
        return
    full_path = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(f"// auto-generated: {rel_path}\n")


def _ready_build_state(workspace: str, state: dict) -> dict:
    """为调度器测试落盘一份已确认的当前 JSON DAG，匹配真实 Build 门禁。"""

    plan = dict(state.get("build_task_plan") or {})
    plan["status"] = "ready"
    plan["confirmation_status"] = "confirmed"
    plan["confirmed_at"] = "2026-08-19T00:00:00+00:00"
    scope = state.get("build_execution_scope")
    plan["build_execution_scope"] = dict(scope) if isinstance(scope, dict) else {}
    graph = plan.get("task_graph") if isinstance(plan.get("task_graph"), dict) else {}
    # 调度器测试把任务计划视为已经通过前置 DAG 编译；图结构本身由规划器测试覆盖。
    graph["validation"] = {"is_valid": True, "errors": []}
    plan["task_graph"] = graph
    path = os.path.join(workspace, ".xcodeagent", "plans", "build-task-plan.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False)
    return {**state, "build_task_plan": plan}


class BuildSubgraphSchedulerTests(unittest.TestCase):
    def test_backend_and_page_owners_execute_in_parallel_with_isolated_changes(self) -> None:
        """同批 backend/page 必须并发执行，并只认领各自授权文件。"""

        barrier = threading.Barrier(2)
        tasks = [
            {
                "id": "api",
                "owner": "backend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Backend/app/api.py"}],
            },
            {
                "id": "page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Page.tsx"}],
            },
        ]

        def complete_runner(**kwargs):
            barrier.wait(timeout=3)
            for task in kwargs["tasks"]:
                path = str(task["change_scope"][0]["path"])
                _write_workspace_file(kwargs.get("workspace"), path)
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_data_sources_with_deep_agent",
                    side_effect=complete_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=complete_runner,
                ),
            ):
                results, change_sets = _execute_ready_tasks(
                    {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": {"schema_version": "build-dag.v3"},
                    },
                    tasks,
                )

        results_by_id = {result["task_id"]: result for result in results}
        self.assertEqual(results_by_id["api"]["status"], "completed")
        self.assertEqual(results_by_id["page"]["status"], "completed")
        self.assertEqual(results_by_id["api"]["changed_files"], ["Backend/app/api.py"])
        self.assertEqual(results_by_id["page"]["changed_files"], ["Frontend/src/Page.tsx"])
        self.assertEqual(len(change_sets), 2)

    def test_file_changes_are_attributed_to_each_task_scope(self) -> None:
        """同 owner 批次的实际变更只能记到授权路径命中的任务。"""

        results = attribute_task_file_changes(
            results=[
                {"task_id": "page-a", "status": "completed"},
                {"task_id": "page-b", "status": "completed"},
            ],
            code_change_set={
                "files": [
                    {"path": "Frontend/src/PageA.tsx", "changeType": "modified"}
                ]
            },
            tasks=[
                {"id": "page-a", "allowed_paths": ["Frontend/src/PageA.tsx"]},
                {"id": "page-b", "allowed_paths": ["Frontend/src/PageB.tsx"]},
            ],
        )

        self.assertEqual(results[0]["changed_files"], ["Frontend/src/PageA.tsx"])
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[1]["status"], "completed")
        self.assertEqual(results[1]["changed_files"], [])

    def test_build_owner_execution_skips_engineering_acceptance_verifier(self) -> None:
        """代码生成完成后只归属 diff，不再逐任务执行工程验收。"""

        task = {
            "id": "page",
            "owner": "frontend",
            "status": "pending",
            "dependencies": [],
            "change_scope": [
                {"operation": "add", "path": "Frontend/src/Page.tsx"}
            ],
            "acceptance_checks": [
                {
                    "id": "page-file",
                    "kind": "file_operation",
                    "path": "Frontend/src/Page.tsx",
                }
            ],
        }

        def complete_runner(**kwargs):
            workspace = kwargs.get("workspace")
            _write_workspace_file(workspace, "Frontend/src/Page.tsx")
            # Maven/编译工具产生的 target 文件不属于任务 scope，但不应再阻断任务完成。
            _write_workspace_file(
                workspace,
                "backend/target/classes/application.yml",
            )
            return [{"task_id": "page", "owner": "frontend", "status": "completed"}]

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=complete_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.verify_task_file_changes",
                    create=True,
                ) as acceptance_verifier,
            ):
                results, _ = _execute_ready_tasks(
                    {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": {"schema_version": "build-dag.v3"},
                    },
                    [task],
                )

        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[0]["changed_files"], ["Frontend/src/Page.tsx"])
        acceptance_verifier.assert_not_called()

    def test_build_scheduler_runs_dependency_order_until_complete(self) -> None:
        runner_skill_sets: list[list[str] | None] = []
        tasks = [
            {
                "id": "api",
                "owner": "backend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Backend/app/api.py"}],
            },
            {
                "id": "page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": ["api"],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Page.tsx"}],
            },
        ]

        def data_runner(**kwargs):
            runner_skill_sets.append(kwargs.get("selected_skill_names"))
            _write_workspace_file(kwargs.get("workspace"), "Backend/app/api.py")
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        def frontend_runner(**kwargs):
            runner_skill_sets.append(kwargs.get("selected_skill_names"))
            _write_workspace_file(kwargs.get("workspace"), "Frontend/src/Page.tsx")
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_data_sources_with_deep_agent",
                    side_effect=data_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=frontend_runner,
                ),
            ):
                result = build(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "application:root": {
                                        "id": "application:root",
                                        "kind": "application",
                                        "task_ids": ["api", "page"],
                                    }
                                },
                                "unit_graph": {"nodes": ["application:root"], "edges": []},
                            },
                            tasks,
                        ),
                        "timeline": [],
                        "selected_skill_names": ["workflow-skill"],
                    })
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(result["build_summary"]["completed"], 2)
        self.assertEqual([task["status"] for task in result["tasks"]], ["completed", "completed"])
        self.assertIn("scheduler:dispatch:api", result["build_events"])
        self.assertIn("scheduler:dispatch:page", result["build_events"])
        self.assertEqual(runner_skill_sets, [["workflow-skill"], ["workflow-skill"]])

    def test_explicit_retry_resets_runner_failure_and_releases_downstream(self) -> None:
        """重试动作应恢复 runner 失败任务，并在成功后继续执行其下游任务。"""

        calls: list[list[str]] = []
        tasks = [
            {
                "id": "api",
                "owner": "backend",
                "status": "failed",
                "failure_category": "runner_crash",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Backend/app/api.py"}],
            },
            {
                "id": "page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": ["api"],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Page.tsx"}],
            },
        ]
        initial_results = [
            {
                "task_id": "api",
                "owner": "backend",
                "status": "failed",
                "failure_category": "runner_crash",
            }
        ]

        def complete_runner(**kwargs):
            calls.append([str(task["id"]) for task in kwargs["tasks"]])
            for task in kwargs["tasks"]:
                change = task.get("change_scope", [{}])[0]
                _write_workspace_file(kwargs.get("workspace"), str(change.get("path") or ""))
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_data_sources_with_deep_agent",
                    side_effect=complete_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=complete_runner,
                ),
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "application:root": {
                                        "id": "application:root",
                                        "kind": "application",
                                        "task_ids": ["api", "page"],
                                    }
                                },
                                "unit_graph": {"nodes": ["application:root"], "edges": []},
                            },
                            tasks,
                        ),
                        "build_results": initial_results,
                        # 旧快照可能残留修复计划；显式重试不能把它重新派发。
                        "repair_task_plan": {
                            "decision": "repair",
                            "tasks": [
                                {
                                    "id": "stale-repair",
                                    "owner": "backend",
                                    "status": "pending",
                                    "dependencies": [],
                                }
                            ],
                        },
                        "retry_failed_tasks": True,
                        "timeline": [],
                    })
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(result["build_summary"]["retry_task_ids"], ["api"])
        self.assertEqual(calls, [["api"], ["page"]])
        self.assertIn("scheduler:retry:api", result["build_events"])
        self.assertEqual([task["status"] for task in result["tasks"]], ["completed", "completed"])

    def test_explicit_retry_reports_when_no_task_is_retryable(self) -> None:
        """没有 retry 分类候选时应返回明确提示，而不是静默重跑或伪造成功。"""

        tasks = [
            {
                "id": "contract-task",
                "owner": "backend",
                "status": "failed",
                "failure_category": "runner_protocol_error",
                "dependencies": [],
            }
        ]
        with tempfile.TemporaryDirectory() as workspace:
            result = run_build_scheduler(
                _ready_build_state(workspace, {
                    "workspace": workspace,
                    "project_plan": {"version": "1.0.0"},
                    "build_task_plan": replace_build_task_plan_tasks(
                        {
                            "schema_version": "build-dag.v3",
                            "build_units": {
                                "application:root": {
                                    "id": "application:root",
                                    "kind": "application",
                                    "task_ids": ["contract-task"],
                                }
                            },
                            "unit_graph": {"nodes": ["application:root"], "edges": []},
                        },
                        tasks,
                    ),
                    "build_results": [
                        {
                            "task_id": "contract-task",
                            "owner": "backend",
                            "status": "failed",
                            "failure_category": "runner_protocol_error",
                        }
                    ],
                    "retry_failed_tasks": True,
                    "timeline": [],
                })
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["build_summary"]["retry_task_ids"], [])
        self.assertIn("当前没有可重试的构建任务", result["build_summary"]["retry_message"])
        self.assertIn("scheduler:retry:no_candidates", result["build_events"])

    def test_explicit_retry_executes_ready_repair_plan_when_original_failure_is_repairable(self) -> None:
        """验收失败没有瞬时重试候选时，显式恢复应执行已有修复任务。"""

        calls: list[list[str]] = []
        tasks = [
            {
                "id": "page",
                "owner": "frontend",
                "status": "failed",
                "failure_category": "acceptance_verification_failed",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Page.tsx"}],
            }
        ]
        repair_task = {
            "id": "repair-page",
            "kind": "repair",
            "owner": "frontend",
            "status": "pending",
            "task_type": "frontend.code",
            "dependencies": [],
            "change_scope": [{"operation": "add", "path": "Frontend/src/Page.tsx"}],
            "allowed_paths": ["Frontend/src/Page.tsx"],
            "target_files": ["Frontend/src/Page.tsx"],
            "repairs": {"task_id": "page", "result_task_id": "page"},
        }

        def complete_runner(**kwargs):
            calls.append([str(task["id"]) for task in kwargs["tasks"]])
            for task in kwargs["tasks"]:
                _write_workspace_file(
                    kwargs.get("workspace"),
                    str(task.get("change_scope", [{}])[0].get("path") or ""),
                )
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                side_effect=complete_runner,
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "application:root": {
                                        "id": "application:root",
                                        "kind": "application",
                                        "task_ids": ["page"],
                                    }
                                },
                                "unit_graph": {"nodes": ["application:root"], "edges": []},
                            },
                            tasks,
                        ),
                        "build_results": [
                            {
                                "task_id": "page",
                                "owner": "frontend",
                                "status": "failed",
                                "failure_category": "acceptance_verification_failed",
                            }
                        ],
                        "repair_task_plan": {
                            "status": "ready",
                            "decision": "repair",
                            "tasks": [repair_task],
                        },
                        "retry_failed_tasks": True,
                        "timeline": [],
                    })
                )

        self.assertEqual(calls, [["repair-page"]])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["build_summary"]["recovery_mode"], "repair")
        self.assertIn("scheduler:retry:repair:repair-page", result["build_events"])

    def test_explicit_retry_resets_stale_failed_repair_task_before_dispatch(self) -> None:
        """恢复计划仍为 pending 时，必须重置 DAG 中同 ID 的旧失败修复节点。"""

        calls: list[list[str]] = []
        original_task = {
            "id": "page",
            "owner": "frontend",
            "status": "failed",
            "failure_category": "acceptance_verification_failed",
            "dependencies": [],
            "change_scope": [{"operation": "modify", "path": "Frontend/src/Page.tsx"}],
        }
        repair_task = {
            "id": "repair-page",
            "kind": "repair",
            "owner": "frontend",
            "status": "pending",
            "task_type": "frontend.code",
            "dependencies": [],
            "change_scope": [{"operation": "modify", "path": "Frontend/src/Page.tsx"}],
            "allowed_paths": ["Frontend/src/Page.tsx"],
            "target_files": ["Frontend/src/Page.tsx"],
            "repairs": {"task_id": "page", "result_task_id": "page"},
        }
        stale_repair_task = {
            **repair_task,
            "status": "failed",
            "failure_category": "acceptance_verification_failed",
            "failure_reason": "previous recovery attempt stopped before dispatch",
        }

        def complete_runner(**kwargs):
            calls.append([str(task["id"]) for task in kwargs["tasks"]])
            _write_workspace_file(kwargs.get("workspace"), "Frontend/src/Page.tsx")
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            baseline_path = os.path.join(workspace, "Frontend/src/Page.tsx")
            os.makedirs(os.path.dirname(baseline_path), exist_ok=True)
            with open(baseline_path, "w", encoding="utf-8") as file:
                file.write("// existing baseline\n")
            with patch(
                "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                side_effect=complete_runner,
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "unit_graph": {
                                    "nodes": ["application:root"],
                                    "edges": [],
                                },
                            },
                            [original_task, stale_repair_task],
                        ),
                        "build_results": [
                            {
                                "task_id": "page",
                                "owner": "frontend",
                                "status": "failed",
                                "failure_category": "acceptance_verification_failed",
                            }
                        ],
                        "repair_task_plan": {
                            "status": "ready",
                            "decision": "repair",
                            "tasks": [repair_task],
                        },
                        "retry_failed_tasks": True,
                        "timeline": [],
                    })
                )

        self.assertEqual(calls, [["repair-page"]])
        self.assertIn("scheduler:dispatch:repair-page", result["build_events"])
        self.assertEqual(result["status"], "completed")

    def test_database_owner_uses_database_runner_without_file_change_verification(self) -> None:
        """数据库任务由 database.deep_agent 执行，成功结果不要求工作区文件变更。"""

        tasks = [
            {
                "id": "orders-db",
                "unit_id": "database:orders",
                "owner": "database",
                "status": "pending",
                "dependencies": [],
                "task_type": "database.change",
            }
        ]

        def database_runner(**kwargs):
            return [
                {
                    "task_id": task["id"],
                    "owner": "database",
                    "status": "completed",
                    "database_execution": {"status": "completed"},
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.subgraphs.build.generate_database_with_deep_agent",
                side_effect=database_runner,
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "database:orders": {
                                        "id": "database:orders",
                                        "kind": "database",
                                    }
                                },
                                "unit_graph": {"nodes": ["database:orders"], "edges": []},
                            },
                            tasks,
                        ),
                        "timeline": [],
                    })
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(result["tasks"][0]["status"], "completed")
        self.assertEqual(result["build_results"][0]["owner"], "database")

    def test_database_high_risk_result_pauses_for_user_approval(self) -> None:
        """高危数据库计划必须先返回审批交互，任务保持 pending 以便批准后重试。"""

        tasks = [
            {
                "id": "orders-db",
                "unit_id": "database:orders",
                "owner": "database",
                "status": "pending",
                "dependencies": [],
                "task_type": "database.change",
            }
        ]

        def database_runner(**kwargs):
            return [
                {
                    "task_id": "orders-db",
                    "owner": "database",
                    "status": "failed",
                    "failure_category": "database_approval_required",
                    "failure_reason": "需要审批。",
                    "database_change_plan": {"statements": ["ALTER TABLE orders DROP COLUMN old_col"]},
                    "database_risk": {
                        "level": "high",
                        "reasons": ["删除字段"],
                    },
                    "database_approval": {
                        "id": "approval-1",
                        "tool": "database.execute",
                        "title": "高危数据库操作审批",
                        "description": "需要审批。",
                        "subject": "sales / abc123",
                        "risk": {"level": "high", "reasons": ["删除字段"]},
                        "status": "pending",
                    },
                }
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.subgraphs.build.generate_database_with_deep_agent",
                side_effect=database_runner,
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "database:orders": {
                                        "id": "database:orders",
                                        "kind": "database",
                                    }
                                },
                                "unit_graph": {"nodes": ["database:orders"], "edges": []},
                            },
                            tasks,
                        ),
                        "timeline": [],
                    })
                )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["build_summary"]["status"], "requires_confirmation")
        self.assertEqual(result["tasks"][0]["status"], "pending")
        self.assertEqual(result["clarification"]["mode"], "agent_approval")
        self.assertEqual(result["clarification"]["approval"]["tool"], "database.execute")
        self.assertIn("scheduler:database_requires_approval", result["build_events"])

    def test_database_approval_rejection_fails_paused_tasks_without_redispatch(self) -> None:
        """用户拒绝高危数据库审批后，恢复构建应直接失败数据库任务而不是重新调度。"""

        tasks = [
            {
                "id": "orders-db",
                "unit_id": "database:orders",
                "owner": "database",
                "status": "pending",
                "dependencies": [],
                "task_type": "database.change",
                "scheduler": {"paused_for": "database_approval"},
            }
        ]
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.subgraphs.build.generate_database_with_deep_agent",
                side_effect=AssertionError("拒绝后不得重新调度数据库任务"),
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "request": "拒绝执行",
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "database:orders": {
                                        "id": "database:orders",
                                        "kind": "database",
                                    }
                                },
                                "unit_graph": {"nodes": ["database:orders"], "edges": []},
                            },
                            tasks,
                        ),
                        "build_results": [],
                        "timeline": [],
                    })
                )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["build_summary"]["status"], "failed")
        self.assertEqual(result["tasks"][0]["status"], "failed")
        self.assertEqual(
            result["tasks"][0]["scheduler"]["paused_for"],
            "database_approval_rejected",
        )
        self.assertEqual(
            result["build_results"][0]["failure_category"],
            "database_approval_rejected",
        )
        self.assertIn("scheduler:database_approval_rejected", result["build_events"])

    def test_database_approval_resume_reuses_approved_plan(self) -> None:
        """批准恢复后必须复用已审批的数据库计划，避免重新生成导致审批指纹变化。"""

        approved_plan = {
            "summary": "创建 orders 表。",
            "statements": ["CREATE TABLE orders (id BIGINT PRIMARY KEY)"],
        }
        tasks = [
            {
                "id": "orders-db",
                "unit_id": "database:orders",
                "owner": "database",
                "status": "pending",
                "dependencies": [],
                "task_type": "database.change",
                "scheduler": {"paused_for": "database_approval"},
                "approved_database_change_plan": approved_plan,
            }
        ]
        captured: dict = {}

        def database_runner(**kwargs):
            captured.update(kwargs)
            return [
                {
                    "task_id": task["id"],
                    "owner": "database",
                    "status": "completed",
                    "database_execution": {"status": "completed"},
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.subgraphs.build.generate_database_with_deep_agent",
                side_effect=database_runner,
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "request": "同意执行，仅本次",
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "database:orders": {
                                        "id": "database:orders",
                                        "kind": "database",
                                    }
                                },
                                "unit_graph": {"nodes": ["database:orders"], "edges": []},
                            },
                            tasks,
                        ),
                        "build_results": [],
                        "timeline": [],
                    })
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(captured["database_change_plan"], approved_plan)

    def test_build_scheduler_streams_task_progress_snapshots(self) -> None:
        """调度器应在任务运行和结果应用时输出当前执行切片。"""

        progress_events: list[dict] = []
        tasks = [
            {
                "id": "api",
                "unit_id": "database:orders",
                "owner": "backend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Backend/app/orders.py"}],
            },
            {
                "id": "page",
                "unit_id": "page:orders",
                "owner": "frontend",
                "status": "pending",
                "dependencies": ["api"],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Orders.tsx"}],
            },
        ]

        def complete_runner(**kwargs):
            for task in kwargs["tasks"]:
                change = task.get("change_scope", [{}])[0]
                _write_workspace_file(kwargs.get("workspace"), str(change.get("path") or ""))
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_data_sources_with_deep_agent",
                    side_effect=complete_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=complete_runner,
                ),
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_execution_scope": {
                            "type": "page",
                            "targetId": "orders",
                        },
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "database:orders": {
                                        "id": "database:orders",
                                        "kind": "data_source",
                                    },
                                    "page:orders": {"id": "page:orders", "kind": "page"},
                                },
                                "unit_graph": {
                                    "nodes": ["database:orders", "page:orders"],
                                    "edges": [
                                        {
                                            "from": "database:orders",
                                            "to": "page:orders",
                                            "type": "depends_on",
                                        }
                                    ],
                                },
                            },
                            tasks,
                        ),
                        "timeline": [],
                    }),
                    progress_writer=progress_events.append,
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertGreaterEqual(len(progress_events), 4)
        first_slice = progress_events[0]["state"]["build_execution_slice"]
        self.assertEqual(first_slice["scope"], {"type": "page", "targetId": "orders"})
        self.assertEqual(first_slice["summary"]["running"], 1)
        self.assertEqual(
            {task["id"]: task["status"] for task in first_slice["tasks"]},
            {"api": "running", "page": "pending"},
        )
        final_slice = progress_events[-1]["state"]["build_execution_slice"]
        self.assertEqual(final_slice["summary"]["completed"], 2)
        self.assertTrue(
            all(task["status"] == "completed" for task in final_slice["tasks"])
        )

    def test_build_scheduler_streams_ephemeral_tool_activity_and_clears_it(self) -> None:
        """工具活动应只进入运行中任务的临时切片，并在批次结束后清除。"""

        progress_events: list[dict] = []
        tasks = [
            {
                "id": "page-a",
                "unit_id": "page:home",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "allowed_paths": ["Frontend/src/PageA.tsx"],
            },
            {
                "id": "page-b",
                "unit_id": "page:home",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "allowed_paths": ["Frontend/src/PageB.tsx"],
            },
        ]

        def frontend_runner(**kwargs):
            for task in kwargs["tasks"]:
                path = task["allowed_paths"][0]
                activity = {
                    "callId": f"edit-{task['id']}",
                    "tool": "edit_file",
                    "category": "write",
                    "status": "running",
                    "message": f"正在编辑文件：/{path}",
                    "path": f"/{path}",
                }
                kwargs["on_tool_activity"](activity)
                _write_workspace_file(kwargs.get("workspace"), path)
                kwargs["on_tool_activity"](
                    {
                        **activity,
                        "status": "completed",
                        "message": f"已编辑文件：/{path}",
                    }
                )
            return [
                {"task_id": task["id"], "owner": task["owner"], "status": "completed"}
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                side_effect=frontend_runner,
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "page:home": {"id": "page:home", "kind": "page"},
                                },
                                "unit_graph": {"nodes": ["page:home"], "edges": []},
                            },
                            tasks,
                        ),
                        "timeline": [],
                    }),
                    progress_writer=progress_events.append,
                )

        activity_events = [event for event in progress_events if event.get("ephemeral")]
        self.assertGreaterEqual(len(activity_events), 4)
        active_snapshots = [
            event["state"]["build_execution_slice"]["tasks"]
            for event in activity_events
            if any(
                "activeToolActivity" in task
                for task in event["state"]["build_execution_slice"]["tasks"]
            )
        ]
        self.assertEqual(
            {
                task["activeToolActivity"]["callId"]
                for tasks_snapshot in active_snapshots
                for task in tasks_snapshot
                if "activeToolActivity" in task
            },
            {"edit-page-a", "edit-page-b"},
        )
        self.assertTrue(
            all(
                task["status"] == "running"
                for tasks_snapshot in active_snapshots
                for task in tasks_snapshot
                if "activeToolActivity" in task
            )
        )
        cleared_tasks = activity_events[-1]["state"]["build_execution_slice"]["tasks"]
        self.assertTrue(all("activeToolActivity" not in task for task in cleared_tasks))
        self.assertTrue(
            all("activeToolActivity" not in task for task in result["build_execution_slice"]["tasks"])
        )

    def test_build_scheduler_plans_and_runs_repair_task(self) -> None:
        tasks = [
            {
                "id": "page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Page.tsx"}],
                "allowed_paths": ["Frontend/src/**"],
                "acceptance_criteria": ["页面可渲染"],
            },
        ]

        def frontend_runner(**kwargs):
            results = []
            for task in kwargs["tasks"]:
                if task.get("kind") == "repair":
                    _write_workspace_file(kwargs.get("workspace"), "Frontend/src/Page.tsx")
                    results.append(
                        {
                            "task_id": task["id"],
                            "owner": task["owner"],
                            "status": "completed",
                        }
                    )
                else:
                    results.append(
                        {
                            "task_id": task["id"],
                            "owner": task["owner"],
                            "status": "failed",
                            "failure_category": "test_failure",
                            "failure_signature": "test_failure:page",
                        }
                    )
            return results

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=frontend_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.plan_build_failure_repair_with_repair_planner_agent",
                    return_value={
                        "decision": "repair",
                        "strategy": "修复页面测试失败。",
                        "boundaries": {},
                        "repair_tasks": [
                            {
                                "title": "修复页面测试失败",
                                "description": "只在原任务范围内修复测试失败。",
                            }
                        ],
                    },
                ) as repair_planner,
            ):
                result = build(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "application:root": {
                                        "id": "application:root",
                                        "kind": "application",
                                        "task_ids": ["page"],
                                    }
                                },
                                "unit_graph": {"nodes": ["application:root"], "edges": []},
                            },
                            tasks,
                        ),
                        "timeline": [],
                        "selected_skill_names": ["repair-skill"],
                    })
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(result["tasks"][0]["status"], "completed")
        self.assertTrue(result["tasks"][0]["completed_by_repair"])
        self.assertEqual(result["repair_task_plan"]["status"], "ready")
        self.assertIn("scheduler:repair_planned:1", result["build_events"])
        self.assertEqual(
            repair_planner.call_args.kwargs["selected_skill_names"],
            ["repair-skill"],
        )

    def test_failed_task_does_not_abort_independent_ready_tasks(self) -> None:
        """失败任务不应阻止与其无依赖关系的独立任务继续执行。"""
        tasks = [
            {
                "id": "task_backend_bootstrap",
                "owner": "backend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Backend/app/bootstrap.py"}],
            },
            {
                "id": "task_frontend_api_client",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Frontend/src/apiClient.ts"}],
            },
            {
                "id": "task_frontend_ledger_page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": ["task_frontend_api_client"],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Ledger.tsx"}],
            },
            {
                "id": "task_backend_project_list",
                "owner": "backend",
                "status": "pending",
                "dependencies": ["task_backend_bootstrap"],
                "change_scope": [{"operation": "add", "path": "Backend/app/project_list.py"}],
            },
        ]

        def backend_runner(**kwargs):
            # bootstrap 始终崩溃（runner_crash，可重试但不可修复）
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "failed",
                    "failure_category": "runner_crash",
                    "failure_reason": "ReadTimeout",
                }
                for task in kwargs["tasks"]
            ]

        def frontend_runner(**kwargs):
            for task in kwargs["tasks"]:
                _write_workspace_file(
                    kwargs.get("workspace"), task["change_scope"][0]["path"]
                )
            return [
                {"task_id": task["id"], "owner": task["owner"], "status": "completed"}
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_data_sources_with_deep_agent",
                    side_effect=backend_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=frontend_runner,
                ),
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "application:root": {
                                        "id": "application:root",
                                        "kind": "application",
                                        "task_ids": [
                                            "task_backend_bootstrap",
                                            "task_frontend_api_client",
                                            "task_frontend_ledger_page",
                                            "task_backend_project_list",
                                        ],
                                    }
                                },
                                "unit_graph": {"nodes": ["application:root"], "edges": []},
                            },
                            tasks,
                        ),
                        "timeline": [],
                    })
                )

        statuses = {task["id"]: task["status"] for task in result["tasks"]}
        # 独立任务必须实际执行完成（修复前会停在 pending）
        self.assertEqual(statuses["task_frontend_api_client"], "completed")
        self.assertEqual(statuses["task_frontend_ledger_page"], "completed")
        # 失败任务保持失败
        self.assertEqual(statuses["task_backend_bootstrap"], "failed")
        # 被失败任务阻塞的下游保持 pending（未被派发）
        self.assertEqual(statuses["task_backend_project_list"], "pending")
        # ledger 必须出现在派发事件中（关键断言：修复前不会出现）
        self.assertIn(
            "scheduler:dispatch:task_frontend_ledger_page", result["build_events"]
        )
        # 最终构建状态为 failed（bootstrap 失败且其下游永久阻塞）
        self.assertEqual(result["build_summary"]["status"], "failed")
        self.assertEqual(result["build_summary"]["completed"], 2)
        self.assertEqual(result["build_summary"]["failed"], 1)
        self.assertEqual(result["build_summary"]["pending"], 1)

    def test_page_scope_does_not_execute_unrelated_page_tasks(self) -> None:
        """页面范围只调度目标页面闭包内任务，不更新其他页面任务。"""

        dispatched_task_ids: list[str] = []
        tasks = [
            {
                "id": "orders-api",
                "unit_id": "database:orders",
                "owner": "backend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Backend/app/orders.py"}],
            },
            {
                "id": "orders-page",
                "unit_id": "page:orders",
                "owner": "frontend",
                "status": "pending",
                "dependencies": ["orders-api"],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Orders.tsx"}],
            },
            {
                "id": "customers-page",
                "unit_id": "page:customers",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Customers.tsx"}],
            },
        ]

        def complete_runner(**kwargs):
            dispatched_task_ids.extend(task["id"] for task in kwargs["tasks"])
            for task in kwargs["tasks"]:
                change = task.get("change_scope", [{}])[0]
                _write_workspace_file(kwargs.get("workspace"), str(change.get("path") or ""))
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_data_sources_with_deep_agent",
                    side_effect=complete_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=complete_runner,
                ),
            ):
                result = build(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_execution_scope": {"type": "page", "targetId": "orders"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {
                                    "database:orders": {
                                        "id": "database:orders",
                                        "kind": "data_source",
                                    },
                                    "page:orders": {"id": "page:orders", "kind": "page"},
                                    "page:customers": {
                                        "id": "page:customers",
                                        "kind": "page",
                                    },
                                },
                                "unit_graph": {
                                    "nodes": [
                                        "database:orders",
                                        "page:orders",
                                        "page:customers",
                                    ],
                                    "edges": [
                                        {
                                            "from": "database:orders",
                                            "to": "page:orders",
                                            "type": "depends_on",
                                        }
                                    ],
                                },
                            },
                            tasks,
                        ),
                        "timeline": [],
                    })
                )

        self.assertEqual(dispatched_task_ids, ["orders-api", "orders-page"])
        statuses = {task["id"]: task["status"] for task in result["tasks"]}
        self.assertEqual(statuses["orders-api"], "completed")
        self.assertEqual(statuses["orders-page"], "completed")
        self.assertEqual(statuses["customers-page"], "pending")
        self.assertEqual(result["build_execution_slice"]["task_ids"], ["orders-api", "orders-page"])
        self.assertEqual(result["build_summary"]["status"], "completed")

    def test_incoming_integration_repair_task_enters_page_scope_and_counts_on_dispatch(self) -> None:
        """测试修复任务必须追加到最新计划、命中页面切片并在真实派发后计数。"""

        dispatched_task_ids: list[str] = []
        tasks = [
            {
                "id": "orders-page",
                "unit_id": "page:orders",
                "owner": "frontend",
                "status": "completed",
                "dependencies": [],
                "allowed_paths": ["Frontend/src/Orders.tsx"],
                "change_scope": [{"operation": "add", "path": "Frontend/src/Orders.tsx"}],
            }
        ]
        repair_task = {
            "id": "repair:plan-1:frontend_build:frontend",
            "task_id": "repair:plan-1:frontend_build:frontend",
            "kind": "repair",
            "unit_id": "page:orders",
            "owner": "frontend",
            "status": "pending",
            "dependencies": [],
            "allowed_paths": ["Frontend/src/Orders.tsx"],
            "change_scope": [{"operation": "add", "path": "Frontend/src/Orders.tsx"}],
        }

        def repair_runner(**kwargs):
            dispatched_task_ids.extend(task["id"] for task in kwargs["tasks"])
            _write_workspace_file(kwargs.get("workspace"), "Frontend/src/Orders.tsx")
            return [
                {"task_id": task["id"], "owner": "frontend", "status": "completed"}
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                side_effect=repair_runner,
            ):
                result = run_build_scheduler(
                    _ready_build_state(workspace, {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_execution_scope": {"type": "page", "targetId": "orders"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v3",
                                "build_units": {"page:orders": {"id": "page:orders", "kind": "page"}},
                                "unit_graph": {"nodes": ["page:orders"], "edges": []},
                            },
                            tasks,
                        ),
                        "tasks": tasks,
                        "repair_task_plan": {
                            "status": "ready",
                            "decision": "repair",
                            "tasks": [repair_task],
                        },
                        "repair_iteration": 0,
                    })
                )

        self.assertEqual(dispatched_task_ids, [repair_task["id"]])
        self.assertIn(repair_task["id"], result["build_execution_slice"]["task_ids"])
        self.assertEqual(result["repair_iteration"], 1)
        self.assertEqual(result["build_summary"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
