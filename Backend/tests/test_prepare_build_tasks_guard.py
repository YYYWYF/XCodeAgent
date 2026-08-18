from __future__ import annotations

from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.nodes.tasks import prepare_build_tasks
from app.services.build_task_planner import (
    create_build_task_plan,
    replace_build_task_plan_tasks,
    tasks_from_build_task_plan,
)
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from tests.entity_design_test_utils import confirm_entity_designs


def _externalize_detail_designs(workspace: str, project_plan: dict) -> str:
    """把测试 PageDetail/EndpointDetail 写成独立文件并返回主计划路径。"""

    workspace_root = Path(workspace)
    plan_path = workspace_root / ".xcodeagent/plans/project-plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    page_details = list(project_plan.get("page_detail_plans", []))
    for detail in page_details:
        page_id = str(detail.get("pageId") or "")
        if not page_id:
            continue
        detail = {"status": "confirmed", **detail}
        detail_path = workspace_root / f".xcodeagent/plans/pages/page--{page_id}.json"
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_text(json.dumps(detail), encoding="utf-8")
        for page in project_plan.get("frontend_pages", []):
            if isinstance(page, dict) and str(page.get("pageId") or "") == page_id:
                page["detail_design"] = {
                    "status": "confirmed",
                    "json_path": f".xcodeagent/plans/pages/page--{page_id}.json",
                    "sha256": f"sha-page-{page_id}",
                }
    supplied_endpoint_details = {
        (
            str(detail.get("api_contract_id") or ""),
            str(detail.get("endpoint_id") or ""),
        ): detail
        for detail in project_plan.get("endpoint_detail_plans", [])
        if isinstance(detail, dict)
    }
    for contract in project_plan.get("api_contracts", []):
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "")
        source_id = str(contract.get("data_source_id") or "")
        for endpoint in contract.get("endpoints", []) or []:
            if not isinstance(endpoint, dict):
                continue
            endpoint_id = str(endpoint.get("id") or "")
            detail = {
                "api_contract_id": contract_id,
                "endpoint_id": endpoint_id,
                "data_source_id": source_id,
                "status": "confirmed",
                "data_origin": {
                    "source_type": "static",
                    "effective_source": {"kind": "frontend_mock"},
                },
                **supplied_endpoint_details.get((contract_id, endpoint_id), {}),
            }
            detail_path = workspace_root / (
                ".xcodeagent/plans/endpoints/"
                f"endpoint--{contract_id}--{endpoint_id}.json"
            )
            detail_path.parent.mkdir(parents=True, exist_ok=True)
            detail_path.write_text(json.dumps(detail), encoding="utf-8")
            endpoint["detail_design"] = {
                "status": "confirmed",
                "json_path": (
                    ".xcodeagent/plans/endpoints/"
                    f"endpoint--{contract_id}--{endpoint_id}.json"
                ),
                "sha256": f"sha-endpoint-{contract_id}-{endpoint_id}",
            }
    plan_path.write_text(json.dumps(project_plan), encoding="utf-8")
    return str(plan_path)


def _with_confirmed_designs(plan: dict, *, source_type: str = "database") -> dict:
    """去掉计划级 data_source 残留并把实体标记为已确认设计，供构建任务测试使用。"""

    updated = deepcopy(plan)
    updated.pop("data_source_detail_plans", None)
    for entity in updated.get("entities") or []:
        if isinstance(entity, dict):
            entity.pop("data_source", None)
    for contract in updated.get("api_contracts") or []:
        if isinstance(contract, dict):
            contract.pop("data_source_id", None)
    return confirm_entity_designs(updated, source_type=source_type)


def _database_planning_context() -> dict:
    """构造最小可用的数据库规划上下文，模拟前置数据库检查节点已完成。"""

    return {
        "schema_version": "database-context.v1",
        "status": "completed",
        "connection": {"status": "connected", "database": "sales"},
        "actual_schema": {
            "database": "sales",
            "database_exists": True,
            "tables": [],
        },
        "required_schema": {
            "database": "sales",
            "tables": [],
            "resolution_items": [],
        },
        "gaps": [],
        "resolution_items": [],
        "task_intents": [],
        "targets": [],
        "summary": "数据库上下文检查完成。",
    }


class PrepareBuildTasksGuardTests(unittest.TestCase):
    def test_page_scope_prepares_only_direct_units_and_context(self) -> None:
        """页面 scope 只编译当前页面、直接数据源和必要公共 Unit 的叶子任务。"""

        project_plan = {
            "version": "1.0.0",
            "confirmation_status": "confirmed",
            "frontend_pages": [
                {
                    "pageId": "orders",
                    "detail_design": {"status": "confirmed", "json_path": "pages/orders.json"},
                },
                {
                    "pageId": "customers",
                    "detail_design": {"status": "confirmed", "json_path": "pages/customers.json"},
                },
            ],
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                    "data_source": "database",
                },
                {
                    "id": "Customer",
                    "name": "Customer",
                    "fields": [],
                    "data_source": "database",
                },
            ],
            "api_contracts": [
                {"id": "orders-api", "entity_ids": ["Order"], "data_source_id": "database", "endpoints": [{"id": "orders.list"}]},
                {"id": "customers-api", "entity_ids": ["Customer"], "data_source_id": "database", "endpoints": [{"id": "customers.list"}]},
            ],
            "page_detail_plans": [
                {"pageId": "orders", "references": {"endpoint_dependencies": [{"endpoint_id": "orders.list"}]}},
                {"pageId": "customers", "references": {"endpoint_dependencies": [{"endpoint_id": "customers.list"}]}},
            ],
            "data_source_detail_plans": [
                {"data_source_id": "database"},
                {"data_source_id": "database"},
            ],
        }
        project_plan = _with_confirmed_designs(project_plan)
        agent_plan = create_build_task_plan(
            project_plan,
            agent_plan={
                "tasks": [
                    {
                        "id": "orders-api-task",
                        "unit_id": "backend:endpoint:orders-api:orders.list",
                        "owner": "backend",
                        "description": "实现订单接口",
                        "change_scope": [{"path": "api/orders.py"}],
                    },
                    {
                        "id": "orders-page-task",
                        "unit_id": "page:orders",
                        "owner": "frontend",
                        "description": "实现订单页面",
                        "dependencies": ["orders-api-task"],
                        "change_scope": [{"path": "src/pages/Orders.tsx"}],
                    },
                ]
            },
        )

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=agent_plan,
        ) as preparer:
            state_project_plan = deepcopy(project_plan)
            project_plan_path = _externalize_detail_designs(workspace, project_plan)
            result = prepare_build_tasks(
                {
                    "request": "生成订单页面",
                    "workspace": workspace,
                    "project_plan": state_project_plan,
                    "project_plan_json_path": project_plan_path,
                    "database_planning_context": _database_planning_context(),
                    "build_execution_scope": {"type": "page", "targetId": "orders"},
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["build_execution_scope"], {"type": "page", "targetId": "orders"})
        self.assertEqual(
            set(result["build_task_plan"]["task_registry"]),
            {"orders-api-task", "orders-page-task"},
        )
        self.assertEqual(result["build_task_plan"]["build_units"]["page:customers"]["status"], "not_prepared")
        prepared_project_plan = preparer.call_args.args[0]
        self.assertNotIn("frontend_pages", prepared_project_plan)
        self.assertNotIn("page_detail_plans", prepared_project_plan)
        self.assertEqual(
            [page["pageId"] for page in prepared_project_plan["application_skeleton"]["pages"]],
            ["orders", "customers"],
        )
        executable_details = prepared_project_plan["executable_details"]
        self.assertEqual([detail["pageId"] for detail in executable_details["page_detail_plans"]], ["orders"])
        self.assertEqual(
            [detail["endpoint_id"] for detail in executable_details["endpoint_detail_plans"]],
            ["orders.list"],
        )
        self.assertEqual(
            [source["id"] for source in executable_details["data_sources"]],
            ["database"],
        )
        self.assertEqual(
            [
                endpoint["id"]
                for contract in executable_details["api_contracts"]
                for endpoint in contract["endpoints"]
            ],
            ["orders.list"],
        )

        customer_agent_plan = create_build_task_plan(
            project_plan,
            agent_plan={
                "tasks": [
                    {
                        "id": "customers-api-task",
                        "unit_id": "backend:endpoint:customers-api:customers.list",
                        "owner": "backend",
                        "description": "实现客户接口",
                        "change_scope": [{"path": "api/customers.py"}],
                    },
                    {
                        "id": "customers-page-task",
                        "unit_id": "page:customers",
                        "owner": "frontend",
                        "description": "实现客户页面",
                        "dependencies": ["customers-api-task"],
                        "change_scope": [{"path": "src/pages/Customers.tsx"}],
                    },
                ]
            },
        )
        first_plan = result["build_task_plan"]
        first_plan["build_units"]["frontend:shell"]["task_ids"] = ["shared-shell-task"]
        first_plan["task_registry"]["shared-shell-task"] = {
            "id": "shared-shell-task",
            "unit_id": "frontend:shell",
            "owner": "frontend",
            "status": "completed",
            "dependencies": [],
            "change_scope": [],
        }
        first_plan["task_graph"]["topological_order"].append("shared-shell-task")
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=customer_agent_plan,
        ) as customer_preparer:
            project_plan_path = _externalize_detail_designs(workspace, project_plan)
            customer_result = prepare_build_tasks(
                {
                    "request": "生成客户页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
                    "database_planning_context": _database_planning_context(),
                    "build_task_plan": first_plan,
                    "build_execution_scope": {"type": "page", "targetId": "customers"},
                    "timeline": [],
                }
            )

        self.assertIn("shared-shell-task", customer_result["build_task_plan"]["task_registry"])
        self.assertIn("orders-page-task", customer_result["build_task_plan"]["task_registry"])
        customer_context = customer_preparer.call_args.kwargs["build_context"]
        self.assertEqual(customer_context["reusable_tasks_by_unit"]["frontend:shell"], ["shared-shell-task"])

    def test_page_scope_rejects_out_of_scope_unit_tasks(self) -> None:
        """页面 scope 模型若返回其他页面 Unit，必须阻止而不是扩展当前 DAG。"""

        project_plan = {
            "version": "1.0.0",
            "confirmation_status": "confirmed",
            "frontend_pages": [
                {
                    "pageId": "orders",
                    "detail_design": {"status": "confirmed", "json_path": "pages/orders.json"},
                },
                {
                    "pageId": "dashboard",
                    "detail_design": {"status": "pending", "json_path": "pages/dashboard.json"},
                },
            ],
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                    "data_source": "database",
                }
            ],
            "api_contracts": [
                {"id": "orders-api", "entity_ids": ["Order"], "data_source_id": "database", "endpoints": [{"id": "orders.list"}]},
            ],
            "page_detail_plans": [
                {"pageId": "orders", "references": {"endpoint_dependencies": [{"endpoint_id": "orders.list"}]}},
            ],
            "data_source_detail_plans": [{"data_source_id": "database"}],
        }
        project_plan = _with_confirmed_designs(project_plan)
        agent_plan = create_build_task_plan(
            project_plan,
            agent_plan={
                "tasks": [
                    {
                        "id": "dashboard-page-task",
                        "unit_id": "page:dashboard",
                        "owner": "frontend",
                        "description": "错误生成首页任务",
                        "change_scope": [{"path": "src/pages/Dashboard.tsx"}],
                    }
                ]
            },
        )

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=agent_plan,
        ):
            project_plan_path = _externalize_detail_designs(workspace, project_plan)
            result = prepare_build_tasks(
                {
                    "request": "生成订单页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
                    "database_planning_context": _database_planning_context(),
                    "build_execution_scope": {"type": "page", "targetId": "orders"},
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "build_task_plan_generation_error")
        self.assertIn("page:dashboard", result["clarification"]["error"])
        self.assertEqual(
            next(
                stage
                for stage in result["dag_generation_progress"]["stages"]
                if stage["id"] == "task_compilation"
            )["status"],
            "failed",
        )

    def test_page_scope_renames_model_task_ids_that_conflict_with_retained_units(self) -> None:
        """页面 scope 模型复用其他 Unit 的任务 ID 时应重命名而不是失败。"""

        project_plan = {
            "version": "1.0.0",
            "confirmation_status": "confirmed",
            "frontend_pages": [
                {
                    "pageId": "orders",
                    "detail_design": {
                        "status": "confirmed",
                        "json_path": "pages/orders.json",
                    },
                }
            ],
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                    "data_source": "database",
                }
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "entity_ids": ["Order"],
                    "data_source_id": "database",
                    "endpoints": [{"id": "orders.list"}],
                }
            ],
            "page_detail_plans": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "orders.list"}]
                    },
                }
            ],
            "data_source_detail_plans": [{"data_source_id": "database"}],
        }
        project_plan = _with_confirmed_designs(project_plan)
        base_plan = ensure_build_unit_skeleton(project_plan, {}, {})
        base_plan = replace_build_task_plan_tasks(
            base_plan,
            [
                {
                    "id": "shared-api-client-task",
                    "unit_id": "frontend:api-client",
                    "owner": "frontend",
                    "status": "completed",
                    "dependencies": [],
                    "change_scope": [],
                }
            ],
        )
        agent_plan = create_build_task_plan(
            project_plan,
            agent_plan={
                "tasks": [
                    {
                        "id": "shared-api-client-task",
                        "unit_id": "backend:endpoint:orders-api:orders.list",
                        "owner": "backend",
                        "description": "实现订单接口",
                        "change_scope": [{"path": "api/orders.py"}],
                    },
                    {
                        "id": "orders-page-task",
                        "unit_id": "page:orders",
                        "owner": "frontend",
                        "description": "实现订单页面",
                        "dependencies": ["shared-api-client-task"],
                        "change_scope": [{"path": "src/pages/Orders.tsx"}],
                    },
                ]
            },
        )

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=agent_plan,
        ):
            project_plan_path = _externalize_detail_designs(workspace, project_plan)
            result = prepare_build_tasks(
                {
                    "request": "生成订单页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
                    "build_task_plan": base_plan,
                    "database_planning_context": _database_planning_context(),
                    "build_execution_scope": {"type": "page", "targetId": "orders"},
                    "timeline": [],
                }
            )

        task_registry = result["build_task_plan"]["task_registry"]
        self.assertEqual(result["status"], "completed")
        self.assertIn("shared-api-client-task", task_registry)
        self.assertIn(
            "backend-endpoint-orders-api-orders-list--shared-api-client-task",
            task_registry,
        )
        self.assertNotIn(
            "backend-endpoint-orders-api-orders-list--shared-api-client-task",
            task_registry["orders-page-task"]["dependencies"],
        )
        self.assertIn(
            "shared-api-client-task",
            task_registry["orders-page-task"]["dependencies"],
        )

    def test_page_scope_reuses_prepared_app_and_endpoint_units(self) -> None:
        """页面 scope 不应追加已准备公共 Unit 或 endpoint Unit 的模型新任务。"""

        project_plan = {
            "version": "1.0.0",
            "confirmation_status": "confirmed",
            "frontend_pages": [
                {
                    "pageId": "orders",
                    "detail_design": {
                        "status": "confirmed",
                        "json_path": "pages/orders.json",
                    },
                },
                {
                    "pageId": "orderReports",
                    "detail_design": {
                        "status": "confirmed",
                        "json_path": "pages/order-reports.json",
                    },
                },
            ],
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                    "data_source": "database",
                }
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "entity_ids": ["Order"],
                    "data_source_id": "database",
                    "endpoints": [{"id": "orders.list"}],
                }
            ],
            "page_detail_plans": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "orders.list"}]
                    },
                },
                {
                    "pageId": "orderReports",
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "orders.list"}]
                    },
                },
            ],
            "data_source_detail_plans": [{"data_source_id": "database"}],
        }
        project_plan = _with_confirmed_designs(project_plan)
        first_agent_plan = create_build_task_plan(
            project_plan,
            agent_plan={
                "tasks": [
                    {
                        "id": "orders-api-task",
                        "unit_id": "backend:endpoint:orders-api:orders.list",
                        "owner": "backend",
                        "description": "实现订单接口",
                        "change_scope": [{"path": "api/orders.py"}],
                    },
                    {
                        "id": "orders-page-task",
                        "unit_id": "page:orders",
                        "owner": "frontend",
                        "description": "实现订单页面",
                        "dependencies": ["orders-api-task"],
                        "change_scope": [{"path": "src/pages/Orders.tsx"}],
                    },
                ]
            },
        )
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=first_agent_plan,
        ):
            project_plan_path = _externalize_detail_designs(workspace, project_plan)
            first_result = prepare_build_tasks(
                {
                    "request": "生成订单页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
                    "database_planning_context": _database_planning_context(),
                    "build_execution_scope": {"type": "page", "targetId": "orders"},
                    "timeline": [],
                }
            )
        first_plan = first_result["build_task_plan"]
        shared_api_client_task = {
            "id": "shared-api-client-task",
            "unit_id": "frontend:api-client",
            "owner": "frontend",
            "status": "completed",
            "dependencies": [],
            "change_scope": [],
        }
        first_plan = replace_build_task_plan_tasks(
            first_plan,
            [*tasks_from_build_task_plan(first_plan), shared_api_client_task],
        )
        second_agent_plan = create_build_task_plan(
            project_plan,
            agent_plan={
                "tasks": [
                    {
                        "id": "duplicate-api-client-task",
                        "unit_id": "frontend:api-client",
                        "owner": "frontend",
                        "description": "重复生成公共 API client",
                        "change_scope": [{"path": "src/api/client.ts"}],
                    },
                    {
                        "id": "duplicate-orders-api-task",
                        "unit_id": "backend:endpoint:orders-api:orders.list",
                        "owner": "backend",
                        "description": "重复生成订单接口",
                        "change_scope": [{"path": "api/orders.py"}],
                    },
                    {
                        "id": "order-reports-page-task",
                        "unit_id": "page:orderReports",
                        "owner": "frontend",
                        "description": "实现订单报表页面",
                        "dependencies": [
                            "duplicate-api-client-task",
                            "duplicate-orders-api-task",
                        ],
                        "change_scope": [{"path": "src/pages/OrderReports.tsx"}],
                    },
                ]
            },
        )

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=second_agent_plan,
        ):
            project_plan_path = _externalize_detail_designs(workspace, project_plan)
            second_result = prepare_build_tasks(
                {
                    "request": "生成订单报表页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
                    "build_task_plan": first_plan,
                    "database_planning_context": _database_planning_context(),
                    "build_execution_scope": {
                        "type": "page",
                        "targetId": "orderReports",
                    },
                    "timeline": [],
                }
            )

        task_registry = second_result["build_task_plan"]["task_registry"]
        self.assertEqual(second_result["status"], "completed")
        self.assertIn("shared-api-client-task", task_registry)
        self.assertIn("orders-api-task", task_registry)
        self.assertNotIn("duplicate-api-client-task", task_registry)
        self.assertNotIn("duplicate-orders-api-task", task_registry)
        self.assertIn(
            "shared-api-client-task",
            task_registry["order-reports-page-task"]["dependencies"],
        )
        self.assertNotIn(
            "orders-api-task",
            task_registry["order-reports-page-task"]["dependencies"],
        )

    def test_prepare_build_tasks_waits_when_project_plan_is_unconfirmed(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "pending_user_confirmation"

        with patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            side_effect=AssertionError("must not prepare tasks before confirmation"),
        ):
            result = prepare_build_tasks(
                {
                    "request": "创建一个库存管理系统",
                    "project_plan": project_plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "project_plan_confirmation")
        self.assertEqual(result["phase"], "prepare_build_tasks")

    def test_prepare_build_tasks_continues_after_user_confirms_project_plan(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "pending_user_confirmation"

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
                return_value={
                    "tasks": [],
                    "summary": {"total": 0},
                },
            ) as preparer:
                result = prepare_build_tasks(
                    {
                        "request": "正确，继续",
                        "workspace": workspace,
                        "project_plan": project_plan,
                        "timeline": [],
                    }
                )

        self.assertEqual(preparer.call_args.args[0]["confirmation_status"], "confirmed")
        self.assertEqual(preparer.call_args.kwargs["workspace"], workspace)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")
        self.assertEqual(result["tasks"], [])

    def test_prepare_build_tasks_uses_model_output_and_does_not_report_code_changes(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "confirmed"

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
                return_value={
                    "tasks": [],
                    "summary": {"total": 0},
                    "prepared_by": {"mode": "direct"},
                },
            ):
                result = prepare_build_tasks(
                    {
                        "request": "开始任务拆分",
                        "workspace": workspace,
                        "project_plan": project_plan,
                        "timeline": [],
                    }
                )
                dag_path = result["build_task_dag_path"]
                with open(dag_path, encoding="utf-8") as dag_file:
                    dag_content = dag_file.read()

        self.assertNotIn("code_changes", result)
        self.assertNotIn("code_change_sets", result)
        self.assertEqual(result["build_task_plan"]["prepared_by"]["mode"], "direct")
        self.assertTrue(
            dag_path.replace("\\", "/").endswith(
                ".xcodeagent/plans/BUILD_TASK_DAG.md"
            )
        )
        self.assertIn("# Build Task DAG", dag_content)
        self.assertTrue(
            all(
                stage["status"] == "completed"
                for stage in result["dag_generation_progress"]["stages"]
            )
        )

    def test_prepare_build_tasks_confirmation_ignores_question_text_negative_words(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "pending_user_confirmation"
        continuation_message = "\n".join(
            [
                "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
                "",
                "原始需求：",
                "创建一个库存管理系统",
                "",
                "用户补充确认：",
                "- 计划确认：代码生成即将开始，但当前 ProjectPlan 尚未由用户确认。请确认项目规划书是否正确。正确请回复“正确，继续”；如需调整，请说明要修改的架构、API、页面、数据源、权限或验收标准。",
                "  回答：正确，继续",
            ]
        )

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
                return_value={
                    "tasks": [],
                    "summary": {"total": 0},
                },
            ) as preparer:
                result = prepare_build_tasks(
                    {
                        "request": continuation_message,
                        "workspace": workspace,
                        "project_plan": project_plan,
                        "timeline": [],
                    }
                )

        self.assertEqual(preparer.call_args.args[0]["confirmation_status"], "confirmed")
        self.assertEqual(preparer.call_args.kwargs["workspace"], workspace)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")

    def test_prepare_build_tasks_blocks_inconsistent_api_contract(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "confirmed"
        project_plan["api_contracts"][0]["entity_ids"] = ["Unknown"]

        with patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            side_effect=AssertionError("must not generate tasks with contract drift"),
        ):
            result = prepare_build_tasks(
                {
                    "request": "开始任务拆分",
                    "project_plan": project_plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "api_contract_consistency_error",
        )
        self.assertTrue(result["clarification"]["errors"])
        self.assertEqual(
            next(
                stage
                for stage in result["dag_generation_progress"]["stages"]
                if stage["id"] == "contract_validation"
            )["status"],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
