"""验证全局目录所有权、局部恢复、全局冲突及原子完整发布。"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

from app.agents.main.technical_plan_catalog_repair import repaired_catalog_fragments
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.agents.main.technical_plan_catalog_parts_validation import validate_registry
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from tests.technical_plan_catalog_fixture import catalog_response, catalog_responses
from tests.test_technical_plan_modular_generation import modular_fixture, fragment_outputs


class CatalogModel:
    """根据目录阶段选择受控对象，后续 Contract 与页面仍走真实生成编排。"""

    def __init__(self, catalog, contracts, pages):
        """保存预期业务事实和逐次请求，便于注入单个目录片段错误。"""

        self.catalog = catalog
        self.calls = []
        self.remaining = iter(fragment_outputs(catalog, contracts, pages)[len(catalog_responses(catalog)):])

    def __call__(self, prompt):
        """目录响应按实际阶段裁剪，其他阶段按既有完整片段响应。"""

        self.calls.append(prompt)
        if "catalog_" in prompt.splitlines()[0]:
            return json.dumps(catalog_response(prompt, self.catalog), ensure_ascii=False)
        return next(self.remaining)


class TechnicalPlanCatalogPartsTests(TestCase):
    """不替换目录校验或存储，检查跨片段约束及恢复后真正发起的模型请求。"""

    def setUp(self):
        """创建两个共享实体、两个业务 Contract、三个页面及组合业务步骤。"""

        self.requirement, self.catalog, self.contracts, self.pages = modular_fixture()
        self.model = CatalogModel(self.catalog, self.contracts, self.pages)

    def test_catalog_is_split_by_ownership_and_publishes_only_complete_result(self):
        """全局只输出身份，实体、接口及动作映射各归一个局部请求。"""

        progress = {}
        result = generate_technical_plan_sections(self.requirement, invoke=self.model, fragments=progress)
        self.assertEqual(result["entities"], self.catalog["entities"])
        self.assertEqual(result["api_contracts"], self.contracts)
        stages = [prompt.splitlines()[0] for prompt in self.model.calls]
        for stage, count in (("catalog_registry", 1), ("catalog_entity", 2), ("catalog_endpoints", 2), ("catalog_bindings", 3)):
            self.assertEqual(sum(stage in item for item in stages), count)
        contexts = [json.loads(prompt.splitlines()[-1]) for prompt in self.model.calls[:8]]
        self.assertNotIn("fields", catalog_response(self.model.calls[0], self.catalog)["entities"][0])
        self.assertEqual({page["pageId"] for page in contexts[1]["pages"]}, {"orders", "order_detail"})
        self.assertNotIn("SECRET-DO-NOT-PROMPT", "".join(self.model.calls))
        self.assertFalse(any(key.startswith("catalog-part:") for key in progress["completed"]))
        self.assertEqual(progress["completed"]["catalog"], self.catalog)

    def test_registry_rejects_unknown_ownership_and_old_bulk_response(self):
        """旧的整目录包络及未知实体归属不能作为当前全局身份响应。"""

        registry = catalog_responses(self.catalog)[0]
        for value in (self.catalog, {"entities": [], "contracts": []},
                {**registry, "contracts": [{**registry["contracts"][0], "entity_ids": ["unknown"]}]}):
            with self.subTest(value=list(value)), self.assertRaises(ValueError):
                validate_registry(value, self.requirement["confirmed_product_plan"])

    def test_cross_contract_route_conflict_retries_only_current_directory(self):
        """第二业务目录不能用不同参数名重复占用第一个目录的路由。"""

        self.catalog["contracts"][0]["endpoints"][0]["path"] = "/api/shared/{first}"
        self.catalog["contracts"][1]["endpoints"][0]["path"] = "/api/shared/{second}"
        for spec, contract in zip(self.catalog["contracts"], self.contracts):
            # 两个能力可共享路径范围，但仍不得重复占用同一规范化 HTTP 路由。
            spec["base_path"] = contract["base_path"] = "/api/shared"
        progress = {}
        with self.assertRaisesRegex(ValueError, "method/path"):
            generate_technical_plan_sections(self.requirement, invoke=self.model, fragments=progress)
        self.assertNotIn("catalog", progress["completed"])
        self.contracts[1]["endpoints"][0]["path"] = "/api/shared/customers"
        self.catalog["contracts"][0]["endpoints"][0]["path"] = self.contracts[0]["endpoints"][0]["path"]
        self.catalog["contracts"][1]["endpoints"][0]["path"] = self.contracts[1]["endpoints"][0]["path"]
        # 已完成第一目录的内容不会悄悄被外部夹具修改；恢复时保持其冻结路由。
        self.contracts[0]["endpoints"][0]["path"] = "/api/shared/{first}"
        self.contracts[0]["endpoints"][0]["parameters"] = [{"name": "first", "in": "path", "required": True, "schema": {"type": "string"}}]
        self.catalog["contracts"][0]["endpoints"][0]["path"] = "/api/shared/{first}"
        resumed = CatalogModel(self.catalog, self.contracts, self.pages)
        result = generate_technical_plan_sections(self.requirement, invoke=resumed, fragments=progress)
        self.assertEqual(result["api_contracts"], self.contracts)
        self.assertEqual(sum("catalog_" in prompt.splitlines()[0] for prompt in resumed.calls), 4)
        self.assertTrue("catalog_endpoints" in resumed.calls[0].splitlines()[0])

    def test_invalid_entity_fields_preserve_other_completed_entity(self):
        """第二实体重复字段时只撤下该响应，恢复不会重生成全局身份和第一实体。"""

        progress = {}
        self.catalog["entities"][1]["fields"].append(deepcopy(self.catalog["entities"][1]["fields"][0]))
        with self.assertRaisesRegex(ValueError, "重复"):
            generate_technical_plan_sections(self.requirement, invoke=self.model, fragments=progress)
        self.catalog["entities"][1]["fields"].pop()
        resumed = CatalogModel(self.catalog, self.contracts, self.pages)
        generate_technical_plan_sections(self.requirement, invoke=resumed, fragments=progress)
        self.assertTrue("catalog_entity" in resumed.calls[0].splitlines()[0])
        self.assertEqual(json.loads(resumed.calls[0].splitlines()[-1])["entity"]["id"], "Customer")
        self.assertEqual(sum("catalog_entity" in prompt.splitlines()[0] for prompt in resumed.calls), 1)

    def test_bad_binding_retries_same_page_without_replanning_apis(self):
        """动作映射漏组合步骤时，不重复全局身份、实体字段或接口目录。"""

        progress = {}
        missing = self.catalog["action_bindings"].pop()
        with self.assertRaisesRegex(ValueError, "目录遗漏业务"):
            generate_technical_plan_sections(self.requirement, invoke=self.model, fragments=progress)
        self.catalog["action_bindings"].append(missing)
        resumed = CatalogModel(self.catalog, self.contracts, self.pages)
        result = generate_technical_plan_sections(self.requirement, invoke=resumed, fragments=progress)
        self.assertEqual(result["pages"], self.pages)
        self.assertEqual(sum("catalog_" in prompt.splitlines()[0] for prompt in resumed.calls), 1)
        self.assertIn("catalog_bindings", resumed.calls[0].splitlines()[0])

    def test_process_exit_resumes_entity_parts_before_catalog_publication(self):
        """身份与首实体持久化后直接退出，父进程从第二实体继续且此前不发布完整目录。"""

        with tempfile.TemporaryDirectory() as workspace:
            requirement = {**self.requirement, "technical_plan_generation_workspace": workspace}
            path = Path(workspace) / "inputs.json"
            path.write_text(json.dumps([requirement, self.catalog]))
            script = '''
import json,os,sys
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from tests.technical_plan_catalog_fixture import catalog_response
req,catalog=json.load(open(sys.argv[1]))
count=0
def invoke(prompt):
    """两个目录片段同步成功后，模拟测试进程立即结束。"""
    global count
    count+=1
    if count>2:os._exit(73)
    return json.dumps(catalog_response(prompt,catalog))
generate_technical_plan_sections(req,invoke=invoke)
'''
            process = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, timeout=30)
            self.assertEqual(process.returncode, 73, process.stderr.decode())
            store = TechnicalPlanFragmentStore(workspace)
            directory = json.loads(store.path.read_text())
            self.assertNotIn("catalog", directory["completed"])
            self.assertEqual(len(directory["completed"]), 2)
            resumed = CatalogModel(self.catalog, self.contracts, self.pages)
            result = generate_technical_plan_sections(requirement, invoke=resumed)
            self.assertEqual(result["api_contracts"], self.contracts)
            self.assertEqual(json.loads(resumed.calls[0].splitlines()[-1])["entity"]["id"], "Customer")

    def test_failed_complete_publication_preserves_durable_children(self):
        """完整目录原子发布失败时不泄露半目录，重试无需重新请求任何目录片段。"""

        with tempfile.TemporaryDirectory() as workspace:
            requirement = {**self.requirement, "technical_plan_generation_workspace": workspace}
            with patch.object(TechnicalPlanFragmentStore, "replace", side_effect=OSError("目录发布失败")):
                with self.assertRaisesRegex(OSError, "目录发布失败"):
                    generate_technical_plan_sections(requirement, invoke=self.model)
            directory = json.loads(TechnicalPlanFragmentStore(workspace).path.read_text())
            self.assertNotIn("catalog", directory["completed"])
            self.assertEqual(len(directory["completed"]), 8)
            resumed = CatalogModel(self.catalog, self.contracts, self.pages)
            generate_technical_plan_sections(requirement, invoke=resumed)
            self.assertFalse(any("catalog_" in prompt.splitlines()[0] for prompt in resumed.calls))

    def test_nested_repair_invalidates_against_original_published_baseline(self):
        """第二次缺口不能把前次中间目录当成已发布基线，漏掉第一业务能力的旧缓存。"""

        middle = deepcopy(self.catalog)
        middle["contracts"][0]["endpoints"][0]["summary"] += "第一轮修复。"
        final = deepcopy(middle)
        final["contracts"][1]["endpoints"][0]["summary"] += "第二轮修复。"
        cache = {"catalog_repair": {"catalog": middle, "baseline": self.catalog,
            "issue": {"pageId": "customers", "kind": "initial_load", "actionId": None, "stepId": None,
                "reason": "需要补全客户读取能力。"}}, **{f"contract:{contract['id']}": contract for contract in self.contracts}}
        published = repaired_catalog_fragments(cache, final)
        self.assertNotIn("contract:orders_api", published)
        self.assertNotIn("contract:customers_api", published)
