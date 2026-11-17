"""验证 Frontend Prompt 的任务级计划视图与完整计划回退。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.agents.frontend.generator import _frontend_generation_prompt
from app.agents.frontend.project_plan_context import project_frontend_task_project_plan
from app.services.business_acceptance import compile_business_acceptance


def _plan():
    """构造包含页面跳转、同名接口和完整权限结构的运行时 TechnicalPlan。"""
    dependencies = [{"endpoint_id": "list", "usage": "查询", "required_for_initial_load": True}]
    navigation = [{"targetPageId": "detail"}]
    return {
        "artifact_type": "technical-plan",
        "version": 1,
        "confirmation_status": "confirmed",
        "app": {"name": "库存", "route_root_path": "/inventory"},
        "architecture": {"frontend": {"framework": "react"}},
        "pages": [
            {
                "pageId": "orders", "path": "/orders", "description": "订单列表",
                "references": {"endpoint_dependencies": dependencies, "navigation_targets": navigation},
            },
            {
                "pageId": "detail", "path": "/detail/:orderId",
                "route_params": [{"name": "orderId", "type": "string"}],
                "description": "详细业务说明", "references": {"endpoint_dependencies": []},
            },
            {"pageId": "other", "path": "/other", "description": "无关" * 500},
        ],
        "page_implementation_contracts": [
            {
                "pageId": "orders", "requiredEndpointIds": ["list"],
                "actionBindings": [{"bindingType": "sequence", "steps": [
                    {"type": "endpoint", "endpointId": "list"},
                    {"type": "navigation", "targetPageId": "detail"},
                ]}],
                "permissionBindings": [
                    {"targetType": "page", "pageId": "orders", "resourceKey": "ordersPage"},
                    {"targetType": "action", "pageId": "orders", "actionId": "load",
                     "resourceKey": "ordersLoad", "mode": "disabled"},
                ],
                "navigationBindings": navigation,
                "technicalReferences": {"endpointDependencies": dependencies, "navigationTargets": navigation},
                "responseBindings": [{"endpointId": "list", "field": "data"}],
                "productAcceptance": ["显示订单"],
            },
            {"pageId": "detail", "requiredEndpointIds": [], "actionBindings": [{"bindingType": "local"}]},
        ],
        "api_contracts": [
            {
                "id": "orders-api", "base_path": "/orders", "entity_ids": ["order"],
                "authentication": {"required": True},
                "endpoints": [
                    {"id": "list", "method": "GET", "path": "/orders",
                     "response_schema_ref": "#/components/schemas/Orders",
                     "parameters": [{"schema": {"$ref": "Filter"}}]},
                    {"id": "update", "method": "PUT", "path": "/orders", "request_schema_ref": "Order"},
                    {"id": "old", "method": "GET", "path": "/orders/old", "response_schema_ref": "Historical"},
                ],
                "schemas": {
                    "Orders": {"type": "array", "items": {"$ref": "orders-api#/schemas/Order"}},
                    "Order": {"allOf": [{"$ref": "#/schemas/BaseOrder"}, {"type": "object"}]},
                    "BaseOrder": {"properties": {"again": {"oneOf": [{"$ref": "Orders"}, {"type": "null"}]}}},
                    "Filter": {"properties": {"schema_ref": {"type": "string"}, "id": {"type": "string"}}},
                    "Historical": {"type": "object"},
                },
            },
            {
                "id": "other-api", "entity_ids": ["other-entity"],
                "endpoints": [{"id": "list", "method": "GET", "path": "/other",
                               "response_schema_ref": "Order"}],
                "schemas": {"Order": {"type": "boolean"}},
            },
        ],
        "entities": [
            {"id": "order", "fields": [{"name": "orderId", "description": "订单号"}]},
            {"id": "other-entity"},
        ],
        "authorization_manifest": {
            "schema_version": "authorization-manifest.v1",
            "bindings": {
                "pages": [{"pageId": "orders", "resourceKey": "ordersPage"},
                          {"pageId": "other", "resourceKey": "otherPage"}],
                "actions": [{"pageId": "orders", "actionId": "load", "resourceKey": "ordersLoad",
                             "mode": "disabled"}],
                "endpoints": [{"apiContractId": "orders-api", "endpointId": "list",
                               "operationResourceKeys": ["ordersLoad"]}],
            },
            "resources": [{"resourceKey": key} for key in ("ordersPage", "ordersLoad", "otherPage")],
        },
        "data_sources": [],
    }


def _page_task(page_id="orders"):
    """构造已派发页面任务及其编译的来源引用。"""
    task = {"unit_id": f"page:{page_id}", "owner": "frontend",
            "deliverables": [{"kind": "frontend.page", "target_id": page_id}]}
    if page_id == "orders":
        task["source_refs"] = {
            "endpoint_ids": ["list"],
            "technical_plan_endpoints": [{"api_contract_id": "orders-api", "id": "list"}],
        }
    return task


def _api_task(plan, contract_id="orders-api", endpoint_ids=("list", "update")):
    """调用真实验收编译器，生成带组合身份与允许保留导出的共享 API Task。"""
    task = {
        "id": "shared-api", "unit_id": "frontend:api-client", "owner": "frontend",
        "deliverables": [
            {"id": endpoint_id, "kind": "frontend.api_module", "target_id": endpoint_id,
             "paths": ["frontend/src/apis/exampleApi.ts"]}
            for endpoint_id in endpoint_ids
        ],
        "source_refs": {"api_contract_ids": [contract_id], "endpoint_ids": list(endpoint_ids)},
    }
    return compile_business_acceptance([task], {"project_plan": plan})[0]


class FrontendProjectPlanContextTests(unittest.TestCase):
    """验证已派发身份、引用闭包、批次回退和真实 Prompt 序列化。"""

    def test_page_keeps_full_contract_and_only_direct_navigation_route(self):
        """当前页完整保留，导航目标只提供正式路径和参数。"""
        plan = _plan()
        tasks = [_page_task()]
        before = deepcopy((plan, tasks))
        scoped = project_frontend_task_project_plan(plan, tasks)
        self.assertEqual(scoped["pages"], [plan["pages"][0], {
            "pageId": "detail", "path": "/detail/:orderId", "route_params": plan["pages"][1]["route_params"],
        }])
        self.assertEqual(scoped["page_implementation_contracts"], plan["page_implementation_contracts"][:1])
        expected = deepcopy(plan["api_contracts"][0])
        expected["endpoints"] = expected["endpoints"][:1]
        del expected["schemas"]["Historical"]
        self.assertEqual(scoped["api_contracts"], [expected])
        self.assertEqual(scoped["entities"], plan["entities"][:1])
        self.assertEqual(scoped["authorization_manifest"]["resources"],
                         plan["authorization_manifest"]["resources"][:2])
        self.assertEqual(scoped["authorization_manifest"]["bindings"]["pages"],
                         plan["authorization_manifest"]["bindings"]["pages"][:1])
        self.assertEqual(scoped["app"], plan["app"])
        self.assertEqual(scoped["architecture"], plan["architecture"])
        self.assertLess(len(json.dumps(scoped, ensure_ascii=False)), len(json.dumps(plan, ensure_ascii=False)))
        scoped["pages"][0]["references"]["endpoint_dependencies"].clear()
        scoped["page_implementation_contracts"][0]["productAcceptance"].clear()
        scoped["api_contracts"][0]["schemas"]["Orders"].clear()
        scoped["entities"][0]["fields"].clear()
        self.assertEqual((plan, tasks), before)

    def test_no_endpoint_and_batch_union(self):
        """无接口页可裁剪，合批后仍保留双方职责，导航页升级为完整页面。"""
        plan = _plan()
        scoped = project_frontend_task_project_plan(plan, [_page_task("detail")])
        self.assertEqual(scoped["pages"], [plan["pages"][1]])
        self.assertEqual(scoped["page_implementation_contracts"], plan["page_implementation_contracts"][1:])
        self.assertEqual(scoped["api_contracts"], [])
        tasks = [_page_task("detail"), _api_task(plan, "other-api", ("list",)), _page_task()]
        scoped = project_frontend_task_project_plan(plan, tasks)
        self.assertEqual(scoped["pages"], plan["pages"][:2])
        self.assertEqual(scoped["page_implementation_contracts"], plan["page_implementation_contracts"])
        self.assertEqual([c["id"] for c in scoped["api_contracts"]], ["orders-api", "other-api"])
        self.assertEqual(scoped, project_frontend_task_project_plan(plan, list(reversed(tasks))))

    def test_shared_api_keeps_required_endpoints_only(self):
        """同一模块生成两个接口，旧导出只留在原始 Task 的约束中。"""
        plan = _plan()
        task = _api_task(plan)
        task["source_refs"]["endpoint_ids"].append("old")
        expected = task["business_acceptance_checks"][0]["expected"]
        self.assertEqual([e["endpoint_id"] for e in expected["allowed_existing_endpoints"]], ["old"])
        scoped = project_frontend_task_project_plan(plan, [task])
        self.assertEqual(scoped["pages"], [])
        self.assertEqual(scoped["page_implementation_contracts"], [])
        self.assertEqual([c["id"] for c in scoped["api_contracts"]], ["orders-api"])
        self.assertEqual(scoped["api_contracts"][0]["endpoints"], plan["api_contracts"][0]["endpoints"][:2])
        self.assertNotIn("Historical", scoped["api_contracts"][0]["schemas"])
        self.assertEqual(expected["allowed_existing_endpoints"][0]["endpoint_id"], "old")

    def test_ambiguous_or_conflicting_endpoint_identity_falls_back(self):
        """缺少组合身份、交付物不匹配或页面动作越出依赖时整批回退。"""
        plan = _plan()
        ambiguous = _page_task()
        del ambiguous["source_refs"]["technical_plan_endpoints"]
        mismatch = _api_task(plan)
        mismatch["deliverables"][1]["target_id"] = "old"
        duplicate = _api_task(plan, endpoint_ids=("list",))
        expected = duplicate["business_acceptance_checks"][0]["expected"]
        endpoints = [*expected["endpoints"], {"api_contract_id": "other-api", "endpoint_id": "list"}]
        expected.update(endpoints=endpoints, required_endpoints=endpoints)
        for task in (ambiguous, mismatch, duplicate):
            with self.subTest(task=task["unit_id"]):
                self.assertEqual(project_frontend_task_project_plan(plan, [task]), plan)
        plan["page_implementation_contracts"][0]["actionBindings"][0]["steps"][0]["endpointId"] = "old"
        self.assertEqual(project_frontend_task_project_plan(plan, [_page_task()]), plan)

    def test_invalid_schema_references_fall_back(self):
        """缺失、跨合同、外部与格式不合法的 Schema 引用均回退。"""
        for ref in ("Missing", "other-api#/schemas/Order", "https://example.test/schemas/Order",
                    "#/schemas/Order/properties/id", {"malformed": "Order"}):
            with self.subTest(ref=ref):
                plan = _plan()
                plan["api_contracts"][0]["schemas"]["Order"]["allOf"][0]["$ref"] = ref
                self.assertEqual(project_frontend_task_project_plan(plan, [_page_task()]), plan)

    def test_fallback_preserves_entire_batch(self):
        """不支持的静态或未知任务与缺失合同不会产生半截批次。"""
        plan = _plan()
        for task in ({"unit_id": "frontend:data:static"},
                     {"deliverables": [{"kind": "frontend.static_data_module"}]},
                     {"unit_id": "unknown"}, _page_task("other"), None):
            for tasks in ([task], [_page_task(), task]):
                with self.subTest(tasks=tasks):
                    fallback = project_frontend_task_project_plan(plan, tasks)
                    self.assertEqual(fallback, plan)
                    self.assertIsNot(fallback["pages"], plan["pages"])
        minimal = {"app": {"name": "sample"}}
        self.assertEqual(project_frontend_task_project_plan(minimal, [_page_task()]), minimal)

    def test_flat_page_records_and_data_sources_require_provable_scope(self):
        """扁平页面保留独立路由，只有明确归属的来源可以过滤。"""
        plan = _plan()
        del plan["artifact_type"]
        plan["frontend_pages"] = plan.pop("pages")
        plan["data_sources"] = [
            {"id": "db", "entity_ids": ["order"]},
            {"id": "other-db", "entity_ids": ["other-entity"]},
        ]
        scoped = project_frontend_task_project_plan(plan, [_page_task()])
        self.assertEqual(len(scoped["frontend_pages"]), 2)
        self.assertEqual(scoped["data_sources"], plan["data_sources"][:1])

        plan["data_sources"].append({"id": "unknown"})
        self.assertEqual(project_frontend_task_project_plan(plan, [_page_task()]), plan)
        plan["data_sources"].pop()
        plan["frontend_pages"] = [{"path": "/menu", "children": plan["frontend_pages"]}]
        self.assertEqual(project_frontend_task_project_plan(plan, [_page_task()]), plan)

    def test_repair_requires_inherited_formal_scope(self):
        """页面与共享 API 修复由继承检查定位，职责无法重建时回退。"""
        plan = _plan()
        task = {**_api_task(plan), "kind": "repair"}
        task["source_refs"] = {"parent": task["source_refs"]}
        self.assertEqual(len(project_frontend_task_project_plan(plan, [task])["api_contracts"]), 1)
        task["business_acceptance_checks"] = []
        self.assertEqual(project_frontend_task_project_plan(plan, [task]), plan)
        page_task = {**_page_task(), "kind": "repair"}
        self.assertEqual(project_frontend_task_project_plan(plan, [page_task]), plan)
        page_task["business_acceptance_checks"] = [{
            "kind": "frontend.page_endpoint_usage",
            "expected": {"page_id": "orders", "required_endpoint_ids": ["list"],
                         "endpoints": [{"api_contract_id": "orders-api", "endpoint_id": "list"}]},
        }]
        self.assertEqual(len(project_frontend_task_project_plan(plan, [page_task])["api_contracts"]), 1)

    def test_api_without_checks_requires_exact_deliverable_and_source_pair(self):
        """缺少业务检查时交付物和明确的组合来源必须能唯一交叉定位。"""
        plan = _plan()
        task = _api_task(plan, endpoint_ids=("list",))
        del task["business_acceptance_checks"]
        scoped = project_frontend_task_project_plan(plan, [task])
        self.assertEqual([c["id"] for c in scoped["api_contracts"]], ["orders-api"])
        del task["source_refs"]["api_contract_ids"]
        self.assertEqual(project_frontend_task_project_plan(plan, [task]), plan)

    def test_prompt_changes_only_plan_and_reuses_existing_measurement(self):
        """捕获完整基线和裁剪后的 Prompt，检查注入边界及采集文本。"""
        plan = _plan()
        tasks = [_page_task(), _api_task(plan)]
        summary = {"task_count": 2}
        kwargs = {"project_plan": plan, "tasks": tasks, "build_task_plan": {"summary": summary}}
        generator = "app.agents.frontend.generator"
        with patch(f"{generator}.project_frontend_task_project_plan", return_value=plan):
            baseline = _frontend_generation_prompt(**kwargs)
        with patch(f"{generator}.record_prompt") as record:
            after = _frontend_generation_prompt(**kwargs)
        header = "ProjectPlan context:\n"
        baseline_prefix, baseline_json = baseline.rsplit(header, 1)
        after_prefix, after_json = after.rsplit(header, 1)
        task_text = json.dumps(tasks, ensure_ascii=False, indent=2)
        self.assertEqual(after_prefix, baseline_prefix)
        self.assertTrue(after_prefix.endswith(
            f"Approved frontend tasks:\n{task_text}\n\n"
            f"BuildTaskPlan summary:\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n\n"
        ))
        self.assertEqual(json.loads(baseline_json), plan)
        self.assertEqual(json.loads(after_json), project_frontend_task_project_plan(plan, tasks))
        self.assertLess(len(after_json.encode("utf-8")), len(baseline_json.encode("utf-8")))
        record.assert_called_once_with(after, task_context=task_text, project_plan=after_json)
