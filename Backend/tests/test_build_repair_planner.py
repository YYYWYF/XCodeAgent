from __future__ import annotations

import unittest

from app.services.engineering_acceptance import (
    compile_engineering_acceptance,
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
                    "id": "page",
                    "owner": "frontend",
                    "status": "failed",
                    "change_scope": [
                        {"operation": "modify", "path": "frontend/src/pages/Dashboard/index.tsx"}
                    ],
                    "allowed_paths": ["frontend/src/pages/Dashboard/index.tsx"],
                }
            ],
            {},
        )[0]
        result = {
            "task_id": "page",
            "status": "failed",
            "failure_category": "no_file_changes",
            "scheduler_decision": {"action": "repair", "reason": "no_file_changes"},
        }

        plan = create_build_failure_repair_plan(
            failed_results=[result],
            tasks=[task],
            repair_planner=lambda repair_input: {
                "decision": "repair",
                "strategy": "检查并修复页面",
                "repair_tasks": [
                    {
                        "title": "修复页面",
                        "description": "确保页面内容正确",
                        "acceptance_criteria": ["文件必须产生实际变更（非空写入）"],
                    }
                ],
            },
        )

        repair_task = plan["tasks"][0]
        self.assertNotIn("acceptance_criteria", repair_task)
        self.assertEqual(repair_task["business_acceptance_checks"], [])

    def test_repair_task_preserves_platform_authorization_slice(self) -> None:
        """RepairPlanner 不得替换或扩大父任务的权限事实。"""

        task = {
            "id": "orders-approve",
            "owner": "backend",
            "unit_id": "backend:endpoint:orders_api:orders.approve",
            "status": "failed",
            "change_scope": [{"operation": "modify", "path": "backend/OrdersController.java"}],
            "allowed_paths": ["backend/OrdersController.java"],
            "source_refs": {
                "authorization": {
                    "endpoints": [{"endpointId": "orders.approve", "operationResourceKeys": ["orders_approve"]}],
                    "authConstants": [{"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"}],
                }
            },
        }
        result = {
            "task_id": "orders-approve",
            "status": "failed",
            "failure_category": "acceptance_verification_failed",
            "scheduler_decision": {"action": "repair"},
        }
        plan = create_build_failure_repair_plan(
            failed_results=[result],
            tasks=[task],
            repair_planner=lambda _input: {
                "decision": "repair",
                "strategy": "仅修复注解",
                "repair_tasks": [{"change_scope": [{"path": "backend/OrdersController.java"}]}],
            },
        )

        self.assertEqual(plan["tasks"][0]["unit_id"], task["unit_id"])
        self.assertEqual(plan["tasks"][0]["source_refs"]["authorization"], task["source_refs"]["authorization"])

    def test_formal_source_change_requires_dag_replan_instead_of_repair(self) -> None:
        """正式产物哈希变化时必须终止当前 Repair，并要求重新生成 Build DAG。"""

        plan = create_build_failure_repair_plan(
            failed_results=[
                {
                    "task_id": "api",
                    "status": "failed",
                    "failure_category": "business_acceptance_blocked",
                    "scheduler_decision": {"action": "repair"},
                    "business_acceptance_evidence": [
                        {
                            "status": "blocked",
                            "evidence": "正式来源 orders.list 哈希已变化。",
                            "facts": {"reason_code": "formal_source_changed"},
                        }
                    ],
                }
            ],
            tasks=[
                {
                    "id": "api",
                    "owner": "frontend",
                    "change_scope": [{"operation": "modify", "path": "src/api.ts"}],
                    "allowed_paths": ["src/api.ts"],
                }
            ],
        )

        self.assertEqual(plan["decision"], "terminal_failure")
        self.assertEqual(plan["tasks"], [])
        self.assertEqual(plan["terminal_failures"][0]["failure_handling"], "replan_build_dag")

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

if __name__ == "__main__":
    unittest.main()
