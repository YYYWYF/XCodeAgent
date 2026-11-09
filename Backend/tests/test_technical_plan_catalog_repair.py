"""验证目录缺口向上修复、依赖失效和中断后的原子恢复。"""

from copy import deepcopy
import json
import tempfile
from unittest import TestCase
from unittest.mock import patch

from app.agents.main import planner
from app.agents.main.technical_plan_catalog_repair import CatalogRepairRequired, repaired_catalog_fragments
from app.agents.main.technical_plan_contract_budget import contract_part_prefix
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.agents.main.technical_plan_generation_validation import validate_catalog
from app.graph.nodes.planning import _generate_valid_technical_plan
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from tests.test_technical_plan_modular_generation import modular_fixture, fragment_outputs
from tests.technical_plan_catalog_fixture import catalog_responses


class TechnicalPlanCatalogRepairTests(TestCase):
    """受控输出只替换模型，实际执行覆盖校验、预算和存储原子更新。"""

    def setUp(self):
        """构造订单目录漏详情接口、客户模块已完成的场景。"""

        self.requirement, self.catalog, self.contracts, self.pages = modular_fixture()
        product = self.requirement["confirmed_product_plan"]
        product["pages"] = [product["pages"][2], *product["pages"][:2]]
        self.pages = [self.pages[2], *self.pages[:2]]
        page_order = {page["pageId"]: index for index, page in enumerate(product["pages"])}
        self.catalog["action_bindings"].sort(key=lambda binding: page_order[binding["pageId"]])
        self.issue = {"catalog_issue": {"pageId": "order_detail", "kind": "business_action",
            "actionId": "refresh", "stepId": None, "reason": "详情必须按编号读取，目录只提供列表读取。"}}
        self.fixed = deepcopy(self.catalog)
        endpoint = {"id": "orders_api.detail", "name": "读取订单详情", "method": "GET",
            "path": "/api/orders/{code}", "summary": "按编号读取订单详情。"}
        self.fixed["contracts"][0]["endpoints"].append(endpoint)
        for binding in self.fixed["action_bindings"]:
            if binding["pageId"] == "order_detail":
                binding["endpointId"] = endpoint["id"]
        self.fixed_contract = deepcopy(self.contracts[0])
        self.fixed_contract["endpoints"].append({**endpoint, "parameters": [
            {"name": "code", "in": "path", "required": True, "schema": {"type": "string"}}],
            "request_schema_ref": None, "response_schema_ref": "Output", "error_codes": [],
            "authentication": {"required": False}})
        self.fixed_page = deepcopy(self.pages[2])
        self.fixed_page["references"]["endpoint_dependencies"][0]["endpoint_id"] = endpoint["id"]
        self.fixed_page["references"]["action_implementations"][0]["endpointId"] = endpoint["id"]

    def _invoke(self, values, calls):
        """按队列返回受控输出并记录实际阶段，异常用来模拟连接失败。"""

        responses = iter(values)

        def invoke(prompt):
            """记录模块提示词并模拟一次原模型请求。"""

            calls.append(prompt)
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return json.dumps(value, ensure_ascii=False)

        return invoke

    def _initial_outputs(self):
        """两个契约和前两页成功后，详情页报告语义目录缺口。"""

        return [*catalog_responses(self.catalog, page_ids=[page["pageId"] for page in self.pages]), *[{"api_contracts": [c]} for c in self.contracts],
            *[{"pages": [p]} for p in self.pages[:2]], self.issue]

    def _repair_outputs(self):
        """修目录后只重生成订单契约及订单两页，不重做客户模块。"""

        return [*catalog_responses(self.fixed, entity_ids={"Order"}, contract_ids={"orders_api"}, page_ids=["orders", "order_detail"]),
            {"api_contracts": [self.fixed_contract]},
            {"pages": [self.pages[1]]}, {"pages": [self.fixed_page]}]

    def test_catalog_requires_exact_business_action_and_step_coverage(self):
        """空目录、漏业务步骤、重复或越界绑定在生成 Contract 前即被拒绝。"""

        for kind in ("empty", "missing_step", "duplicate", "unknown_action", "unknown_endpoint", "wrong_page"):
            with self.subTest(kind=kind):
                catalog = deepcopy(self.catalog)
                if kind == "empty":
                    catalog = {"entities": [], "contracts": [], "action_bindings": []}
                elif kind == "missing_step":
                    catalog["action_bindings"] = [b for b in catalog["action_bindings"] if b["stepId"] is None]
                elif kind == "duplicate":
                    catalog["action_bindings"].append(deepcopy(catalog["action_bindings"][0]))
                elif kind == "unknown_action":
                    catalog["action_bindings"][0]["actionId"] = "not_declared"
                elif kind == "unknown_endpoint":
                    catalog["action_bindings"][0]["endpointId"] = "not_declared"
                else:
                    catalog["contracts"][0]["page_ids"] = ["order_detail"]
                with self.assertRaises(ValueError):
                    validate_catalog(catalog, self.requirement["confirmed_product_plan"])

    def test_repair_invalidates_all_affected_contract_children(self):
        """目录变更撤销受影响对象目录、字段、接口与缩批标记，其他 Contract 子片段保留。"""

        affected = contract_part_prefix("orders_api")
        untouched = contract_part_prefix("orders_api:other")
        cache = {"catalog_repair": {"catalog": self.catalog, "baseline": self.catalog, "issue": self.issue["catalog_issue"]},
            **{affected + kind: {"value": kind} for kind in ("plan", "schema:Output:one", "endpoints:one", "endpoints:one:split")},
            untouched + "plan": {"value": "保留"}, "contract:orders_api": self.contracts[0],
            "contract:customers_api": self.contracts[1]}
        retained = repaired_catalog_fragments(cache, self.fixed)
        self.assertFalse(any(key.startswith(affected) for key in retained))
        self.assertIn(untouched + "plan", retained)
        self.assertIn("contract:customers_api", retained)
        self.assertNotIn("contract:orders_api", retained)

    def test_empty_catalog_retries_catalog_instead_of_looping_on_pages(self):
        """复现原 P0：漏全部接口时下一次模型请求必须仍是 catalog，不能走页面空转。"""

        outputs = [json.dumps({"entities": [], "contracts": []}),
            *fragment_outputs(self.catalog, self.contracts, self.pages)]
        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=outputs) as model, patch(
            "app.graph.nodes.planning._project_plan_validation_errors", return_value=[]), patch(
            "app.graph.nodes.planning._planning_token_callback"):
            plan, errors, _ = _generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(plan, errors)
        self.assertIn("catalog", model.call_args_list[1].args[0].splitlines()[0])
        self.assertIn("目录遗漏业务", model.call_args_list[1].args[0])

    def test_semantic_issue_repairs_catalog_and_preserves_unaffected_modules(self):
        """完整预算内能补缺失接口，并且只重做受影响的 Contract 与页面。"""

        outputs = [json.dumps(v, ensure_ascii=False) for v in self._initial_outputs() + self._repair_outputs()]
        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=outputs) as model, patch(
            "app.graph.nodes.planning._project_plan_validation_errors", return_value=[]), patch(
            "app.graph.nodes.planning._planning_token_callback"):
            plan, errors, _ = _generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(plan, errors)
        self.assertEqual(plan["api_contracts"], [self.fixed_contract, self.contracts[1]])
        self.assertEqual(plan["pages"], [*self.pages[:2], self.fixed_page])
        prompts = [call.args[0] for call in model.call_args_list]
        repair_index = len(self._initial_outputs())
        repair_count = len(catalog_responses(self.fixed, entity_ids={"Order"}, contract_ids={"orders_api"}, page_ids=["orders", "order_detail"]))
        self.assertEqual(len(prompts), repair_index + len(self._repair_outputs()))
        self.assertIn("共享目录修复任务", prompts[repair_index])
        for prompt in prompts[repair_index + repair_count:]:
            self.assertNotIn(self.issue["catalog_issue"]["reason"], prompt)
            self.assertIn(self.requirement["planning_adjustment_request"], prompt)
        self.assertEqual(sum("单个 API Contract" in p and '"id": "customers_api"' in p for p in prompts), 1)
        self.assertEqual(sum('目标页面：{"pageId": "customers"' in p for p in prompts), 1)

    def test_pending_repair_and_invalidation_survive_connection_errors(self):
        """缺口和新目录失效均即时落盘，无内存的新调用也不会复用已过期订单片段。"""

        with tempfile.TemporaryDirectory() as workspace:
            requirement = {**self.requirement, "technical_plan_generation_workspace": workspace}
            calls = []
            with self.assertRaises(CatalogRepairRequired):
                generate_technical_plan_sections(requirement, invoke=self._invoke(self._initial_outputs(), calls))
            store = TechnicalPlanFragmentStore(workspace)
            digest = json.loads(store.path.read_text())["inputSha256"]
            pending = store.load(digest)
            self.assertNotIn("catalog", pending)
            self.assertIn("catalog_repair", pending)
            with self.assertRaises(ConnectionError):
                generate_technical_plan_sections(requirement, invoke=self._invoke([ConnectionError("目录连接失败")], calls))
            self.assertEqual(store.load(digest), pending)
            with self.assertRaises(ConnectionError):
                generate_technical_plan_sections(requirement,
                    invoke=self._invoke(self._repair_outputs()[:-3] + [ConnectionError("订单连接失败")], calls))
            published = store.load(digest)
            self.assertEqual(set(published), {"catalog", "contract:customers_api", "page:customers"})
            self.assertEqual(published["catalog"], self.fixed)
            self.assertEqual(published["page:customers"], self.pages[0])
            plan = generate_technical_plan_sections(requirement, invoke=self._invoke(self._repair_outputs()[-3:], calls))
            self.assertEqual(plan["pages"][-1], self.fixed_page)

    def test_binding_stage_issue_repairs_directory_before_full_contract_generation(self):
        """逐页目录映射报告缺口时，先修目录并复用其他实体，不提前生成完整 Contract。"""

        initial = catalog_responses(self.catalog, page_ids=[page["pageId"] for page in self.pages])
        repaired = self._repair_outputs()[:-3]
        values = [*initial[:-1], self.issue, *repaired,
            {"api_contracts": [self.fixed_contract]}, {"api_contracts": [self.contracts[1]]},
            {"pages": [self.pages[0]]}, {"pages": [self.pages[1]]}, {"pages": [self.fixed_page]}]
        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=[json.dumps(value) for value in values]) as model, patch(
            "app.graph.nodes.planning._project_plan_validation_errors", return_value=[]), patch(
            "app.graph.nodes.planning._planning_token_callback"):
            plan, errors, _ = _generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(plan, errors)
        self.assertEqual(plan["api_contracts"], [self.fixed_contract, self.contracts[1]])
        prompts = [call.args[0] for call in model.call_args_list]
        self.assertFalse(any("单个 API Contract" in prompt.splitlines()[0] for prompt in prompts[:len(initial)]))
        customer_entities = [prompt for prompt in prompts if "catalog_entity" in prompt.splitlines()[0]
            and json.loads(prompt.splitlines()[-1])["entity"]["id"] == "Customer"]
        self.assertEqual(len(customer_entities), 1)

    def test_noop_catalog_repair_remains_pending_and_uses_existing_budget(self):
        """模型原样返回目录不会清掉缺口；同一目录修复链三轮内收口。"""

        progress = {}
        noop = catalog_responses(self.catalog, entity_ids={"Order"}, contract_ids={"orders_api"}, page_ids=["order_detail"])
        outputs = [json.dumps(v) for v in self._initial_outputs() + noop + noop]
        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=outputs) as model, patch(
            "app.graph.nodes.planning._planning_token_callback"):
            plan, errors, _ = _generate_valid_technical_plan({}, self.requirement, None, generation_fragments=progress)
        self.assertIsNone(plan)
        self.assertIn("修复未调整", errors[0])
        self.assertEqual(model.call_count, len(self._initial_outputs()) + len(noop) * 2)
        self.assertNotIn("catalog", progress["completed"])
        self.assertIn("catalog_repair", progress["completed"])

    def test_malformed_issue_does_not_invalidate_directory(self):
        """普通页面错误或未知动作、非业务步骤、混合输出不能撤下已验证目录。"""

        for kind in ("page", "action", "step", "mixed"):
            with self.subTest(kind=kind):
                issue = deepcopy(self.issue)
                if kind == "mixed":
                    issue["pages"] = []
                else:
                    field = {"page": "pageId", "action": "actionId", "step": "stepId"}[kind]
                    issue["catalog_issue"][field] = "unknown"
                progress = {}
                calls = []
                with self.assertRaises(ValueError):
                    generate_technical_plan_sections(self.requirement,
                        invoke=self._invoke(self._initial_outputs()[:-1] + [issue] * 3, calls), fragments=progress)
                self.assertIn("catalog", progress["completed"])
                self.assertNotIn("catalog_repair", progress["completed"])

    def test_atomic_publication_failure_keeps_previous_dependency_set(self):
        """新目录发布失败时保留待修复快照，不出现新目录与旧受影响片段的混合版本。"""

        with tempfile.TemporaryDirectory() as workspace:
            requirement = {**self.requirement, "technical_plan_generation_workspace": workspace}
            with self.assertRaises(CatalogRepairRequired):
                generate_technical_plan_sections(requirement, invoke=self._invoke(self._initial_outputs(), []))
            store = TechnicalPlanFragmentStore(workspace)
            digest = json.loads(store.path.read_text())["inputSha256"]
            before = store.load(digest)
            with patch("app.services.technical_plan_fragments.os.replace", side_effect=OSError("磁盘写入失败")):
                with self.assertRaises(OSError):
                    generate_technical_plan_sections(requirement, invoke=self._invoke(self._repair_outputs(), []))
            self.assertEqual(store.load(digest), before)

    def test_shared_entity_change_invalidates_all_consuming_contracts(self):
        """实体字段变化会失效依赖契约，不能仅比较 Contract 目录字段。"""

        fixed = deepcopy(self.catalog)
        fixed["entities"][0]["fields"][0]["description"] += "用于详情查询。"
        cache = {"catalog_repair": {"catalog": self.catalog, "baseline": self.catalog, "issue": self.issue["catalog_issue"]},
            "contract:orders_api": self.contracts[0], "contract:customers_api": self.contracts[1],
            **{f"page:{page['pageId']}": page for page in self.pages}}
        result = repaired_catalog_fragments(cache, fixed)
        self.assertEqual(set(result), {"catalog", "contract:customers_api", "page:customers"})
