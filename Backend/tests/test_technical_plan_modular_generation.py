"""受控模型驱动真实分段生成、局部校验、拼接与全量修复入口。"""

from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import patch

from app.agents.main import planner
from app.agents.model_business_context import current_model_context
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.agents.main.technical_plan_generation_validation import validate_catalog
from app.config import Settings
from app.graph.nodes import planning
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.page_implementation_contract import validate_page_implementation_contracts
from app.services.project_plan import validate_technical_plan_api_contracts
from app.services.run_workers import bind_run_workers
from app.services.workspace_process_registry import workspace_process_registry
from tests.technical_plan_catalog_fixture import catalog_responses


def modular_fixture() -> tuple[dict, dict, list, list]:
    """构造两种实体、两个共享契约和三个页面，包含同名动作及组合业务步骤。"""

    entities, specs, contracts, pages, product_pages = [], [], [], [], []
    for entity_id, name in (("Order", "订单"), ("Customer", "客户")):
        contract_id = entity_id.lower() + "s_api"
        endpoint = {"id": contract_id + ".read", "name": f"查询{name}", "method": "GET", "path": "/api/" + entity_id.lower() + "s", "summary": f"读取{name}业务信息。"}
        entities.append({"id": entity_id, "name": name, "description": f"记录{name}信息。", "fields": [
            {"name": "code", "label": f"{name}编号", "description": f"标识{name}的编号。", "type": "text", "required": True},
        ]})
        spec = {"id": contract_id, "name": name + "管理", "entity_ids": [entity_id], "base_path": endpoint["path"], "page_ids": [], "endpoints": [endpoint]}
        specs.append(spec)
        contracts.append({key: deepcopy(spec[key]) for key in ("id", "name", "entity_ids", "base_path")})
        contracts[-1].update(authentication={"required": False}, schemas={"Output": {"type": "object", "properties": {
            "code": {"type": "string", "description": f"{name}编号。", "entity_field_ref": entity_id + ".code"},
        }, "required": ["code"]}}, endpoints=[{
            **endpoint, "parameters": [], "request_schema_ref": None, "response_schema_ref": "Output",
            "error_codes": [], "authentication": {"required": False},
        }])
    for page_id, spec in (("orders", specs[0]), ("order_detail", specs[0]), ("customers", specs[1])):
        spec["page_ids"].append(page_id)
        endpoint_id = spec["endpoints"][0]["id"]
        behavior = {"type": "business", "expectedResult": "显示当前业务信息。"}
        binding = {"actionId": "refresh", "endpointId": endpoint_id}
        if page_id == "customers":
            behavior = {"type": "sequence", "expectedResult": "更新并显示客户信息。", "steps": [
                {"stepId": "load", "type": "business", "expectedResult": "读取客户信息。"},
                {"stepId": "show", "type": "interface", "expectedResult": "展开详情区域。"},
            ]}
            binding = {"actionId": "refresh", "stepBindings": [{"stepId": "load", "endpointId": endpoint_id}]}
        product_pages.append({"pageId": page_id, "name": "业务页面", "path": "/" + page_id, "module_id": spec["id"],
            "goal": "查看当前业务信息。", "information_items": [{"itemId": "code", "label": "编号", "description": "业务编号。"}],
            "actions": [{"actionId": "refresh", "name": "刷新", "description": "读取最新信息。", "requiresConfirmation": False, "behavior": behavior}],
            "navigation_targets": []})
        pages.append({"pageId": page_id, "references": {"endpoint_dependencies": [
            {"endpoint_id": endpoint_id, "usage": "page_load", "trigger": "进入页面时读取信息。", "required_for_initial_load": True},
        ], "action_implementations": [binding]}})
    requirement = {"confirmed_product_plan": {"app": {"name": "订单客户应用", "summary": "管理订单和客户。"},
        "pages": product_pages, "business_flows": [], "authorizationTargets": {"pageRules": [], "operationRules": []}},
        "application_config": {"configRevision": 1, "auth": {"enable": False, "privateKey": "SECRET-DO-NOT-PROMPT"},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []}},
        "confirmed_ui_design_sha256": "ui-current", "planning_adjustment_request": "保留全部页面和业务操作。"}
    return requirement, {"entities": entities, "contracts": specs, "action_bindings": catalog_bindings(pages)}, contracts, pages


def catalog_bindings(pages: list[dict]) -> list[dict]:
    """从受控页面业务实现生成当前目录覆盖夹具，不把本地界面效果写成接口。"""

    bindings = []
    for page in pages:
        for action in page["references"]["action_implementations"]:
            if action.get("endpointId"):
                bindings.append({"pageId": page["pageId"], "actionId": action["actionId"], "stepId": None,
                    "endpointId": action["endpointId"]})
            for step in action.get("stepBindings", []):
                if step.get("endpointId"):
                    bindings.append({"pageId": page["pageId"], "actionId": action["actionId"],
                        "stepId": step["stepId"], "endpointId": step["endpointId"]})
    return bindings


def fragment_outputs(catalog: dict, contracts: list, pages: list) -> list[str]:
    """按实际调用顺序编码共享目录、单 Contract 和单页面的受控模型响应。"""

    return [json.dumps(value, ensure_ascii=False) for value in [*catalog_responses(catalog),
        *[{"api_contracts": [contract]} for contract in contracts], *[{"pages": [page]} for page in pages]]]


class TechnicalPlanModularGenerationTests(TestCase):
    """验证拆分能缩小上下文、复用成功片段且不绕过正式完整校验。"""

    def setUp(self):
        """每个用例使用独立共享目录和不连接远端的模型配置。"""

        self.requirement, self.catalog, self.contracts, self.pages = modular_fixture()
        self.catalog_count = len(catalog_responses(self.catalog))
        self.settings = Settings(model_base_url="https://model.test.invalid/v1", model_api_key="test-key", model_name="test-model")

    def _generate(self, outputs: list, *, fragments: dict | None = None):
        """执行真实规划入口，只替换最外层模型响应并记录逐段提示词。"""

        with patch.object(planner.Settings, "from_env", return_value=self.settings), patch.object(
            planner, "_invoke_prompt_with_chat_model", side_effect=outputs,
        ) as model:
            plan = planner.plan_project_with_chat_model(self.requirement, generation_fragments=fragments)
        return plan, [call.args[0] for call in model.call_args_list]

    def test_fragment_generation_retains_upstream_model_context_and_cache(self):
        """合并模型转发上下文与分段缓存参数后，每次片段调用仍保留用途并复用已完成内容。"""
        outputs = iter(fragment_outputs(self.catalog, self.contracts, self.pages))
        contexts = []

        def invoke(prompt, **kwargs):
            """只替换远端响应，读取真实分段调用所继承的模型上下文。"""
            contexts.append(current_model_context({}))
            return next(outputs)

        fragments = {}
        with patch.object(planner.Settings, "from_env", return_value=self.settings), patch.object(
            planner, "_invoke_prompt_with_chat_model", side_effect=invoke,
        ):
            first = planner.plan_project_with_chat_model(self.requirement, generation_fragments=fragments)
            call_count = len(contexts)
            second = planner.plan_project_with_chat_model(self.requirement, generation_fragments=fragments)
        self.assertEqual(first, second)
        self.assertGreater(call_count, 0)
        self.assertEqual(len(contexts), call_count)
        self.assertTrue(all(item.get("business_stage") == "technical_planning" and
            item.get("business_operation") == "generate_technical_plan" for item in contexts))

    def test_multi_page_generation_assembles_existing_contract_and_scopes_context(self):
        """共享接口仅生成一次，逐页绑定同名动作，Schema 名只在各自契约内解释。"""

        original = deepcopy(self.requirement)
        plan, prompts = self._generate(fragment_outputs(self.catalog, self.contracts, self.pages))
        self.assertEqual(len(prompts), self.catalog_count + 5)
        self.assertEqual(plan["api_contracts"], self.contracts)
        self.assertEqual(plan["pages"], self.pages)
        self.assertEqual(plan["entities"][0]["id"], "Order")
        self.assertIn("Java8", plan["architecture"]["backend"])
        self.assertIn("MySQL8", plan["architecture"]["data"])
        self.assertEqual(validate_technical_plan_api_contracts(plan), [])
        self.assertEqual(validate_api_contract_consistency(plan), [])
        ui = {"pages": [{"pageId": page["pageId"], "bindings": {"actions": [{
            "actionId": "refresh", "controlIds": ["refresh-control"],
            "stepEffects": [{"stepId": "show", "uiEffect": "展开详情区域。"}],
        }]}} for page in self.pages]}
        self.assertEqual(validate_page_implementation_contracts(plan, self.requirement["confirmed_product_plan"], ui), [])
        self.assertNotIn('"Customer"', prompts[self.catalog_count])
        self.assertNotIn('"order_detail"', prompts[self.catalog_count + 1])
        self.assertNotIn('"response_schema_ref"', prompts[self.catalog_count + 2])
        self.assertNotIn('"schemas"', prompts[self.catalog_count + 2])
        self.assertNotIn('"pageId": "customers"', prompts[self.catalog_count + 2])
        for prompt in prompts:
            self.assertIn(self.requirement["planning_adjustment_request"], prompt)
            self.assertNotIn("SECRET-DO-NOT-PROMPT", prompt)
        self.assertEqual(self.requirement, original)

    def test_invalid_contract_retries_only_failed_module_with_own_budget(self):
        """第二模块 Schema 错误只重试该模块，不重复实体或第一个成功契约。"""

        outputs = fragment_outputs(self.catalog, self.contracts, self.pages)
        broken = deepcopy(self.contracts[1])
        broken["endpoints"][0]["response_schema_ref"] = "MissingOutput"
        outputs.insert(self.catalog_count + 1, json.dumps({"api_contracts": [broken]}))
        with patch.object(planner.Settings, "from_env", return_value=self.settings), patch.object(
            planner, "_invoke_prompt_with_chat_model", side_effect=outputs,
        ) as model, patch.object(planning, "_planning_token_callback"), patch.object(
            planning, "_project_plan_validation_errors", return_value=[],
        ), patch.object(planner, "edit_technical_plan_with_agent", side_effect=AssertionError("片段校验不应进入完整候选编辑器")) as editor:
            plan, errors, failed = planning._generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(plan, errors)
        self.assertEqual(errors, [])
        self.assertIsNone(failed)
        self.assertEqual(model.call_count, self.catalog_count + 6)
        self.assertIn("MissingOutput", model.call_args_list[self.catalog_count + 2].args[0])
        editor.assert_not_called()

    def test_failed_page_preserves_contracts_and_previous_pages(self):
        """缺失业务步骤时只重生成失败页面，并保留其他页面和接口。"""

        outputs = fragment_outputs(self.catalog, self.contracts, self.pages)
        broken = deepcopy(self.pages[-1])
        broken["references"]["action_implementations"][0]["stepBindings"] = []
        outputs.insert(self.catalog_count + 4, json.dumps({"pages": [broken]}))
        with patch.object(planner.Settings, "from_env", return_value=self.settings), patch.object(
            planner, "_invoke_prompt_with_chat_model", side_effect=outputs,
        ) as model, patch.object(planning, "_planning_token_callback"), patch.object(
            planning, "_project_plan_validation_errors", return_value=[],
        ), patch.object(planner, "edit_technical_plan_with_agent", side_effect=AssertionError("片段校验不应进入完整候选编辑器")):
            plan, errors, _ = planning._generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(plan, errors)
        self.assertEqual(model.call_count, self.catalog_count + 6)
        self.assertIn("load", model.call_args_list[-1].args[0])

    def test_exhausted_fragment_budget_preserves_completed_fragments_for_retry(self):
        """持续失败仅消耗本模块三次机会，已完成片段能在同输入的后续调用复用。"""

        progress = {}
        outputs = fragment_outputs(self.catalog, self.contracts, [])[:self.catalog_count + 1] + ['{"api_contracts": ['] * 3
        with patch.object(planner.Settings, "from_env", return_value=self.settings), patch.object(
            planner, "_invoke_prompt_with_chat_model", side_effect=outputs,
        ) as model, patch.object(planning, "_planning_token_callback"):
            plan, errors, failed = planning._generate_valid_technical_plan(
                {}, self.requirement, None, generation_fragments=progress,
            )
        self.assertIsNone(plan)
        self.assertIsNone(failed)
        self.assertIn("contract:customers_api", errors[0])
        self.assertEqual(model.call_count, self.catalog_count + 4)
        self.assertEqual(set(progress["completed"]), {"catalog", "contract:orders_api"})
        plan, prompts = self._generate(fragment_outputs(self.catalog, self.contracts, self.pages)[self.catalog_count + 1:], fragments=progress)
        self.assertEqual(len(prompts), 4)
        self.assertEqual(plan["pages"], self.pages)

    def test_global_failure_uses_existing_editor_without_restarting_fragments(self):
        """片段全部完成后出现全局错误，仍沿完整候选局部修复而不重新分段。"""

        sections = {"architecture": {}, "entities": self.catalog["entities"], "api_contracts": self.contracts, "pages": self.pages}
        with patch.object(planner.Settings, "from_env", return_value=self.settings), patch.object(
            planner, "_invoke_prompt_with_chat_model", side_effect=[
                value for output in fragment_outputs(self.catalog, self.contracts, self.pages)
                for value in ('{"broken":', '{}', output)
            ],
        ) as model, patch.object(planning, "_planning_token_callback"), patch.object(
            planning, "_project_plan_validation_errors", side_effect=[["跨页面权限需要修复"], []],
        ), patch.object(planner, "edit_technical_plan_with_agent", return_value=deepcopy(sections)) as editor:
            plan, errors, _ = planning._generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(plan, errors)
        self.assertEqual(model.call_count, (self.catalog_count + 5) * 3)
        editor.assert_called_once()
        self.assertIn("跨页面权限", editor.call_args.args[0]["technical_plan_validation_feedback"])

    def test_changed_input_invalidates_all_cached_fragments(self):
        """产品、UI、配置或原始要求变化均不得混入上一轮片段，诊断变化可复用。"""

        for key in ("product", "ui", "config", "request"):
            with self.subTest(changed=key):
                requirement = deepcopy(self.requirement)
                cache = {}
                values = fragment_outputs(self.catalog, self.contracts, self.pages)
                generate_technical_plan_sections(requirement, invoke=lambda prompt: values.pop(0), fragments=cache)
                old_digest = cache["input_sha256"]
                if key == "product":
                    requirement["confirmed_product_plan"]["app"]["summary"] += "更新。"
                elif key == "ui":
                    requirement["confirmed_ui_design_sha256"] = "ui-new"
                elif key == "config":
                    requirement["application_config"]["configRevision"] += 1
                else:
                    requirement["planning_adjustment_request"] += "更新。"
                values = fragment_outputs(self.catalog, self.contracts, self.pages)
                generate_technical_plan_sections(requirement, invoke=lambda prompt: values.pop(0), fragments=cache)
                self.assertEqual(values, [])
                self.assertNotEqual(cache["input_sha256"], old_digest)

    def test_catalog_rejects_duplicate_ids_routes_and_unknown_references(self):
        """拒绝实体、契约、Endpoint 和 HTTP 路由冲突以及未知页面或实体引用。"""

        for kind in ("entity", "contract", "endpoint", "route", "page_ref", "entity_ref"):
            with self.subTest(kind=kind):
                catalog = deepcopy(self.catalog)
                if kind == "entity":
                    catalog["entities"].append(deepcopy(catalog["entities"][0]))
                elif kind == "contract":
                    catalog["contracts"][1]["id"] = catalog["contracts"][0]["id"]
                elif kind in {"endpoint", "route"}:
                    target = catalog["contracts"][1]["endpoints"][0]
                    source = catalog["contracts"][0]["endpoints"][0]
                    target["id" if kind == "endpoint" else "path"] = source["id" if kind == "endpoint" else "path"]
                elif kind == "page_ref":
                    catalog["contracts"][0]["page_ids"] = ["unknown"]
                else:
                    catalog["contracts"][0]["entity_ids"] = ["Unknown"]
                with self.assertRaises(ValueError):
                    validate_catalog(catalog, self.requirement["confirmed_product_plan"])

    def test_contract_cannot_change_frozen_endpoint_identity_or_reference_other_contract(self):
        """模块不得改动共享目录或依赖其他模块中的同名 Schema。"""

        for kind in ("path", "id", "schema", "extra", "type"):
            with self.subTest(kind=kind):
                contract = deepcopy(self.contracts[0])
                if kind in {"path", "id"}:
                    contract["endpoints"][0][kind] += "changed"
                elif kind == "schema":
                    contract["schemas"]["Output"]["properties"]["code"] = {"$ref": "customers_api.Output"}
                elif kind == "extra":
                    contract["data_source_id"] = "forbidden"
                else:
                    contract["schemas"] = []
                with self.assertRaises(ValueError):
                    self._generate(fragment_outputs(self.catalog, [], []) + [json.dumps({"api_contracts": [contract]})] * 3)

    def test_truncated_catalog_does_not_cache_nested_object(self):
        """截断目录不形成可复用候选，不将内部实体误认为完整根对象。"""

        progress = {}
        with self.assertRaisesRegex(ValueError, "catalog-part:.*"):
            self._generate(['{"entities": [{"id": "Order"}], "contracts": ['] * 3, fragments=progress)
        self.assertEqual(progress["completed"], {})

    def test_static_page_generates_no_business_contract(self):
        """纯静态展示仍有页面片段，但无需虚构实体或接口。"""

        self.requirement["confirmed_product_plan"]["pages"] = [{"pageId": "about", "actions": [], "information_items": []}]
        page = {"pageId": "about", "references": {"endpoint_dependencies": [], "action_implementations": []}}
        plan, prompts = self._generate(fragment_outputs({"entities": [], "contracts": [], "action_bindings": []}, [], [page]))
        self.assertEqual(len(prompts), 2)
        self.assertEqual(plan["api_contracts"], [])
        self.assertEqual(plan["entities"], [])

    def test_cancelled_run_does_not_start_next_fragment(self):
        """模块完成时收到停止请求，禁止继续发起后续模型调用。"""

        run_id = "modular-generation-cancel"
        calls = []

        def invoke(prompt):
            """返回共享目录后模拟用户结束当前运行。"""

            calls.append(prompt)
            workspace_process_registry.cancel_run(run_id)
            return json.dumps(catalog_responses(self.catalog)[0])

        try:
            with bind_run_workers(run_id, ""), self.assertRaisesRegex(RuntimeError, "当前运行已停止"):
                generate_technical_plan_sections(self.requirement, invoke=invoke)
        finally:
            workspace_process_registry.allow_run(run_id)
        self.assertEqual(len(calls), 1)
