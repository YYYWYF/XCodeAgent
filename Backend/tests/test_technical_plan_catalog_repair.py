"""验证撤除模型目录否决后仍保留动作覆盖、局部重试和持久化。"""

from copy import deepcopy
import json
import tempfile
from unittest import TestCase
from unittest.mock import patch

from app.agents.main import planner
from app.agents.main.technical_plan_fragment_retry import PlanningFragmentFailed
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.agents.main.technical_plan_generation_validation import validate_catalog, validate_page
from app.graph.nodes.planning import _generate_valid_technical_plan
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from tests.test_technical_plan_modular_generation import modular_fixture, fragment_outputs
from tests.technical_plan_catalog_fixture import catalog_responses


class TechnicalPlanCatalogRepairTests(TestCase):
    """只替换外部模型输出，执行实际覆盖校验、缓存和重试入口。"""

    def setUp(self):
        """准备三个页面及不应触发目录失效的模型审查报告。"""

        self.requirement, self.catalog, self.contracts, self.pages = modular_fixture()
        self.original = deepcopy(self.requirement)
        self.issue = {"catalog_issue": {"pageId": "customers", "kind": "initial_load",
            "actionId": None, "stepId": None, "reason": "模型认为不应实现该页面操作。"}}

    def _invoke(self, values, calls):
        """记录真实阶段并按队列返回模型输出。"""

        responses = iter(values)

        def invoke(prompt):
            """以受控 JSON 替换远端模型，不替换片段校验。"""

            calls.append(prompt)
            return json.dumps(next(responses), ensure_ascii=False)

        return invoke

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

    def test_report_retries_binding_without_replanning_directory(self):
        """审查报告按错误输出在原映射片段重试，不撤下身份、实体或接口目录。"""

        initial = catalog_responses(self.catalog)
        values = [*initial[:-1], self.issue, initial[-1],
            *[{"api_contracts": [contract]} for contract in self.contracts],
            *[{"pages": [page]} for page in self.pages]]
        calls, progress = [], {}
        plan = generate_technical_plan_sections(self.requirement,
            invoke=self._invoke(values, calls), fragments=progress)
        self.assertEqual(plan["api_contracts"], self.contracts)
        self.assertEqual(plan["pages"], self.pages)
        self.assertEqual(progress["completed"]["catalog"], self.catalog)
        self.assertNotIn("catalog_repair", progress["completed"])
        self.assertIn("catalog_bindings", calls[len(initial)].splitlines()[0])
        self.assertEqual(sum("catalog_registry" in p.splitlines()[0] for p in calls), 1)
        self.assertEqual(self.requirement, self.original)

    def test_report_retries_page_without_changing_valid_directory(self):
        """页面审查报告不会要求修改已通过目录，后续合法页面输出可直接完成。"""

        initial = [*catalog_responses(self.catalog),
            *[{"api_contracts": [contract]} for contract in self.contracts],
            *[{"pages": [page]} for page in self.pages]]
        values = [*initial[:-1], self.issue, initial[-1]]
        calls, progress = [], {}
        plan = generate_technical_plan_sections(self.requirement,
            invoke=self._invoke(values, calls), fragments=progress)
        self.assertEqual(plan["pages"], self.pages)
        self.assertEqual(progress["completed"]["catalog"], self.catalog)
        self.assertNotIn("catalog_repair", progress["completed"])
        self.assertTrue(all("page（" in p.splitlines()[0] for p in calls[-2:]))
        self.assertEqual(sum("catalog_registry" in p.splitlines()[0] for p in calls), 1)
        self.assertEqual(self.requirement, self.original)

    def test_repeated_reports_keep_durable_modules_for_explicit_retry(self):
        """模型持续输出错误报告时仍按三次预算失败，显式重试只生成未完成页面。"""

        with tempfile.TemporaryDirectory() as workspace:
            requirement = {**self.requirement, "technical_plan_generation_workspace": workspace}
            initial = [*catalog_responses(self.catalog),
                *[{"api_contracts": [contract]} for contract in self.contracts],
                *[{"pages": [page]} for page in self.pages[:-1]]]
            calls = []
            with self.assertRaises(PlanningFragmentFailed) as caught:
                generate_technical_plan_sections(requirement,
                    invoke=self._invoke([*initial, *[self.issue] * 3], calls))
            self.assertEqual(caught.exception.attempts, 3)
            store = TechnicalPlanFragmentStore(workspace)
            digest = json.loads(store.path.read_text())["inputSha256"]
            retained = store.load(digest)
            self.assertEqual(retained["catalog"], self.catalog)
            self.assertEqual(retained["contract:orders_api"], self.contracts[0])
            self.assertEqual(retained["page:orders"], self.pages[0])
            self.assertNotIn("catalog_repair", retained)
            resumed = []
            result = generate_technical_plan_sections(requirement,
                invoke=self._invoke([{"pages": [self.pages[-1]]}], resumed))
            self.assertEqual(result["pages"], self.pages)
            self.assertEqual(len(resumed), 1)
            self.assertIn("page（", resumed[0].splitlines()[0])

    def test_binding_report_failure_does_not_expand_outer_budget(self):
        """映射报告耗尽原片段预算后停止，不重建接口目录或叠加外层重试。"""

        initial = catalog_responses(self.catalog)
        outputs = [json.dumps(value) for value in [*initial[:-1], *[self.issue] * 3]]
        progress = {}
        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=outputs) as model, patch(
                "app.graph.nodes.planning._planning_token_callback"):
            plan, errors, _ = _generate_valid_technical_plan({}, self.requirement, None,
                generation_fragments=progress)
        self.assertIsNone(plan)
        self.assertIn("已尝试 3 次", errors[0])
        self.assertEqual(model.call_count, len(outputs))
        self.assertNotIn("catalog_repair", progress["completed"])
        self.assertTrue(any(key.startswith("catalog-part:") for key in progress["completed"]))

    def test_confirmed_product_prose_does_not_add_a_separate_semantic_gate(self):
        """已确认动作的文字不触发额外目录审查，也不引入跳过业务绑定的例外。"""

        action = self.requirement["confirmed_product_plan"]["pages"][0]["actions"][0]
        action["name"] = "查看详情"
        action["description"] = "仅展示按钮，本期不实现点击逻辑。"
        action["behavior"]["expectedResult"] = "本期不执行点击后的业务逻辑。"
        before = deepcopy(self.requirement)
        calls = []
        values = [json.loads(value) for value in fragment_outputs(self.catalog, self.contracts, self.pages)]
        result = generate_technical_plan_sections(self.requirement, invoke=self._invoke(values, calls))
        self.assertEqual(result["pages"], self.pages)
        self.assertEqual(self.requirement, before)
        self.assertFalse(any("catalog_issue" in prompt for prompt in calls))

    def test_page_still_rejects_missing_binding_and_unknown_endpoint(self):
        """撤除模型审查后，原有动作覆盖和未知接口校验仍拒绝不完整页面。"""

        product_page = self.requirement["confirmed_product_plan"]["pages"][0]
        for kind in ("missing", "unknown"):
            with self.subTest(kind=kind):
                page = deepcopy(self.pages[0])
                if kind == "missing":
                    page["references"]["action_implementations"] = []
                else:
                    page["references"]["action_implementations"][0]["endpointId"] = "missing.read"
                with self.assertRaises(ValueError):
                    validate_page({"pages": [page]}, product_page, self.contracts)
