"""验证 Frontend Prompt 的任务级计划视图和歧义阻断。"""

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.agents.frontend.generator import (
    _frontend_generation_prompt, _scoped_generation_prompt, generate_frontend_with_deep_agent,
)
from app.agents.frontend.task_packet import frontend_task_packet
from app.agents.frontend.project_plan_context import (
    FrontendTaskProjectionError, project_frontend_task_project_plan,
)
from app.services.business_acceptance import compile_business_acceptance
from app.services.template_state import template_context


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

    def _authorization_workspace(self, root):
        """创建当前 V3 安装清单、真实受管源码及正式页面任务。"""
        permission = "frontend/src/components/authorization/Permission/index.tsx"
        resources = "frontend/src/constants/resources.ts"
        state = {"schemaVersion": 3, "templateRevision": "2026.11.19.1",
                 "requested": {}, "effective": {"authorization": {"enabled": True, "config": {}}},
                 "managedContributions": {}, "installedArtifacts": {
                     "frontend:authorization:" + permission.removeprefix("frontend/"): {
                         "capabilityId": "authorization", "target": permission,
                         "installedRevision": "2026.11.19.1"}}}
        for path, text in ((permission, "export const Permission = () => null;"),
                           (resources, "export const RESOURCES = {};"),
                           (".devagentstudio/template-state.json", json.dumps(state))):
            physical = root / path
            physical.parent.mkdir(parents=True, exist_ok=True)
            physical.write_text(text)
        page = _page_task("detail")
        target = "frontend/src/pages/Detail/index.tsx"
        page["deliverables"][0]["paths"] = [target]
        page.update(id="page-detail", target_files=[target], allowed_paths=[target],
                    source_refs={"authorization": {"actions": [{"actionId": "save"}]}})
        return page, {"template_context": template_context(state)}, state, permission, resources

    def test_template_dependencies_are_exact_and_conditional(self):
        """只为权限页面投影真实依赖，且不改变目标、操作和 API 依赖。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            page, build, state, permission, resources = self._authorization_workspace(root)
            before = deepcopy((page, build))
            packet = frontend_task_packet(_plan(), build, page, workspace=folder)
            expected = sorted(["/" + permission, "/" + resources])
            self.assertEqual(packet["implementation_contract"]["template_read_dependencies"], expected)
            self.assertEqual((page, build), before)
            plain = frontend_task_packet(_plan(), {}, page)
            for key in ("target_files", "allowed_paths", "change_scope"):
                self.assertEqual(packet[key], plain[key])
            self.assertEqual(packet["implementation_contract"]["api_dependencies"], [])
            self.assertNotIn("template_read_dependencies", plain["implementation_contract"])
            page["source_refs"]["authorization"]["actions"] = []
            self.assertNotIn("template_read_dependencies", frontend_task_packet(
                _plan(), build, page)["implementation_contract"])
            api = _api_task(_plan())
            api.update(target_files=["frontend/src/apis/exampleApi.ts"],
                       allowed_paths=["frontend/src/apis/exampleApi.ts"])
            self.assertNotIn("template_read_dependencies", frontend_task_packet(
                _plan(), build, api)["implementation_contract"])
            # 清单记录可定位不同的真实目录，不依赖历史页面 import。
            moved = "frontend/src/components/access/Permission.tsx"
            (root / moved).parent.mkdir(parents=True)
            (root / permission).rename(root / moved)
            state["installedArtifacts"] = {"frontend:authorization:src/components/access/Permission.tsx": {
                "capabilityId": "authorization", "target": moved, "installedRevision": "2026.11.19.1"}}
            (root / ".devagentstudio/template-state.json").write_text(json.dumps(state))
            page["source_refs"]["authorization"]["actions"] = [{"actionId": "save"}]
            self.assertIn("/" + moved, frontend_task_packet(
                _plan(), build, page, workspace=folder)["implementation_contract"]["template_read_dependencies"])

    def test_template_dependency_resolution_fails_closed(self):
        """缺失、歧义、目录、符号链接、硬链接、越界记录及状态漂移均在模型前失败。"""
        for scenario in ("missing", "resources_missing", "directory", "symlink", "hardlink",
                         "parent_link", "ambiguous", "unsafe_record", "drift", "state_link"):
            with self.subTest(scenario=scenario), TemporaryDirectory() as folder:
                root = Path(folder)
                page, build, state, permission, resources = self._authorization_workspace(root)
                path = root / permission
                state_path = root / ".devagentstudio/template-state.json"
                if scenario in {"missing", "directory", "symlink", "hardlink"}:
                    path.unlink()
                    if scenario == "directory":
                        path.mkdir()
                    elif scenario == "symlink":
                        path.symlink_to(root / resources)
                    elif scenario == "hardlink":
                        os.link(root / resources, path)
                elif scenario == "resources_missing":
                    (root / resources).unlink()
                elif scenario == "parent_link":
                    parent = path.parent
                    parent.rename(parent.with_name("Original"))
                    parent.symlink_to(parent.with_name("Original"), target_is_directory=True)
                elif scenario == "state_link":
                    state_path.rename(state_path.with_suffix(".copy"))
                    state_path.symlink_to(state_path.with_suffix(".copy"))
                else:
                    if scenario == "ambiguous":
                        target = "frontend/src/components/authorization/Permission.tsx"
                        state["installedArtifacts"]["frontend:authorization:src/components/authorization/Permission.tsx"] = {
                            "capabilityId": "authorization", "target": target, "installedRevision": "2026.11.19.1"}
                    elif scenario == "unsafe_record":
                        artifact = next(iter(state["installedArtifacts"].values()))
                        artifact["target"] = "frontend/src/../Permission.tsx"
                    else:
                        state["templateRevision"] = "2026.11.19.2"
                    state_path.write_text(json.dumps(state))
                with self.assertRaisesRegex(FrontendTaskProjectionError, "template_read_dependency_unresolved"):
                    frontend_task_packet(_plan(), build, page, workspace=folder)

    def test_template_dependency_bounded_candidates(self):
        """无精确安装记录时只接受唯一约定文件，不使用其他目录替代缺失组件。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            page, build, state, permission, _ = self._authorization_workspace(root)
            state["installedArtifacts"] = {}
            (root / ".devagentstudio/template-state.json").write_text(json.dumps(state))
            self.assertIn("/" + permission, frontend_task_packet(
                _plan(), build, page, workspace=folder)["implementation_contract"]["template_read_dependencies"])
            second = root / "frontend/src/components/authorization/Permission.tsx"
            second.write_text("ambiguous")
            with self.assertRaisesRegex(FrontendTaskProjectionError, "template_read_dependency_unresolved"):
                frontend_task_packet(_plan(), build, page, workspace=folder)
            second.unlink()
            outside = root / "frontend/src/components/Permission/index.tsx"
            outside.parent.mkdir(parents=True)
            (root / permission).rename(outside)
            with self.assertRaisesRegex(FrontendTaskProjectionError, "template_read_dependency_unresolved"):
                frontend_task_packet(_plan(), build, page, workspace=folder)
            with self.assertRaisesRegex(FrontendTaskProjectionError, "template_read_dependency_unresolved"):
                frontend_task_packet(_plan(), build, page)

    def test_unresolved_template_dependency_only_fails_affected_page(self):
        """准备失败页面不会调用模型，也不会阻断同批无权限动作页面。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            page, build, _, permission, _ = self._authorization_workspace(root)
            (root / permission).unlink()
            unrelated = deepcopy(page)
            unrelated["id"] = "unrelated-page"
            unrelated["source_refs"]["authorization"]["actions"] = []
            with patch("app.agents.frontend.generator._invoke_live_frontend_agent", return_value="{}") as invoke:
                results = generate_frontend_with_deep_agent(
                    project_plan=_plan(), build_task_plan=build, tasks=[page, unrelated], workspace=folder,
                )
            invoke.assert_called_once()
            self.assertEqual([task["id"] for task in invoke.call_args.kwargs["tasks"]], ["unrelated-page"])
            failed = next(result for result in results if result["task_id"] == page["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertIn("template_read_dependency_unresolved", str(failed))

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
        self.assertNotIn("architecture", scoped)
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

    def test_ambiguous_or_conflicting_endpoint_identity_fails_closed(self):
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
                with self.assertRaises(FrontendTaskProjectionError):
                    project_frontend_task_project_plan(plan, [task])
        plan["page_implementation_contracts"][0]["actionBindings"][0]["steps"][0]["endpointId"] = "old"
        with self.assertRaises(FrontendTaskProjectionError):
            project_frontend_task_project_plan(plan, [_page_task()])

    def test_invalid_schema_references_fail_closed(self):
        """缺失、跨合同、外部与格式不合法的 Schema 引用均回退。"""
        for ref in ("Missing", "other-api#/schemas/Order", "https://example.test/schemas/Order",
                    "#/schemas/Order/properties/id", {"malformed": "Order"}):
            with self.subTest(ref=ref):
                plan = _plan()
                plan["api_contracts"][0]["schemas"]["Order"]["allOf"][0]["$ref"] = ref
                with self.assertRaises(FrontendTaskProjectionError):
                    project_frontend_task_project_plan(plan, [_page_task()])

    def test_unsupported_tasks_fail_closed(self):
        """不支持的静态或未知任务与缺失合同不会产生半截批次。"""
        plan = _plan()
        for task in ({"unit_id": "frontend:data:static"},
                     {"deliverables": [{"kind": "frontend.static_data_module"}]},
                     {"unit_id": "unknown"}, _page_task("other"), None):
            for tasks in ([task], [_page_task(), task]):
                with self.subTest(tasks=tasks):
                    with self.assertRaises(FrontendTaskProjectionError):
                        project_frontend_task_project_plan(plan, tasks)
        minimal = {"app": {"name": "sample"}}
        with self.assertRaises(FrontendTaskProjectionError):
            project_frontend_task_project_plan(minimal, [_page_task()])

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
        with self.assertRaises(FrontendTaskProjectionError):
            project_frontend_task_project_plan(plan, [_page_task()])
        plan["data_sources"].pop()
        plan["frontend_pages"] = [{"path": "/menu", "children": plan["frontend_pages"]}]
        with self.assertRaises(FrontendTaskProjectionError):
            project_frontend_task_project_plan(plan, [_page_task()])

    def test_repair_requires_inherited_formal_scope(self):
        """页面与共享 API 修复由继承检查定位，职责无法重建时回退。"""
        plan = _plan()
        task = {**_api_task(plan), "kind": "repair"}
        task["source_refs"] = {"parent": task["source_refs"]}
        self.assertEqual(len(project_frontend_task_project_plan(plan, [task])["api_contracts"]), 1)
        task["business_acceptance_checks"] = []
        with self.assertRaises(FrontendTaskProjectionError):
            project_frontend_task_project_plan(plan, [task])
        page_task = {**_page_task(), "kind": "repair"}
        with self.assertRaises(FrontendTaskProjectionError):
            project_frontend_task_project_plan(plan, [page_task])
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
        with self.assertRaises(FrontendTaskProjectionError):
            project_frontend_task_project_plan(plan, [task])

    def test_uncontrolled_prompt_core_docs_and_specialized_static_skill(self):
        """非受控生成在静态来源和设计稿分支均使用两份核心规范。"""
        for static in (False, True):
            for confirmed_design in (False, True):
                with self.subTest(static=static, confirmed_design=confirmed_design):
                    task = {"source_refs": {"endpoint_designs": [{"sourceSnapshots": [
                        {"sourceType": "static" if static else "database"}
                    ]}]}}
                    designs = {"pages": [{"pageId": "orders", "page_key": "Orders",
                                           "status": "confirmed" if confirmed_design else "pending"}]}
                    prompt = _frontend_generation_prompt(project_plan=_plan(), build_task_plan={},
                                                         tasks=[task], ui_designs=designs)
                    self.assertEqual(prompt.count("/.devagentstudio/builtin-skills/frontend-engineering-standards/SKILL.md"), 1)
                    self.assertIn("`/frontend/AGENTS.md`", prompt)
                    self.assertIn("not a built-in Skill", prompt)
                    for rule in (
                        "For every `frontend.api_module` task",
                        "Preserve every existing formal Endpoint export and its compliant implementation",
                        "exists, read it before editing",
                        "preserve it unchanged and return already_satisfied",
                        "Never rewrite a compliant implementation solely to match an example",
                        "style preference, or an equivalent implementation pattern",
                        "Only modify concrete contract or engineering violations within the current task scope",
                        "create a second per-Endpoint API module, split one Contract into multiple files",
                        "remove existing exports because this task is an incremental PlanningRun",
                        "continue importing all business API functions from the shared Contract module",
                    ):
                        self.assertIn(rule, prompt)
                    for obsolete in ("code-block-template", "react-develop-specification",
                                     "frontend-template-modification-boundary"):
                        self.assertNotIn(obsolete, prompt)
                    self.assertEqual("frontend-static-data-generate/SKILL.md" in prompt, static)
                    self.assertEqual("UI Design Reference (MUST READ" in prompt, confirmed_design)
                    if confirmed_design:
                        self.assertIn("queries managed by `useRequest`", prompt)
                        self.assertIn("ProTable consuming `dataSource`/`loading`", prompt)
                        self.assertIn("confirmed page contract", prompt)
                        self.assertIn("do not block or fail the task", prompt)
                    if static:
                        self.assertIn("module-level in-memory array", prompt)
                        self.assertIn("Keep `service.ts` untouched", prompt)

    def test_scoped_prompt_regular_and_static_paths(self):
        """任务包只注入本页设计、正式事实与短验收标识，静态分支原样接收计划。"""
        plan = _plan()
        plan["architecture"]["privateMarker"] = "global-plan-sentinel"
        page = _page_task()
        target = "frontend/src/pages/Orders/index.tsx"
        page["deliverables"][0]["paths"] = [target]
        page.update(id="page-orders", target_files=[target], allowed_paths=[target],
                    dependencies=["shared-api", "other-api"])
        api = _api_task(plan)
        api.update(target_files=["frontend/src/apis/exampleApi.ts"],
                   allowed_paths=["frontend/src/apis/exampleApi.ts"])
        unrelated = _api_task(plan, "other-api", ("list",))
        unrelated["deliverables"][0]["paths"] = ["frontend/src/apis/unrelatedApi.ts"]
        build = {"summary": {"internal": "dag-summary-sentinel"},
                 "task_graph": {"nodes": ["shared-api", "other-api"]},
                 "task_registry": {"shared-api": {**api, "status": "completed"},
                                   "other-api": {**unrelated, "id": "other-api", "status": "completed"}}}
        designs = {"pages": [
            {"pageId": "orders", "status": "confirmed", "page_key": "Orders"},
            {"pageId": "other", "status": "confirmed", "page_key": "Other"},
        ]}
        with patch("app.services.page_templates.load_template_source", return_value="template-body") as load:
            packets = [frontend_task_packet(plan, build, task, ui_designs=designs,
                           page_template={"id": "table"}, template_page_id="orders")
                       for task in [page, api]]
        load.assert_called_once_with("table")
        self.assertNotIn("confirmed_design", packets[1]["implementation_contract"])
        self.assertEqual(packets[0]["execution_steps"], [f"step_{index}" for index in range(1, 5)])
        self.assertEqual(len(packets[0]["step_contracts"]), 4)
        self.assertNotIn("execution_steps", packets[1])
        self.assertNotIn("step_contracts", packets[1])
        with patch("app.agents.frontend.generator.record_prompt") as record:
            prompt = _scoped_generation_prompt(packets)
        self.assertEqual(prompt.count("ui-design/pages/Orders/index.tsx"), 1)
        self.assertEqual(prompt.count("template-body"), 1)
        for irrelevant in ("ui-design/pages/Other", "source_refs", "source_hash",
                           "dag-summary-sentinel", "global-plan-sentinel", "unrelatedApi.ts"):
            self.assertNotIn(irrelevant, prompt)
        record.assert_called_once()
        static = _frontend_generation_prompt(project_plan=plan, build_task_plan=build,
                                              tasks=[{"unit_id": "frontend:data:static"}])
        self.assertIn("global-plan-sentinel", static)
        self.assertLess(len(prompt.encode()), len(static.encode()))
        for packet in packets:
            with patch("app.agents.frontend.generator.record_prompt"):
                single = _scoped_generation_prompt([packet])
            for control in ("begin_frontend_task", "advance_frontend_step", "complete_frontend_task"):
                self.assertEqual(control in single, packet["kind"] == "frontend.page")
            self.assertEqual(single.count('"implementation_contract"'), 1)
            self.assertIn("FE-API-BOUNDARY", single)
            self.assertIn("FE-TYPES", single)
            self.assertIn("FE-CODE-LAYERING", single)
            for irrelevant in ("source_refs", "dag-summary-sentinel", "global-plan-sentinel"):
                self.assertNotIn(irrelevant, single)
            self.assertEqual("FE-HOOKS" in single, packet["kind"] == "frontend.page")
