"""T0.1 应继续保持的 DAG Planning Contract；legacy 边界见同目录说明。"""

from copy import deepcopy
import unittest

from app.services.build_scheduler import resolve_execution_slice
from app.services.build_task_planner import (
    compile_build_task_plan_scope, create_build_task_plan, tasks_from_build_task_plan,
)
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from tests.dag_planning_baseline_fixtures import (
    build_context, candidate_tasks, compiled_plan, confirmed_baseline,
    execution_scope, project_plan, task, workspace_snapshot,
)


class DagPlanningBaselineTests(unittest.TestCase):
    def test_empty_baseline_compiles_complete_graph_without_mutating_inputs(self) -> None:
        """无历史 DAG 时生成当前任务、正确骨架和可执行图，且不改写正式输入。"""

        for source, auth, target in (
            ("database", False, "page"), ("database", False, "endpoint"),
            ("static", False, "page"), ("database", True, "page"),
        ):
            with self.subTest(source=source, authorization=auth, target=target):
                plan = project_plan(source_type=source, authorization=auth)
                scope = execution_scope(target)
                before = deepcopy(plan)
                result = compiled_plan(plan, scope, baseline={})
                expected_ids = ({"orders:static", "orders:page"} if source == "static" else
                    {"backend:bootstrap::config", "orders:controller"} if target == "endpoint" else
                    {"backend:bootstrap::config", "orders:controller", "api:adapter", "orders:api", "orders:page"})
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["schema_version"], "build-dag.v3")
                self.assertEqual(result["build_execution_scope"], scope)
                self.assertEqual(result["task_graph"]["validation"], {"is_valid": True, "errors": []})
                self.assertEqual(set(result["task_registry"]), expected_ids)
                self.assertEqual(set(result["task_graph"]["nodes"]), expected_ids)
                self.assertCountEqual(result["task_graph"]["topological_order"], expected_ids)
                self.assertEqual(result["build_units"]["frontend:shell"]["task_ids"], [])
                self.assertNotIn("frontend:route-registry", result["build_units"])
                self.assertEqual(plan, before)

    def test_confirmed_baseline_preserves_tasks_evidence_and_skeleton_on_reuse(self) -> None:
        """已有 confirmed DAG 作为骨架输入时保留任务、状态和验收证据且不污染原对象。"""

        plan = project_plan()
        baseline = confirmed_baseline(plan, execution_scope())
        baseline["task_registry"]["api:adapter"]["acceptance_evidence"] = [{"status": "passed"}]
        before = deepcopy(baseline)
        reused = ensure_build_unit_skeleton(plan, workspace_snapshot(), baseline)
        self.assertTrue(reused["unit_skeleton"]["reused"])
        for key in ("task_registry", "task_graph", "build_units", "confirmation_status"):
            self.assertEqual(reused[key], before[key])
        reused["task_registry"]["api:adapter"]["acceptance_evidence"][0]["status"] = "failed"
        self.assertEqual(baseline, before)

    def test_workspace_revision_refresh_keeps_confirmed_task_contracts(self) -> None:
        """工作区修订变化只刷新骨架元数据，不丢失已确认任务合同。"""

        plan = project_plan()
        baseline = confirmed_baseline(plan, execution_scope())
        refreshed = ensure_build_unit_skeleton(plan, {**workspace_snapshot(), "workspace_revision": "next"}, baseline)
        self.assertFalse(refreshed["unit_skeleton"]["reused"])
        self.assertEqual(refreshed["task_registry"], baseline["task_registry"])
        self.assertEqual(refreshed["build_units"]["page:orders"]["task_ids"], ["orders:page"])

    def test_context_and_skeleton_agree_on_direct_target_units(self) -> None:
        """页面、接口、静态及权限 scope 只暴露直接 Endpoint 和实体。"""

        for source, auth, target in (
            ("database", False, "page"), ("database", False, "endpoint"),
            ("static", False, "page"), ("database", True, "page"),
        ):
            with self.subTest(source=source, authorization=auth, target=target):
                plan = project_plan(source_type=source, authorization=auth)
                context = build_context(plan, execution_scope(target))
                skeleton = ensure_build_unit_skeleton(plan, workspace_snapshot())
                required = set(context["required_unit_ids"])
                expected = ({"frontend:shell", "frontend:data:static", "page:orders"} if source == "static" else
                    {"backend:bootstrap", "backend:endpoint:orders-api:orders.list"})
                if source != "static" and target == "page":
                    expected |= {"frontend:shell", "frontend:api-client", "page:orders"}
                if auth:
                    expected.add("frontend:auth-guard")
                self.assertEqual(required, expected)
                self.assertTrue(required <= set(skeleton["build_units"]))
                self.assertEqual(context["endpoint_ids"], ["orders.list"])
                self.assertEqual(context["entity_ids"], ["Order"])
                self.assertEqual([d["entity_id"] for d in context["entity_designs"]], ["Order"])
                self.assertFalse(any(unit.startswith("database:") for unit in skeleton["build_units"]))
                if source == "static":
                    self.assertFalse(any(unit.startswith("backend:") for unit in skeleton["build_units"]))
                self.assertEqual(skeleton["unit_graph"]["validation"], {"is_valid": True, "errors": []})

    def test_task_graph_preserves_local_order_and_parallel_frontend_backend(self) -> None:
        """保留同 Unit 顺序、bootstrap 前置和前后端并行的现有正确行为。"""

        result = compiled_plan(project_plan(), execution_scope())
        dependencies = {key: set(value["dependencies"]) for key, value in result["task_registry"].items()}
        self.assertEqual(dependencies, {
            "backend:bootstrap::config": set(), "orders:controller": {"backend:bootstrap::config"},
            "api:adapter": set(), "orders:api": {"api:adapter"},
            "orders:page": {"api:adapter", "orders:api"},
        })
        graph = result["task_graph"]
        expected_edges = {(dependency, task_id) for task_id, values in dependencies.items() for dependency in values}
        self.assertEqual({(e["from"], e["to"]) for e in graph["edges"]}, expected_edges)
        order = graph["topological_order"]
        for source, target in expected_edges:
            self.assertLess(order.index(source), order.index(target))
        self.assertEqual(result["execution"]["blocked_batches"], [])

    def test_invalid_local_dependencies_remain_visible(self) -> None:
        """同 Unit 的环和不存在的依赖必须阻止执行，读取器不能静默丢弃任务。"""

        plan = project_plan()
        scope = execution_scope()
        context = build_context(plan, scope)
        for invalid_dependency in ("orders:api", "missing-task"):
            with self.subTest(dependency=invalid_dependency):
                candidates = candidate_tasks(context)
                next(t for t in candidates if t["id"] == "api:adapter")["dependencies"] = [invalid_dependency]
                result = create_build_task_plan(
                    plan, agent_plan={"tasks": candidates}, build_context=context,
                    base_build_task_plan=ensure_build_unit_skeleton(plan, workspace_snapshot()),
                )
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["task_graph"]["validation"]["is_valid"])
                self.assertTrue(result["task_graph"]["validation"]["errors"])
                self.assertCountEqual([t["id"] for t in tasks_from_build_task_plan(result)], [t["id"] for t in candidates])

    def test_acceptance_compiles_exact_paths_and_formal_endpoint_sources(self) -> None:
        """工程检查绑定路径，业务检查绑定正式 Endpoint、页面和 schema。"""

        for source in ("database", "static"):
            with self.subTest(source=source):
                result = compiled_plan(project_plan(source_type=source), execution_scope())
                expected_kinds = {"orders:page": "frontend.page_endpoint_usage"}
                expected_kinds.update({"orders:static": "frontend.static_data_contract"} if source == "static" else
                    {"orders:api": "frontend.api_contract", "orders:controller": "backend.endpoint_contract"})
                for task_id, kind in expected_kinds.items():
                    compiled = result["task_registry"][task_id]
                    checks = {c["kind"]: c for c in compiled["acceptance_checks"]}
                    self.assertEqual(checks["file_operation"]["target_paths"], compiled["target_files"])
                    self.assertEqual(checks["file_operation"]["expected"], {"operation": "modify", "change_type": "modified"})
                    self.assertEqual(checks["scope_boundary"]["expected"]["allowed_paths"], compiled["allowed_paths"])
                    business = compiled["business_acceptance_checks"]
                    self.assertEqual([check["kind"] for check in business], [kind])
                    self.assertEqual(business[0]["target_paths"], compiled["deliverables"][0]["paths"])
                    self.assertEqual(business[0]["verification"]["mode"], "deterministic")
                    endpoint = business[0]["expected"]["endpoints"][0]
                    self.assertEqual((endpoint["api_contract_id"], endpoint["endpoint_id"], endpoint["method"], endpoint["path"]), ("orders-api", "orders.list", "GET", "/orders"))
                    self.assertEqual(endpoint["response_schema"]["properties"]["id"]["type"], "string")
                    self.assertTrue(business[0]["sources"])
                    for ref in business[0]["sources"]:
                        self.assertRegex(ref["sha256"], r"^[0-9a-f]{64}$")
                        self.assertNotIn("customers", ref["pointer"])
                page = result["task_registry"]["orders:page"]
                self.assertTrue({"page_entry", "page_default_export", "page_placeholder"} <= {c["kind"] for c in page["acceptance_checks"]})
                self.assertEqual(page["business_acceptance_checks"][0]["expected"]["required_endpoint_ids"], ["orders.list"])

    def test_business_acceptance_fingerprint_changes_only_with_related_formal_input(self) -> None:
        """同一正式输入稳定编译，无关 Endpoint 不污染当前检查，相关变更更新来源指纹。"""

        plan = project_plan()
        original = compiled_plan(plan, execution_scope())["task_registry"]["orders:api"]["business_acceptance_checks"]
        self.assertEqual(original, compiled_plan(plan, execution_scope())["task_registry"]["orders:api"]["business_acceptance_checks"])
        plan["api_contracts"][1]["endpoints"][0]["path"] = "/changed-customers"
        self.assertEqual(original, compiled_plan(plan, execution_scope())["task_registry"]["orders:api"]["business_acceptance_checks"])
        plan["api_contracts"][0]["endpoints"][0]["path"] = "/changed-orders"
        changed = compiled_plan(plan, execution_scope())["task_registry"]["orders:api"]["business_acceptance_checks"]
        self.assertNotEqual(original[0]["sources"], changed[0]["sources"])
        self.assertNotEqual(original[0]["id"], changed[0]["id"])
        self.assertEqual(changed[0]["expected"]["endpoints"][0]["path"], "/changed-orders")

    def test_authorization_compile_keeps_platform_slices_and_exact_any_of(self) -> None:
        """权限切片进入页面与 Controller 验收，候选不能覆盖平台权限事实。"""

        plan = project_plan(authorization=True)
        context = build_context(plan, execution_scope())
        candidates = candidate_tasks(context)
        for candidate in candidates:
            candidate["source_refs"] = {"authorization": {"actions": [{"resourceKey": "forged"}]}}
        result = create_build_task_plan(plan, agent_plan={"tasks": candidates}, build_context=context,
            base_build_task_plan=ensure_build_unit_skeleton(plan, workspace_snapshot()))
        self.assertTrue(result["task_graph"]["validation"]["is_valid"])
        registry = result["task_registry"]
        page = next(c["expected"] for c in registry["orders:page"]["acceptance_checks"] if c["kind"] == "frontend_authorization")
        self.assertEqual([a["resourceKey"] for a in page["controlledActions"]], ["orders_list"])
        backend = next(c["expected"] for c in registry["orders:controller"]["acceptance_checks"] if c["kind"] == "backend_authorization")
        self.assertEqual(backend["semantics"], "ANY_OF")
        self.assertEqual(backend["operationResourceKeys"], ["orders_list"])
        self.assertEqual(backend["authConstants"], [{"name": "ORDERS_LIST_RESOURCE", "resourceKey": "orders_list"}])
        self.assertEqual(backend["endpointIdentity"], {"apiContractId": "orders-api", "endpointId": "orders.list", "httpMethod": "GET", "path": "/orders"})
        self.assertNotIn("authorization", registry["orders:api"]["source_refs"])

    def test_preserved_confirmed_contract_is_not_recompiled_for_another_scope(self) -> None:
        """显式保留的 confirmed 任务不被另一 scope 的编译上下文覆盖。"""

        plan = project_plan()
        baseline = confirmed_baseline(plan, execution_scope())
        before = deepcopy(baseline)
        candidate = task("customers:page", "page:customers", "frontend.page", "frontend/src/pages/Customers/index.tsx", "customers")
        context = build_context(plan, execution_scope(name="customers"))
        result = compile_build_task_plan_scope(baseline, [*tasks_from_build_task_plan(baseline), candidate], context,
            validate_task_scope=False, preserve_compiled_task_ids=set(baseline["task_registry"]))
        self.assertTrue(result["task_graph"]["validation"]["is_valid"])
        for task_id, retained in before["task_registry"].items():
            for key in ("source_refs", "deliverables", "acceptance_checks", "business_acceptance_checks", "status"):
                self.assertEqual(result["task_registry"][task_id][key], retained[key])
        self.assertEqual(baseline, before)

    def test_build_execution_scope_excludes_unrelated_tasks_and_reuses_completed_prerequisites(self) -> None:
        """Build 接口按 Unit 依赖闭包切片，不执行无关页面或重复执行已完成前置任务。"""

        for source, auth in (("database", False), ("static", False), ("database", True)):
            with self.subTest(source=source, authorization=auth):
                plan = project_plan(source_type=source, authorization=auth)
                baseline = confirmed_baseline(plan, execution_scope())
                tasks = tasks_from_build_task_plan(baseline)
                tasks.append(task("customers:page", "page:customers", "frontend.page", "frontend/src/pages/Customers/index.tsx", "customers"))
                original = deepcopy(tasks)
                sliced = resolve_execution_slice(build_task_plan=baseline, tasks=tasks, build_execution_scope=execution_scope())
                self.assertEqual(set(sliced["task_ids"]), set(baseline["task_registry"]))
                self.assertNotIn("page:customers", sliced["unit_ids"])
                expected_reused = set() if source == "static" else {"api:adapter", "backend:bootstrap::config"}
                self.assertEqual(set(sliced["reusable_task_ids"]), expected_reused)
                self.assertEqual(set(sliced["pending_task_ids"]), set(baseline["task_registry"]) - expected_reused)
                if source == "database":
                    endpoint = resolve_execution_slice(build_task_plan=baseline, tasks=tasks, build_execution_scope=execution_scope("endpoint"))
                    self.assertEqual(set(endpoint["task_ids"]), {"backend:bootstrap::config", "orders:controller"})
                    self.assertEqual(endpoint["pending_task_ids"], ["orders:controller"])
                self.assertEqual(tasks, original)
