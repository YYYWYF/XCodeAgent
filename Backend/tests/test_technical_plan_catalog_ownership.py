"""复现接口目录越界、错误候选缓存和原三轮预算内的定向修复。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.agents.main import planner
from app.agents.main.technical_plan_catalog_parts_validation import catalog_registry, validate_endpoint_directory
from app.agents.main.technical_plan_contract_budget import fragment_digest
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.config import Settings
from app.graph.nodes import planning
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from tests.technical_plan_catalog_fixture import catalog_response
from tests.test_technical_plan_catalog_parts import CatalogModel
from tests.test_technical_plan_modular_generation import modular_fixture


class TechnicalPlanCatalogOwnershipTests(TestCase):
    """使用真实目录校验和持久化，只替换模型响应及最终页面契约验证。"""

    def setUp(self):
        """准备订单和客户两个独立能力以及共享订单接口的两个页面。"""

        self.requirement, self.catalog, self.contracts, self.pages = modular_fixture()
        self.registry = catalog_registry(self.catalog)

    def validate(self, endpoints, *, index=0, completed=None):
        """按冻结全局身份验证指定能力的接口目录。"""

        return validate_endpoint_directory({"endpoints": endpoints}, identity=self.registry["contracts"][index],
            entities=self.catalog["entities"], completed=completed or [],
            product=self.requirement["confirmed_product_plan"], registry=self.registry)

    def test_foreign_namespace_and_route_are_rejected_before_caching(self):
        """命名空间、路径越界和相似字符串前缀均不能冒充当前业务能力。"""

        original = self.catalog["contracts"][0]["endpoints"][0]
        foreign = self.catalog["contracts"][1]["endpoints"][0]
        for endpoint in (foreign, {**original, "id": foreign["id"]},
                {**original, "path": foreign["path"]}, {**original, "path": "/api/orders-archive"}):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValueError, "orders_api 接口目录越界") as failure:
                self.validate([endpoint])
            self.assertIn(endpoint["id"], str(failure.exception))
            self.assertIn(endpoint["path"], str(failure.exception))
        # 当前协议允许不带 Contract 前缀的稳定 ID，不能为了归属校验改写已有身份。
        self.validate([{**original, "id": "query_orders"}])

    def test_nested_routes_belong_to_the_most_specific_frozen_capability(self):
        """更具体的路径归属优先；共享相同 base_path 时仍允许不同能力各自定义接口。"""

        self.registry["contracts"][0]["base_path"] = "/api"
        foreign = self.catalog["contracts"][1]["endpoints"][0]
        with self.assertRaisesRegex(ValueError, "customers_api"):
            self.validate([{**foreign, "id": "orders_api.customer_read"}])
        self.validate([foreign], index=1)
        self.registry["contracts"][1]["base_path"] = "/api"
        self.validate([self.catalog["contracts"][0]["endpoints"][0]])

    def test_duplicate_diagnostics_identify_both_endpoints_and_contracts(self):
        """相同 ID 和不同参数名的相同路由都反馈实际冲突来源。"""

        order = deepcopy(self.catalog["contracts"][0])
        customer = self.catalog["contracts"][1]["endpoints"][0]
        for identity in self.registry["contracts"]:
            identity["base_path"] = "/api"
        order["endpoints"] = [{**order["endpoints"][0], "id": "shared.read", "path": "/api/shared/{first}"}]
        for endpoint, expected in (({**customer, "id": "shared.read"}, "Endpoint ID"),
                ({**customer, "path": "/api/shared/{second}"}, "method/path")):
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected) as failure:
                self.validate([endpoint], index=1, completed=[order])
            self.assertIn("orders_api", str(failure.exception))
            self.assertIn("customers_api", str(failure.exception))
            self.assertIn("shared.read", str(failure.exception))

    def test_mixed_first_response_retries_its_owner_in_existing_budget(self):
        """首能力混入客户接口时只重试订单目录，不能冻结错误来源后空转客户目录。"""

        model = CatalogModel(self.catalog, self.contracts, self.pages)
        mixed_once = False

        def invoke(prompt, **kwargs):
            """第一次接口响应混入另一个能力，后续响应由正常受控模型提供。"""

            nonlocal mixed_once
            if "catalog_endpoints" in prompt.splitlines()[0] and not mixed_once:
                mixed_once = True
                model.calls.append(prompt)
                return json.dumps({"endpoints": [endpoint for contract in self.catalog["contracts"]
                    for endpoint in contract["endpoints"]]})
            return model(prompt)

        settings = Settings(model_base_url="https://model.test.invalid/v1", model_api_key="test-key", model_name="test-model")
        with patch.object(planner.Settings, "from_env", return_value=settings), patch.object(
                planner, "_invoke_prompt_with_chat_model", side_effect=invoke), patch.object(
                planning, "_planning_token_callback"), patch.object(planning, "_project_plan_validation_errors", return_value=[]):
            result, errors, failed = planning._generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(result, errors)
        self.assertEqual(errors, [])
        self.assertIsNone(failed)
        self.assertEqual(result["api_contracts"], self.contracts)
        contexts = [json.loads(prompt.splitlines()[-1]) for prompt in model.calls if "catalog_endpoints" in prompt.splitlines()[0]]
        self.assertEqual([context["contract"]["id"] for context in contexts], ["orders_api", "orders_api", "customers_api"])
        self.assertEqual(contexts[0]["other_contracts"], [{"id": "customers_api", "name": "客户管理", "base_path": "/api/customers"}])
        retry_prompt = [prompt for prompt in model.calls if "catalog_endpoints" in prompt.splitlines()[0]][1]
        self.assertIn("接口目录越界", retry_prompt)
        self.assertIn("customers_api.read", retry_prompt)
        self.assertEqual(sum("catalog_registry" in prompt.splitlines()[0] for prompt in model.calls), 1)

    def test_current_invalid_cache_is_removed_durably_and_resumes_after_disconnect(self):
        """复现现场已缓存混合目录，修复来源后再次断线仍只继续尚未完成的客户模块。"""

        with TemporaryDirectory() as workspace:
            requirement = {**self.requirement, "technical_plan_generation_workspace": workspace}

            def before_endpoints(prompt):
                """生成身份及实体后断开，拿到真实生成入口计算的输入身份和片段目录。"""

                if "catalog_endpoints" in prompt.splitlines()[0]:
                    raise ConnectionError("尚未生成接口")
                return json.dumps(catalog_response(prompt, self.catalog))

            with self.assertRaisesRegex(ConnectionError, "尚未生成接口"):
                generate_technical_plan_sections(requirement, invoke=before_endpoints)
            store = TechnicalPlanFragmentStore(workspace)
            directory = json.loads(store.path.read_text())
            fingerprint = directory["inputSha256"]
            untouched = deepcopy(directory["completed"])
            prefix = "catalog-part:" + fragment_digest({"initial": True}) + ":"
            key = prefix + "endpoints:" + fragment_digest({"identity": self.registry["contracts"][0],
                "entities": self.catalog["entities"], "completed": []})
            mixed = {"endpoints": [endpoint for contract in self.catalog["contracts"] for endpoint in contract["endpoints"]]}
            # 当前候选形状与现场一致；不引入格式迁移或手工修改正式产物。
            store.save(fingerprint, key, mixed)
            calls = []

            def repair_then_disconnect(prompt):
                """确认错误引用已撤下，再写回正确订单目录，并在客户模型调用前模拟断线。"""

                context = json.loads(prompt.splitlines()[-1])
                calls.append(context["contract"]["id"])
                if context["contract"]["id"] == "orders_api":
                    self.assertNotIn(key, store.load(fingerprint))
                    self.assertIn("接口目录越界", prompt)
                    return json.dumps(catalog_response(prompt, self.catalog))
                raise ConnectionError("修复订单后连接中断")

            with self.assertRaisesRegex(ConnectionError, "修复订单后连接中断"):
                generate_technical_plan_sections(requirement, invoke=repair_then_disconnect)
            self.assertEqual(calls, ["orders_api", "customers_api"])
            self.assertEqual(store.load(fingerprint)[key], {"endpoints": self.catalog["contracts"][0]["endpoints"]})
            after = json.loads(store.path.read_text())["completed"]
            self.assertTrue(all(after[name] == reference for name, reference in untouched.items()))
            resumed = CatalogModel(self.catalog, self.contracts, self.pages)
            result = generate_technical_plan_sections(requirement, invoke=resumed)
            first = json.loads(resumed.calls[0].splitlines()[-1])
            self.assertEqual(first["contract"]["id"], "customers_api")
            self.assertEqual(result["api_contracts"], self.contracts)
            self.assertEqual(result["pages"], self.pages)
            self.assertEqual(sum("catalog_endpoints" in prompt.splitlines()[0] for prompt in resumed.calls), 1)
            self.assertNotIn(key, store.load(fingerprint))
            self.assertFalse((Path(workspace) / ".devagentstudio/plans/technical-plan.json").exists())
