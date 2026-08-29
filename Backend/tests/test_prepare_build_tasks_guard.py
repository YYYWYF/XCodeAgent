from __future__ import annotations

from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.nodes.tasks import (
    _build_task_plan_confirmation_payload,
    _build_prerequisite_errors,
    _handle_build_task_plan_confirmation,
    _latest_project_plan,
    prepare_build_tasks,
)
from app.services.build_task_planner import (
    create_build_task_plan,
    replace_build_task_plan_tasks,
    tasks_from_build_task_plan,
)
from app.services.artifact_invalidation import canonical_sha256
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.entity_definitions import confirmed_entity_designs
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import (
    load_project_plan_json,
    write_project_plan_document,
)
from tests.entity_design_test_utils import confirm_entity_designs


def _write_current_plan(workspace: str, project_plan: dict) -> str:
    """把当前 TechnicalPlan 测试夹具写入正式 JSON 路径。"""

    workspace_root = Path(workspace)
    plan_path = workspace_root / ".xcodeagent/plans/project-plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(project_plan), encoding="utf-8")
    return str(plan_path)


def _with_confirmed_designs(plan: dict, *, source_type: str = "database") -> dict:
    """为当前实体事实源补齐已确认设计，供构建任务测试使用。"""

    return confirm_entity_designs(deepcopy(plan), source_type=source_type)


def _page_implementation_contract(page_id: str, endpoint_ids: list[str]) -> dict:
    """构造当前 TechnicalPlan 页面实现契约测试夹具。"""

    return {
        "schema_version": "page-implementation-contract.v1",
        "pageId": page_id,
        "uiDesignRef": {"path": f".xcodeagent/ui-design/pages/{page_id}.tsx"},
        "requiredEndpointIds": endpoint_ids,
    }


def _write_formal_build_artifacts(
    workspace: str,
    *,
    technical_status: str = "confirmed",
    include_technical_plan: bool = True,
) -> None:
    """写入 Build 门禁需要的最小正式产物集合。"""

    workspace_root = Path(workspace)
    payloads = {
        ".xcodeagent/specs/requirement-spec.json": {
            "confirmation_status": "confirmed",
        },
        ".xcodeagent/plans/product-plan.json": {
            "confirmation_status": "confirmed",
        },
        ".xcodeagent/specs/ui-designs.json": {
            "confirmation_status": "skipped",
        },
    }
    if include_technical_plan:
        payloads[".xcodeagent/plans/technical-plan.json"] = {
            "artifact_type": "technical-plan",
            "confirmation_status": technical_status,
        }
    for relative_path, payload in payloads.items():
        path = workspace_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


class PrepareBuildTasksGuardTests(unittest.TestCase):
    def test_build_gate_ignores_removed_endpoint_detail_artifacts(self) -> None:
        """当前 Build 门禁不得重新依赖已移除的 EndpointDetail 产物。"""

        technical_plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "confirmed",
            "api_contracts": [
                {
                    "id": "orders-api",
                    "endpoints": [{"id": "orders.list"}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.inspect_template_generation_readiness",
            return_value={"errors": []},
        ):
            _write_formal_build_artifacts(workspace)
            technical_path = Path(workspace) / ".xcodeagent/plans/technical-plan.json"
            technical_path.write_text(json.dumps(technical_plan), encoding="utf-8")
            endpoint_path = (
                Path(workspace)
                / ".xcodeagent/plans/endpoints/endpoint--orders-api--orders-list.json"
            )
            endpoint_path.parent.mkdir(parents=True, exist_ok=True)
            endpoint_path.write_text(
                json.dumps(
                    {
                        "status": "stale",
                        "confirmation_status": "stale",
                        "basedOn": [
                            {"artifactKey": "technical-plan", "sha256": "0" * 64}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "build_execution_scope": {
                    "type": "endpoint",
                    "targetId": "orders.list",
                    "apiContractId": "orders-api",
                }
            }
            stale_errors = _build_prerequisite_errors(
                state,
                technical_plan,
                workspace=workspace,
            )
            endpoint_path.write_text(
                json.dumps(
                    {
                        "status": "confirmed",
                        "confirmation_status": "confirmed",
                        "basedOn": [
                            {"artifactKey": "technical-plan", "sha256": "0" * 64}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            mismatch_errors = _build_prerequisite_errors(
                state,
                technical_plan,
                workspace=workspace,
            )
            endpoint_path.write_text(
                json.dumps(
                    {
                        "status": "confirmed",
                        "confirmation_status": "confirmed",
                        "basedOn": [
                            {
                                "artifactKey": "technical-plan",
                                "sha256": canonical_sha256(technical_path),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            confirmed_errors = _build_prerequisite_errors(
                state,
                technical_plan,
                workspace=workspace,
            )

        for errors in (stale_errors, mismatch_errors, confirmed_errors):
            self.assertFalse(any("endpoint-detail:" in error for error in errors))

    def test_page_endpoint_gate_ignores_unrelated_stale_endpoint(self) -> None:
        """页面 Build 只门禁直接依赖接口，不被无关 endpoint 的 stale 状态阻断。"""

        technical_plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "confirmed",
            "pages": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [
                            {
                                "api_contract_id": "orders-api",
                                "endpoint_id": "orders.list",
                            }
                        ]
                    },
                }
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "endpoints": [
                        {"id": "orders.list"},
                        {"id": "customers.list"},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.inspect_template_generation_readiness",
            return_value={"errors": []},
        ):
            _write_formal_build_artifacts(workspace)
            technical_path = Path(workspace) / ".xcodeagent/plans/technical-plan.json"
            technical_path.write_text(json.dumps(technical_plan), encoding="utf-8")
            endpoint_dir = Path(workspace) / ".xcodeagent/plans/endpoints"
            endpoint_dir.mkdir(parents=True, exist_ok=True)
            endpoint_dir.joinpath("endpoint--orders-api--orders-list.json").write_text(
                json.dumps(
                    {
                        "status": "confirmed",
                        "confirmation_status": "confirmed",
                        "basedOn": [
                            {
                                "artifactKey": "technical-plan",
                                "sha256": canonical_sha256(technical_path),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            endpoint_dir.joinpath("endpoint--orders-api--customers-list.json").write_text(
                json.dumps({"status": "stale", "confirmation_status": "stale"}),
                encoding="utf-8",
            )
            errors = _build_prerequisite_errors(
                {
                    "build_execution_scope": {
                        "type": "page",
                        "targetId": "orders",
                    }
                },
                technical_plan,
                workspace=workspace,
            )

        self.assertFalse(any("endpoint-detail:" in error for error in errors))

    def test_prerequisite_gate_reads_workspace_plan_over_stale_checkpoint(self) -> None:
        """正式 TechnicalPlan 已确认时，不应被 checkpoint 的 pending 状态阻断。"""

        state = {
            "requirement_spec": {"confirmation_status": "confirmed"},
            "product_plan": {"confirmation_status": "confirmed"},
            "ui_designs": {"confirmation_status": "skipped"},
            "technical_plan": {
                "artifact_type": "technical-plan",
                "confirmation_status": "pending_user_confirmation",
            },
        }
        project_plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "confirmed",
        }

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.inspect_template_generation_readiness",
            return_value={"errors": []},
        ):
            _write_formal_build_artifacts(workspace)
            errors = _build_prerequisite_errors(
                state,
                project_plan,
                workspace=workspace,
            )

        self.assertNotIn("TechnicalPlan 缺失、类型不正确或未确认。", errors)

    def test_prerequisite_gate_does_not_fallback_to_checkpoint_when_plan_is_missing(self) -> None:
        """正式 TechnicalPlan 缺失时，即使 checkpoint 已确认也必须阻断。"""

        state = {
            "technical_plan": {
                "artifact_type": "technical-plan",
                "confirmation_status": "confirmed",
            },
        }
        project_plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "confirmed",
        }

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.inspect_template_generation_readiness",
            return_value={"errors": []},
        ):
            _write_formal_build_artifacts(workspace, include_technical_plan=False)
            errors = _build_prerequisite_errors(
                state,
                project_plan,
                workspace=workspace,
            )

        self.assertIn("TechnicalPlan 缺失、类型不正确或未确认。", errors)

    def test_prepare_build_tasks_syncs_formal_artifacts_into_blocked_state(self) -> None:
        """门禁阻断时也要把正式产物状态写回 checkpoint，避免旧快照继续残留。"""

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks.inspect_template_generation_readiness",
            return_value={"errors": ["模板 manifest 尚未就绪。"]},
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            side_effect=AssertionError("formal artifact gate must block before task generation"),
        ):
            _write_formal_build_artifacts(workspace)
            result = prepare_build_tasks(
                {
                    "workspace": workspace,
                    "project_plan": {
                        "artifact_type": "technical-plan",
                        "confirmation_status": "confirmed",
                    },
                    "technical_plan": {
                        "artifact_type": "technical-plan",
                        "confirmation_status": "pending_user_confirmation",
                    },
                    "timeline": [],
                }
            )
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["technical_plan"]["confirmation_status"], "confirmed")

    def test_confirm_repairs_missing_generated_dag_status(self) -> None:
        """确认当前 build-dag.v3 时应修复生成阶段漏写的顶层 status。"""

        scope = {"type": "page", "targetId": "home"}
        plan = {
            "schema_version": "build-dag.v3",
            "task_graph": {"validation": {"is_valid": True, "errors": []}},
            "execution": {"batches": [{"mode": "serial", "tasks": []}]},
            "task_registry": {},
            "confirmation_status": "pending",
            "build_execution_scope": scope,
        }

        with tempfile.TemporaryDirectory() as workspace:
            plan_path = Path(workspace) / ".xcodeagent/plans/build-task-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = _handle_build_task_plan_confirmation(
                {
                    "workspace": workspace,
                    "build_task_plan_confirmation": {
                        "mode": "build_task_plan_confirmation",
                        "action": "confirm",
                    },
                },
                {"artifact_type": "technical-plan", "confirmation_status": "confirmed"},
                scope,
            )
            persisted = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["build_task_plan"]["status"], "ready")
        self.assertEqual(persisted["status"], "ready")
        self.assertEqual(persisted["confirmation_status"], "confirmed")

    def test_build_task_plan_confirmation_exposes_only_supported_actions(self) -> None:
        """DAG 确认卡只暴露确认、局部修改和重新生成动作。"""

        payload = _build_task_plan_confirmation_payload(
            {"confirmation_status": "pending"},
            {"type": "application", "targetId": "application"},
        )

        self.assertEqual(payload["actionValues"], ["confirm", "patch", "regenerate"])

    def test_confirmed_plan_from_other_page_does_not_bypass_generation(self) -> None:
        """切换页面后不得把上一页已确认 DAG 当作当前页计划直接进入 Build。"""

        old_scope = {"type": "page", "targetId": "orders"}
        current_scope = {"type": "page", "targetId": "customers"}
        plan = {
            "schema_version": "build-dag.v3",
            "status": "ready",
            "task_graph": {"validation": {"is_valid": True, "errors": []}},
            "execution": {"batches": []},
            "task_registry": {},
            "confirmation_status": "confirmed",
            "build_execution_scope": old_scope,
        }

        with tempfile.TemporaryDirectory() as workspace:
            plan_path = Path(workspace) / ".xcodeagent/plans/build-task-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = _handle_build_task_plan_confirmation(
                {"workspace": workspace},
                {"artifact_type": "technical-plan", "confirmation_status": "confirmed"},
                current_scope,
            )

        self.assertIsNone(result)

    def test_same_application_scope_legacy_plan_does_not_bypass_generation(self) -> None:
        """同为 application scope 时，缺少当前任务字段的旧 DAG 也必须重新生成。"""

        scope = {"type": "application", "targetId": "application"}
        plan = {
            "schema_version": "build-dag.v3",
            "status": "ready",
            "task_graph": {
                "nodes": ["old-orders-task"],
                "topological_order": ["old-orders-task"],
                "validation": {"is_valid": True, "errors": []},
            },
            "execution": {"batches": []},
            "task_registry": {
                "old-orders-task": {
                    "id": "old-orders-task",
                    "unit_id": "page:orders",
                    "owner": "frontend",
                    "target_files": ["src/pages/Orders/index.tsx"],
                }
            },
            "confirmation_status": "pending",
            "build_execution_scope": scope,
        }

        with tempfile.TemporaryDirectory() as workspace:
            plan_path = Path(workspace) / ".xcodeagent/plans/build-task-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = _handle_build_task_plan_confirmation(
                {"workspace": workspace},
                {"artifact_type": "technical-plan", "confirmation_status": "confirmed"},
                scope,
            )

        self.assertIsNone(result)

    def test_same_application_scope_stale_unit_fingerprint_does_not_bypass_generation(self) -> None:
        """任务字段完整但 Unit 输入指纹过期时，也不能复用旧 application DAG。"""

        scope = {"type": "application", "targetId": "application"}
        plan = {
            "schema_version": "build-dag.v3",
            "status": "ready",
            "task_graph": {
                "nodes": ["old-orders-task"],
                "topological_order": ["old-orders-task"],
                "validation": {"is_valid": True, "errors": []},
            },
            "execution": {"batches": []},
            "task_registry": {
                "old-orders-task": {
                    "id": "old-orders-task",
                    "unit_id": "page:orders",
                    "owner": "frontend",
                    "deliverables": [],
                    "acceptance_checks": [],
                    "business_acceptance_checks": [],
                }
            },
            "unit_skeleton": {"input_fingerprint": "fingerprint-from-previous-plan"},
            "confirmation_status": "confirmed",
            "build_execution_scope": scope,
        }

        with tempfile.TemporaryDirectory() as workspace:
            plan_path = Path(workspace) / ".xcodeagent/plans/build-task-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = _handle_build_task_plan_confirmation(
                {"workspace": workspace},
                {"artifact_type": "technical-plan", "confirmation_status": "confirmed"},
                scope,
            )

        self.assertIsNone(result)

    def test_latest_project_plan_hydrates_confirmed_entity_designs(self) -> None:
        """Build 重读轻量计划时必须回填外置的已确认实体设计。"""

        project_plan = _with_confirmed_designs(
            create_project_plan(create_requirement_spec("创建商品管理系统"))
        )
        project_plan["confirmation_status"] = "confirmed"
        contract = next(
            item
            for item in project_plan["api_contracts"]
            if isinstance(item, dict) and item.get("entity_ids")
        )

        with tempfile.TemporaryDirectory() as workspace:
            state = {
                "workspace": workspace,
                "project_plan": project_plan,
            }
            write_project_plan_document(state, project_plan)
            plan_path = Path(workspace) / ".xcodeagent/plans/project-plan.json"
            state["project_plan_json_path"] = str(plan_path)

            compact_plan = load_project_plan_json(plan_path)
            hydrated_plan = _latest_project_plan(state)

        self.assertNotIn("entity_detail_plans", compact_plan)
        self.assertEqual(
            [
                detail["entity_id"]
                for detail in confirmed_entity_designs(hydrated_plan, contract)
            ],
            list(contract["entity_ids"]),
        )
        unconfirmed_plan = deepcopy(hydrated_plan)
        unconfirmed_id = str(contract["entity_ids"][0])
        for detail in unconfirmed_plan.get("entity_detail_plans", []):
            if str(detail.get("entity_id") or "") == unconfirmed_id:
                detail["status"] = "pending_user_confirmation"
        self.assertNotIn(
            unconfirmed_id,
            [
                detail["entity_id"]
                for detail in confirmed_entity_designs(unconfirmed_plan, contract)
            ],
        )

    def test_latest_technical_plan_rematerializes_page_contracts_from_formal_artifacts(self) -> None:
        """Build 重读正式 TechnicalPlan 后必须恢复运行时页面实现契约。"""

        requirement_spec = {
            "confirmation_status": "confirmed",
            "app_info": {"name": "人名", "summary": "录入并查看人名。"},
            "user_roles": [],
            "feature_modules": [],
            "acceptance_criteria": [],
        }
        product_plan = {
            "confirmation_status": "confirmed",
            "app": {"name": "人名", "summary": "录入并查看人名。"},
            "business_flows": [],
            "product_acceptance_criteria": [],
            "pages": [
                {
                    "pageId": "home",
                    "name": "首页",
                    "path": "/page/home",
                    "actions": [
                        {
                            "actionId": "export_names_excel",
                            "name": "导出 Excel",
                            "behavior": {"type": "business"},
                        }
                    ],
                    "navigation_targets": [],
                    "acceptance_criteria": [],
                }
            ],
        }
        ui_designs = {
            "confirmation_status": "skipped",
            "pages": [],
        }
        technical_plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "confirmed",
            "pages": [
                {
                    "pageId": "home",
                    "references": {
                        "endpoint_dependencies": [
                            {"endpoint_id": "person_name_api.export"},
                        ],
                        "action_implementations": [
                            {
                                "actionId": "export_names_excel",
                                "endpointId": "person_name_api.export",
                            }
                        ],
                    },
                }
            ],
            "api_contracts": [
                {
                    "id": "person_name_api",
                    "endpoints": [{"id": "person_name_api.export"}],
                }
            ],
            "entities": [],
        }

        with tempfile.TemporaryDirectory() as workspace:
            technical_path = Path(workspace) / ".xcodeagent/plans/technical-plan.json"
            technical_path.parent.mkdir(parents=True, exist_ok=True)
            technical_path.write_text(json.dumps(technical_plan), encoding="utf-8")
            latest_plan = _latest_project_plan(
                {
                    "workspace": workspace,
                    "project_plan": {
                        **technical_plan,
                        "page_implementation_contracts": [{"pageId": "stale"}],
                    },
                    "project_plan_json_path": str(technical_path),
                },
                formal_artifacts={
                    "requirement_spec": requirement_spec,
                    "product_plan": product_plan,
                    "ui_designs": ui_designs,
                    "technical_plan": technical_plan,
                },
            )

        self.assertNotIn("page_implementation_contracts", technical_plan)
        self.assertEqual(
            latest_plan["page_implementation_contracts"][0]["pageId"],
            "home",
        )
        self.assertEqual(
            latest_plan["page_implementation_contracts"][0]["requiredEndpointIds"],
            ["person_name_api.export"],
        )
        self.assertEqual(
            latest_plan["page_implementation_contracts"][0]["actionBindings"][0][
                "endpointId"
            ],
            "person_name_api.export",
        )

    def test_page_scope_prepares_only_direct_units_and_context(self) -> None:
        """页面 scope 只编译当前页面、直接数据源和必要公共 Unit 的叶子任务。"""

        project_plan = {
            "version": "1.0.0",
            "confirmation_status": "confirmed",
            "frontend_pages": [
                {"pageId": "orders"},
                {"pageId": "customers"},
            ],
            "page_implementation_contracts": [
                _page_implementation_contract("orders", ["orders.list"]),
                _page_implementation_contract("customers", ["customers.list"]),
            ],
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                },
                {
                    "id": "Customer",
                    "name": "Customer",
                    "fields": [],
                },
            ],
            "api_contracts": [
                {"id": "orders-api", "entity_ids": ["Order"], "endpoints": [{"id": "orders.list"}]},
                {"id": "customers-api", "entity_ids": ["Customer"], "endpoints": [{"id": "customers.list"}]},
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
                        "deliverables": [{"id": "controller:orders", "kind": "backend.endpoint_controller", "target_id": "orders.list", "paths": ["api/orders.py"], "provides": ["orders.endpoint"]}],
                        "change_scope": [{"path": "api/orders.py"}],
                    },
                    {
                        "id": "orders-page-task",
                        "unit_id": "page:orders",
                        "owner": "frontend",
                        "description": "实现订单页面",
                        "dependencies": ["orders-api-task"],
                        "deliverables": [{"id": "capability:orders-page", "kind": "frontend.shared_capability", "target_id": "orders", "paths": ["src/pages/Orders.tsx"], "provides": ["orders.page"]}],
                        "change_scope": [{"path": "src/pages/Orders.tsx"}],
                    },
                ]
            },
        )

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks._build_prerequisite_errors", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=agent_plan,
        ) as preparer:
            state_project_plan = deepcopy(project_plan)
            project_plan_path = _write_current_plan(workspace, project_plan)
            result = prepare_build_tasks(
                {
                    "request": "生成订单页面",
                    "workspace": workspace,
                    "project_plan": state_project_plan,
                    "project_plan_json_path": project_plan_path,
                    "build_execution_scope": {"type": "page", "targetId": "orders"},
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["build_execution_scope"], {"type": "page", "targetId": "orders"})
        self.assertEqual(
            set(result["build_task_plan"]["task_registry"]),
            {"orders-api-task", "orders-page-task"},
        )
        self.assertEqual(result["build_task_plan"]["build_units"]["page:customers"]["status"], "not_prepared")
        prepared_project_plan = preparer.call_args.args[0]
        self.assertNotIn("frontend_pages", prepared_project_plan)
        self.assertNotIn("page_implementation_contracts", prepared_project_plan)
        self.assertEqual(
            [page["pageId"] for page in prepared_project_plan["application_skeleton"]["pages"]],
            ["orders", "customers"],
        )
        executable_details = prepared_project_plan["executable_details"]
        self.assertEqual(
            [contract["pageId"] for contract in executable_details["page_implementation_contracts"]],
            ["orders"],
        )
        self.assertEqual(
            [endpoint["id"] for endpoint in executable_details["endpoint_contracts"]],
            ["orders.list"],
        )
        self.assertNotIn("data_sources", executable_details)
        self.assertIn("entity_designs", executable_details)
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
                        "deliverables": [{"id": "controller:customers", "kind": "backend.endpoint_controller", "target_id": "customers.list", "paths": ["api/customers.py"], "provides": ["customers.endpoint"]}],
                        "change_scope": [{"path": "api/customers.py"}],
                    },
                    {
                        "id": "customers-page-task",
                        "unit_id": "page:customers",
                        "owner": "frontend",
                        "description": "实现客户页面",
                        "dependencies": ["customers-api-task"],
                        "deliverables": [{"id": "capability:customers-page", "kind": "frontend.shared_capability", "target_id": "customers", "paths": ["src/pages/Customers.tsx"], "provides": ["customers.page"]}],
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
            "app.graph.nodes.tasks._build_prerequisite_errors", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=customer_agent_plan,
        ) as customer_preparer:
            project_plan_path = _write_current_plan(workspace, project_plan)
            customer_result = prepare_build_tasks(
                {
                    "request": "生成客户页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
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
                {"pageId": "orders"},
                {"pageId": "dashboard"},
            ],
            "page_implementation_contracts": [
                _page_implementation_contract("orders", ["orders.list"]),
                _page_implementation_contract("dashboard", []),
            ],
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                }
            ],
            "api_contracts": [
                {"id": "orders-api", "entity_ids": ["Order"], "endpoints": [{"id": "orders.list"}]},
            ],
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
            "app.graph.nodes.tasks._build_prerequisite_errors", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=agent_plan,
        ):
            project_plan_path = _write_current_plan(workspace, project_plan)
            persisted_plan_path = Path(workspace) / ".xcodeagent/plans/build-task-plan.json"
            persisted_plan_path.parent.mkdir(parents=True, exist_ok=True)
            persisted_plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "build-dag.v3",
                        "status": "ready",
                        "task_registry": {},
                        "task_graph": {
                            "nodes": [],
                            "edges": [],
                            "topological_order": [],
                            "validation": {"is_valid": True, "errors": []},
                        },
                        "execution": {"batches": []},
                        "build_execution_scope": {
                            "type": "page",
                            "targetId": "dashboard",
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = prepare_build_tasks(
                {
                    "request": "生成订单页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
                    "build_execution_scope": {"type": "page", "targetId": "orders"},
                    "timeline": [],
                }
            )
            persisted_after = json.loads(persisted_plan_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "failed")
        self.assertNotIn("clarification", result)
        self.assertIn("page:dashboard", result["error"])
        self.assertEqual(
            result["build_execution_scope"],
            {"type": "page", "targetId": "orders"},
        )
        self.assertEqual(
            result["build_task_plan"]["build_execution_scope"],
            {"type": "page", "targetId": "orders"},
        )
        self.assertFalse(result["build_task_plan_persisted"])
        self.assertEqual(
            persisted_after["build_execution_scope"],
            {"type": "page", "targetId": "dashboard"},
        )
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
                {"pageId": "orders"}
            ],
            "page_implementation_contracts": [
                _page_implementation_contract("orders", ["orders.list"]),
            ],
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                }
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "entity_ids": ["Order"],
                    "endpoints": [{"id": "orders.list"}],
                }
            ],
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
                        "deliverables": [{"id": "controller:orders-rename", "kind": "backend.endpoint_controller", "target_id": "orders.list", "paths": ["api/orders.py"], "provides": ["orders.endpoint"]}],
                        "change_scope": [{"path": "api/orders.py"}],
                    },
                    {
                        "id": "orders-page-task",
                        "unit_id": "page:orders",
                        "owner": "frontend",
                        "description": "实现订单页面",
                        "dependencies": ["shared-api-client-task"],
                        "deliverables": [{"id": "capability:orders-page-rename", "kind": "frontend.shared_capability", "target_id": "orders", "paths": ["src/pages/Orders.tsx"], "provides": ["orders.page"]}],
                        "change_scope": [{"path": "src/pages/Orders.tsx"}],
                    },
                ]
            },
        )

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks._build_prerequisite_errors", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=agent_plan,
        ):
            project_plan_path = _write_current_plan(workspace, project_plan)
            result = prepare_build_tasks(
                {
                    "request": "生成订单页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
                    "build_task_plan": base_plan,
                    "build_execution_scope": {"type": "page", "targetId": "orders"},
                    "timeline": [],
                }
            )

        task_registry = result["build_task_plan"]["task_registry"]
        self.assertEqual(result["status"], "requires_user_input")
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
                {"pageId": "orders"},
                {"pageId": "orderReports"},
            ],
            "page_implementation_contracts": [
                _page_implementation_contract("orders", ["orders.list"]),
                _page_implementation_contract("orderReports", ["orders.list"]),
            ],
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                }
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "entity_ids": ["Order"],
                    "endpoints": [{"id": "orders.list"}],
                }
            ],
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
                        "deliverables": [{"id": "controller:orders-reuse", "kind": "backend.endpoint_controller", "target_id": "orders.list", "paths": ["api/orders.py"], "provides": ["orders.endpoint"]}],
                        "change_scope": [{"path": "api/orders.py"}],
                    },
                    {
                        "id": "orders-page-task",
                        "unit_id": "page:orders",
                        "owner": "frontend",
                        "description": "实现订单页面",
                        "dependencies": ["orders-api-task"],
                        "deliverables": [{"id": "capability:orders-page-reuse", "kind": "frontend.shared_capability", "target_id": "orders", "paths": ["src/pages/Orders.tsx"], "provides": ["orders.page"]}],
                        "change_scope": [{"path": "src/pages/Orders.tsx"}],
                    },
                ]
            },
        )
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks._build_prerequisite_errors", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=first_agent_plan,
        ):
            project_plan_path = _write_current_plan(workspace, project_plan)
            first_result = prepare_build_tasks(
                {
                    "request": "生成订单页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
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
                        "deliverables": [{"id": "capability:api-client-duplicate", "kind": "frontend.shared_capability", "target_id": "frontend:api-client", "paths": ["src/api/client.ts"], "provides": ["api.client"]}],
                        "change_scope": [{"path": "src/api/client.ts"}],
                    },
                    {
                        "id": "duplicate-orders-api-task",
                        "unit_id": "backend:endpoint:orders-api:orders.list",
                        "owner": "backend",
                        "description": "重复生成订单接口",
                        "deliverables": [{"id": "controller:orders-duplicate", "kind": "backend.endpoint_controller", "target_id": "orders.list", "paths": ["api/orders.py"], "provides": ["orders.endpoint"]}],
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
                        "deliverables": [{"id": "capability:reports-page", "kind": "frontend.shared_capability", "target_id": "orderReports", "paths": ["src/pages/OrderReports.tsx"], "provides": ["reports.page"]}],
                        "change_scope": [{"path": "src/pages/OrderReports.tsx"}],
                    },
                ]
            },
        )

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.tasks._build_prerequisite_errors", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_project_plan_dependencies", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.validate_api_contract_consistency", return_value=[]
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            return_value=second_agent_plan,
        ):
            project_plan_path = _write_current_plan(workspace, project_plan)
            second_result = prepare_build_tasks(
                {
                    "request": "生成订单报表页面",
                    "workspace": workspace,
                    "project_plan": project_plan,
                    "project_plan_json_path": project_plan_path,
                    "build_task_plan": first_plan,
                    "build_execution_scope": {
                        "type": "page",
                        "targetId": "orderReports",
                    },
                    "timeline": [],
                }
            )

        task_registry = second_result["build_task_plan"]["task_registry"]
        self.assertEqual(second_result["status"], "requires_user_input")
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

    def test_prepare_build_tasks_blocks_when_formal_upstream_artifacts_are_unconfirmed(self) -> None:
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
        self.assertEqual(result["clarification"]["mode"], "build_prerequisite_error")
        self.assertTrue(any("RequirementSpec" in error for error in result["clarification"]["errors"]))
        self.assertEqual(result["phase"], "prepare_build_tasks")

    def test_prepare_build_tasks_does_not_confirm_or_rewrite_project_plan(self) -> None:
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

        preparer.assert_not_called()
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "build_prerequisite_error")
        self.assertEqual(result["project_plan"]["confirmation_status"], "pending_user_confirmation")

    def test_prepare_build_tasks_persists_pending_json_and_does_not_report_code_changes(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "confirmed"

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.tasks._build_prerequisite_errors",
                return_value=[],
            ), patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
                return_value={
                    "tasks": [
                        {
                            "id": "application-task",
                            "unit_id": "frontend:shell",
                            "owner": "frontend",
                            "title": "实现首页",
                            "description": "实现首页内容",
                            "change_scope": [
                                {"operation": "modify", "path": "frontend/src/App.tsx"}
                            ],
                            "deliverables": [
                                {
                                    "id": "capability:app-shell",
                                    "kind": "frontend.shared_capability",
                                    "target_id": "frontend:shell",
                                    "paths": ["frontend/src/App.tsx"],
                                    "provides": ["app.home"],
                                }
                            ],
                        }
                    ],
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
                plan_path = Path(workspace) / ".xcodeagent/plans/build-task-plan.json"
                persisted_plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertNotIn("code_changes", result)
        self.assertNotIn("code_change_sets", result)
        self.assertEqual(result["build_task_plan"]["prepared_by"]["mode"], "direct")
        self.assertNotIn("build_task_dag_path", result)
        self.assertEqual(persisted_plan["status"], "ready")
        self.assertEqual(persisted_plan["confirmation_status"], "pending")
        self.assertIsNone(persisted_plan["confirmed_at"])
        self.assertFalse((Path(workspace) / ".xcodeagent/plans/BUILD_TASK_DAG.md").exists())
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

        preparer.assert_not_called()
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "build_prerequisite_error")
        self.assertEqual(result["project_plan"]["confirmation_status"], "pending_user_confirmation")

    def test_prepare_build_tasks_blocks_inconsistent_api_contract(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "confirmed"
        project_plan["api_contracts"][0]["entity_ids"] = ["Unknown"]

        with patch(
            "app.graph.nodes.tasks._build_prerequisite_errors", return_value=[]
        ), patch(
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
