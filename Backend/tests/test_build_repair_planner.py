from __future__ import annotations

import unittest

from app.services.engineering_acceptance import (
    compile_engineering_acceptance,
    migrate_legacy_repair_acceptance,
)
from app.services.build_repair_planner import (
    approve_repair_scope_confirmation,
    append_repair_tasks_to_build_plan,
    close_repaired_parent_tasks,
    create_build_failure_repair_plan,
)
from app.services.build_task_planner import (
    replace_build_task_plan_tasks,
    tasks_from_build_task_plan,
)


class BuildRepairPlannerTests(unittest.TestCase):
    def test_creates_bounded_repair_task_from_failed_result(self) -> None:
        task = {
            "id": "page",
            "owner": "frontend",
            "title": "实现页面",
            "status": "failed",
            "change_scope": [{"path": "Frontend/src/Page.tsx"}],
            "allowed_paths": ["Frontend/src/**"],
            "acceptance_criteria": ["页面可渲染"],
        }
        result = {
            "task_id": "page",
            "status": "failed",
            "failure_category": "test_failure",
            "failure_signature": "test_failure:page",
            "scheduler_decision": {"action": "repair", "reason": "test_failure"},
        }

        plan = create_build_failure_repair_plan(
            failed_results=[result],
            tasks=[task],
            repair_planner=lambda repair_input: {
                "decision": "repair",
                "strategy": "修复页面渲染失败并重新满足原验收条件。",
                "boundaries": {"contract_policy": "do_not_change_contract"},
                "repair_tasks": [
                    {
                        "title": "修复页面渲染",
                        "description": "只在原页面文件范围内修复渲染失败。",
                    }
                ],
            },
        )

        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["decision"], "repair")
        self.assertEqual(plan["prepared_by"]["mode"], "deep_agent_constrained")
        self.assertEqual(plan["tasks"][0]["kind"], "repair")
        self.assertEqual(plan["tasks"][0]["repairs"]["task_id"], "page")
        self.assertEqual(plan["tasks"][0]["allowed_paths"], ["Frontend/src/**"])
        self.assertFalse(plan["tasks"][0]["can_run_in_parallel"])
        self.assertEqual(plan["tasks"][0]["repair_strategy"], "修复页面渲染失败并重新满足原验收条件。")
        self.assertIn("planner_inputs", plan)

    def test_requires_user_confirmation_does_not_create_repair_tasks(self) -> None:
        task = {
            "id": "api",
            "owner": "backend",
            "title": "实现 API",
            "status": "failed",
            "change_scope": [{"path": "Backend/app/api.py"}],
            "allowed_paths": ["Backend/app/**"],
            "acceptance_criteria": ["API contract satisfied"],
        }
        result = {
            "task_id": "api",
            "status": "failed",
            "failure_category": "contract_mismatch",
            "failure_signature": "contract_mismatch:api",
            "scheduler_decision": {"action": "repair", "reason": "contract_mismatch"},
        }

        plan = create_build_failure_repair_plan(
            failed_results=[result],
            tasks=[task],
            repair_planner=lambda repair_input: {
                "decision": "requires_user_confirmation",
                "reason": "需要扩大 API contract 才能继续。",
                "strategy": "暂停并请求用户确认契约变更。",
                "boundaries": {
                    "requested_resources": [
                        {"type": "api_contract", "targetId": "orders-api"},
                        {"type": "page", "targetId": "order-detail"},
                    ]
                },
            },
        )

        self.assertEqual(plan["decision"], "requires_user_confirmation")
        self.assertEqual(plan["status"], "requires_user_confirmation")
        self.assertEqual(plan["tasks"], [])
        self.assertEqual(len(plan["requires_user_confirmation"]), 1)
        self.assertTrue(plan["planId"])
        self.assertEqual(plan["requestedPaths"], ["Backend/app/**"])
        self.assertEqual(
            plan["requestedResources"],
            [
                {"type": "api_contract", "targetId": "orders-api"},
                {"type": "page", "targetId": "order-detail"},
            ],
        )

        approved = approve_repair_scope_confirmation(plan)

        self.assertEqual(approved["decision"], "repair")
        self.assertEqual(approved["approvedPlanId"], plan["planId"])
        self.assertEqual(len(approved["tasks"]), 1)
        self.assertEqual(approved["tasks"][0]["unit_id"], "application:root")
        self.assertEqual(approved["tasks"][0]["allowed_paths"], ["Backend/app/**"])

    def test_appends_repair_tasks_to_build_plan(self) -> None:
        repair_task = {"id": "repair:page:test", "kind": "repair", "status": "pending"}
        updated = append_repair_tasks_to_build_plan(
            build_task_plan=replace_build_task_plan_tasks(
                {"schema_version": "build-dag.v3", "build_units": {}, "unit_graph": {}},
                [{"id": "page", "owner": "frontend", "status": "failed", "dependencies": []}],
            ),
            repair_task_plan={"tasks": [repair_task]},
        )

        self.assertEqual(
            [task["id"] for task in tasks_from_build_task_plan(updated)],
            ["page", "repair:page:test"],
        )
        self.assertEqual(updated["summary"]["repair"], 1)

    def test_closes_parent_task_when_repair_succeeds(self) -> None:
        tasks = [
            {"id": "page", "status": "failed"},
            {
                "id": "repair:page:test",
                "kind": "repair",
                "status": "completed",
                "repairs": {"task_id": "page"},
            },
        ]
        closed = close_repaired_parent_tasks(
            tasks=tasks,
            results=[{"task_id": "repair:page:test", "status": "completed"}],
        )

        self.assertEqual(closed[0]["status"], "completed")
        self.assertTrue(closed[0]["completed_by_repair"])

    def test_closes_parent_as_already_satisfied_when_repair_verifies_noop(self) -> None:
        """修复任务确认目标已存在时，应关闭父任务而不是再次要求写入。"""

        tasks = [
            {"id": "menu", "status": "failed"},
            {
                "id": "repair:menu:test",
                "kind": "repair",
                "status": "already_satisfied",
                "repairs": {"task_id": "menu"},
            },
        ]
        closed = close_repaired_parent_tasks(
            tasks=tasks,
            results=[{"task_id": "repair:menu:test", "status": "already_satisfied"}],
        )

        self.assertEqual(closed[0]["status"], "already_satisfied")
        self.assertTrue(closed[0]["completed_by_repair"])

    def test_repair_task_keeps_parent_outcome_checks_and_rejects_business_criterion(self) -> None:
        """Repair 只继承结果型工程检查，不接受 Planner 自行扩展的验收文案。"""

        task = compile_engineering_acceptance(
            [
                {
                    "id": "menu",
                    "owner": "frontend",
                    "status": "failed",
                    "change_scope": [
                        {"operation": "modify", "path": "frontend/src/constants/menus.ts"}
                    ],
                    "allowed_paths": ["frontend/src/constants/menus.ts"],
                    "engineering_context": {
                        "menu_registration": {
                            "file": "frontend/src/constants/menus.ts",
                            "path": "dashboard",
                            "name": "概览",
                            "key": "DashboardPage",
                        }
                    },
                }
            ],
            {},
        )[0]
        result = {
            "task_id": "menu",
            "status": "failed",
            "failure_category": "no_file_changes",
            "scheduler_decision": {"action": "repair", "reason": "no_file_changes"},
        }

        plan = create_build_failure_repair_plan(
            failed_results=[result],
            tasks=[task],
            repair_planner=lambda repair_input: {
                "decision": "repair",
                "strategy": "检查并修复菜单",
                "repair_tasks": [
                    {
                        "title": "修复菜单",
                        "description": "确保菜单项存在",
                        "acceptance_criteria": ["文件必须产生实际变更（非空写入）"],
                    }
                ],
            },
        )

        repair_task = plan["tasks"][0]
        acceptance = repair_task["acceptance_criteria"]
        self.assertIn("menu_registration", [item["kind"] for item in repair_task["acceptance_checks"]])
        self.assertFalse(any("文件必须产生实际变更（非空写入）" in item for item in acceptance))

    def test_repair_recompiles_file_operations_for_exact_repair_scope(self) -> None:
        """修复 DTO 时不得继续要求父任务全部新增文件再次产生 added 差异。"""

        parent = compile_engineering_acceptance(
            [
                {
                    "id": "backend-create",
                    "owner": "backend",
                    "status": "failed",
                    "change_scope": [
                        {"operation": "add", "path": "backend/Entity.java"},
                        {"operation": "add", "path": "backend/CreateDTO.java"},
                    ],
                }
            ],
            {},
        )[0]
        result = {
            "task_id": "backend-create",
            "status": "failed",
            "failure_category": "acceptance_verification_failed",
            "scheduler_decision": {
                "action": "repair",
                "reason": "acceptance_verification_failed",
            },
        }

        plan = create_build_failure_repair_plan(
            failed_results=[result],
            tasks=[parent],
            repair_planner=lambda _: {
                "decision": "repair",
                "strategy": "补充 DTO JSON 映射。",
                "repair_tasks": [
                    {
                        "title": "修复 DTO",
                        "description": "只修改 DTO 映射。",
                        "change_scope": [
                            {
                                "operation": "modify",
                                "path": "backend/CreateDTO.java",
                            }
                        ],
                    }
                ],
            },
        )

        repair_checks = plan["tasks"][0]["acceptance_checks"]
        file_checks = [item for item in repair_checks if item["kind"] == "file_operation"]
        self.assertEqual(len(file_checks), 1)
        self.assertEqual(file_checks[0]["target_paths"], ["backend/CreateDTO.java"])
        self.assertEqual(file_checks[0]["expected"]["change_type"], "modified")

    def test_migrates_legacy_repair_add_checks_for_safe_resume(self) -> None:
        """旧 Repair 因重复要求 added 失败时，应恢复 DTO 精确范围并允许续跑。"""

        parent = compile_engineering_acceptance(
            [
                {
                    "id": "backend-create",
                    "owner": "backend",
                    "status": "failed",
                    "change_scope": [
                        {"operation": "add", "path": "/backend/Entity.java"},
                        {"operation": "add", "path": "/backend/CreateDTO.java"},
                    ],
                }
            ],
            {},
        )[0]
        legacy_repair = {
            **parent,
            "id": "repair:backend-create:legacy",
            "kind": "repair",
            "repairs": {"task_id": "backend-create"},
            "description": "Edit /backend/CreateDTO.java to add JSON mapping.",
            "status": "failed",
            "last_result_status": "failed",
            "failure_category": "acceptance_verification_failed",
            "failure_reason": "Entity 预期差异类型 added，实际为 none。",
        }

        migrated = migrate_legacy_repair_acceptance([parent, legacy_repair])[1]

        self.assertEqual(migrated["status"], "pending")
        self.assertTrue(migrated["legacy_acceptance_recovered"])
        self.assertEqual(
            migrated["change_scope"],
            [
                {
                    "operation": "modify",
                    "path": "/backend/CreateDTO.java",
                    "description": "从旧 Repair 描述恢复的精确修复目标。",
                }
            ],
        )
        file_checks = [
            item for item in migrated["acceptance_checks"] if item["kind"] == "file_operation"
        ]
        self.assertEqual(len(file_checks), 1)
        self.assertEqual(file_checks[0]["expected"]["change_type"], "modified")


if __name__ == "__main__":
    unittest.main()
