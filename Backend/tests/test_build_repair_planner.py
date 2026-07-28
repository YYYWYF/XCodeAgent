from __future__ import annotations

import unittest

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
            "owner": "data_source",
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
                {"schema_version": "build-dag.v2", "build_units": {}, "unit_graph": {}},
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

    def test_repair_task_keeps_parent_acceptance_and_rejects_forced_write_criterion(self) -> None:
        """RepairPlanner 不得把“必须产生变更”扩展成新的验收条件。"""

        task = {
            "id": "menu",
            "owner": "frontend",
            "status": "failed",
            "change_scope": [{"path": "frontend/src/constants/menus.ts"}],
            "allowed_paths": ["frontend/src/constants/menus.ts"],
            "acceptance_criteria": ["DashboardPage 菜单项存在"],
        }
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

        acceptance = plan["tasks"][0]["acceptance_criteria"]
        self.assertIn("DashboardPage 菜单项存在", acceptance)
        self.assertNotIn("文件必须产生实际变更（非空写入）", acceptance)


if __name__ == "__main__":
    unittest.main()
