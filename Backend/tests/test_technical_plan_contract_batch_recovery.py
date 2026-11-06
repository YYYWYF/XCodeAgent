"""覆盖跨 Schema 引用、局部错误和真实子进程退出后的批次恢复。"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase

from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.agents.main.technical_plan_fragment_output import PlanningOutputTruncated
from app.agents.main import planner
from app.graph.nodes.planning import _generate_valid_technical_plan
from unittest.mock import patch
from tests.test_technical_plan_contract_batches import BatchModel, batch_fixture


class TechnicalPlanContractBatchRecoveryTests(TestCase):
    """运行完整内部编排，确认缓存发布和引用闭合不依赖进程内状态。"""

    def test_cyclic_shared_schemas_close_without_duplicate_definitions(self):
        """跨批次共享与循环引用保持同一 Schema 身份，提示词闭包遍历能够终止。"""

        requirement, catalog, contract, page, plan = batch_fixture()
        contract["schemas"]["Output"]["properties"]["code_0"] = {"$ref": "Detail", "description": "订单详情。"}
        contract["schemas"]["Detail"] = {"type": "object", "properties": {
            "parent": {"$ref": "Output", "description": "所属订单。"}}, "required": []}
        plan["schemas"][0]["references"] = ["Detail"]
        plan["schemas"].append({"name": "Detail", "base": {"type": "object"}, "fields": ["parent"],
            "required": [], "references": ["Output"]})
        model = BatchModel(catalog, contract, page, plan)
        result = generate_technical_plan_sections(requirement, invoke=model)
        self.assertEqual(result["api_contracts"], [contract])
        self.assertEqual(len(model.field_groups), 2)
        prompts = [prompt for prompt in model.prompts if "contract_endpoints" in prompt.splitlines()[0]]
        self.assertTrue(all(set(json.loads(prompt.splitlines()[-1])["schemas"]) == {"Output", "Detail"} for prompt in prompts))

    def test_bad_endpoint_ref_retries_same_task_and_reuses_schemas(self):
        """接口不能更改冻结引用，失败接口不缓存，重试沿同一批次继续。"""

        requirement, catalog, contract, page, plan = batch_fixture()
        model, progress = BatchModel(catalog, contract, page, plan), {}
        def change_ref(prompt):
            """只在第一个接口批次破坏冻结引用，其他阶段使用有效响应。"""

            output = model(prompt)
            if "contract_endpoints" in prompt.splitlines()[0]:
                value = json.loads(output)
                value["endpoints"][0]["response_schema_ref"] = "NewOutput"
                return json.dumps(value)
            return output

        with self.assertRaisesRegex(ValueError, "冻结 Schema 引用"):
            generate_technical_plan_sections(requirement, invoke=change_ref, fragments=progress)
        resumed = BatchModel(catalog, contract, page, plan)
        result = generate_technical_plan_sections(requirement, invoke=resumed, fragments=progress)
        self.assertEqual(result["api_contracts"], [contract])
        self.assertEqual(resumed.field_groups, [])
        self.assertEqual(resumed.endpoint_groups[0], model.endpoint_groups[0])

    def test_model_cannot_expand_other_endpoint_batches(self):
        """复现真实模型返回整份接口列表时，精确提示当前批次且保留已完成 Schema。"""

        requirement, catalog, contract, page, plan = batch_fixture()
        model, progress = BatchModel(catalog, contract, page, plan), {}
        def entire_contract(prompt):
            """接口阶段错误地返回全部十三个接口。"""

            if "contract_endpoints" in prompt.splitlines()[0]:
                return json.dumps({"endpoints": contract["endpoints"]})
            return model(prompt)

        with self.assertRaisesRegex(ValueError, "恰好返回 6 个接口"):
            generate_technical_plan_sections(requirement, invoke=entire_contract, fragments=progress)
        resumed = BatchModel(catalog, contract, page, plan)
        sections = generate_technical_plan_sections(requirement, invoke=resumed, fragments=progress)
        self.assertEqual(sections["api_contracts"], [contract])
        self.assertEqual(resumed.field_groups, [])

    def test_field_truncation_only_splits_failed_field_group(self):
        """已生成的二十四个字段保留，只有截断字段组被二分。"""

        requirement, catalog, contract, page, plan = batch_fixture(fields=70)
        model, progress = BatchModel(catalog, contract, page, plan), {}
        def truncate(prompt):
            """首字段组成功后，第二组模拟明确输出截断。"""

            if "contract_schema_fields" in prompt.splitlines()[0] and len(model.field_groups) == 1:
                raise PlanningOutputTruncated("length")
            return model(prompt)

        with self.assertRaises(PlanningOutputTruncated):
            generate_technical_plan_sections(requirement, invoke=truncate, fragments=progress)
        resumed = BatchModel(catalog, contract, page, plan)
        result = generate_technical_plan_sections(requirement, invoke=resumed, fragments=progress)
        self.assertEqual(result["api_contracts"], [contract])
        self.assertEqual(sum(map(len, resumed.field_groups)), 70 - len(model.field_groups[0]))
        self.assertLess(len(resumed.field_groups[0]), len(model.field_groups[0]))

    def test_process_exit_preserves_schema_and_first_endpoint_batch(self):
        """子进程直接退出后从磁盘恢复，无内存缓存也不会重新调用已完成批次。"""

        requirement, catalog, contract, page, plan = batch_fixture()
        with tempfile.TemporaryDirectory() as workspace:
            requirement["technical_plan_generation_workspace"] = workspace
            inputs = Path(workspace) / "inputs.json"
            inputs.write_text(json.dumps([requirement, catalog, contract, page, plan]))
            script = '''
import json,os,sys
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from tests.test_technical_plan_contract_batches import BatchModel
requirement,catalog,contract,page,plan=json.load(open(sys.argv[1]))
model=BatchModel(catalog,contract,page,plan)
def invoke(prompt):
    """一个接口批次持久化后，在下一请求前直接退出测试进程。"""
    if "contract_endpoints" in prompt.splitlines()[0] and model.endpoint_groups:
        os._exit(73)
    return model(prompt)
generate_technical_plan_sections(requirement,invoke=invoke)
'''
            result = subprocess.run([sys.executable, "-c", script, str(inputs)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assertFalse((Path(workspace) / ".devagentstudio/plans/technical-plan.json").exists())
            resumed = BatchModel(catalog, contract, page, plan)
            sections = generate_technical_plan_sections(requirement, invoke=resumed)
            self.assertEqual(sections["api_contracts"], [contract])
            self.assertEqual(resumed.field_groups, [])
            self.assertEqual(list(map(len, resumed.endpoint_groups)), [6, 1])

    def test_duplicate_field_names_fail_before_cache_and_recover(self):
        """重复 JSON 键不能被静默覆盖成有效字段，失败后同一字段任务能正常重生成。"""

        requirement, catalog, contract, page, plan = batch_fixture()
        model, progress = BatchModel(catalog, contract, page, plan), {}
        def duplicate(prompt):
            """字段阶段模拟模型重复输出相同属性键。"""

            if "contract_schema_fields" in prompt.splitlines()[0]:
                return '{"properties":{"code_0":{"type":"string"},"code_0":{"type":"number"}}}'
            return model(prompt)

        with self.assertRaisesRegex(ValueError, "重复键"):
            generate_technical_plan_sections(requirement, invoke=duplicate, fragments=progress)
        result = generate_technical_plan_sections(requirement, invoke=model, fragments=progress)
        self.assertEqual(result["api_contracts"], [contract])

    def test_unknown_entity_ref_feedback_retries_only_fields_with_authoritative_choices(self):
        """复现复杂样本：时间字段的猜测实体引用被拒绝，下一轮明确给出合法引用后修复。"""

        requirement, catalog, contract, page, plan = batch_fixture()
        contract["schemas"]["Output"]["properties"]["created_at"] = {"type": "string", "format": "date-time", "description": "响应生成时间。"}
        plan["schemas"][0]["fields"].append("created_at")
        model = BatchModel(catalog, contract, page, plan)
        field_calls = []
        def invoke(prompt, **kwargs):
            """第一次输出不存在的实体引用，第二次依照明确白名单输出无实体来源的传输元信息。"""

            output = model(prompt)
            if "contract_schema_fields" in prompt.splitlines()[0]:
                context = json.loads(prompt.splitlines()[-1])
                self.assertIn("Order.code", context["allowed_entity_field_refs"])
                self.assertIn("Order.id", context["allowed_entity_field_refs"])
                self.assertNotIn("Order.created_at", context["allowed_entity_field_refs"])
                field_calls.append(prompt)
                if len(field_calls) == 1:
                    value = json.loads(output)
                    value["properties"]["created_at"]["entity_field_ref"] = "Order.created_at"
                    return json.dumps(value)
                self.assertIn("Order.created_at", prompt)
            return output

        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=invoke), patch(
            "app.graph.nodes.planning._project_plan_validation_errors", return_value=[]), patch(
            "app.graph.nodes.planning._planning_token_callback"):
            result, errors, _ = _generate_valid_technical_plan({}, requirement, None)
        self.assertIsNotNone(result, errors)
        self.assertEqual(result["api_contracts"], [contract])
        self.assertEqual(len(field_calls), 2)
        self.assertEqual(sum("contract_schema_plan" in prompt.splitlines()[0] for prompt in model.prompts), 1)

    def test_real_complex_response_shape_errors_retry_only_failed_part(self):
        """复现真实复杂样本的字段根包络与错误码类型问题，错误响应不缓存且已有片段不重做。"""

        for defect in ("properties_root", "error_code_type"):
            with self.subTest(defect=defect):
                requirement, catalog, contract, page, plan = batch_fixture()
                model = BatchModel(catalog, contract, page, plan)
                attempts = []
                def invoke(prompt, **kwargs):
                    """只注入一次目标阶段错误，后续根据当前明确格式约束返回标准响应。"""

                    output = model(prompt)
                    target = "contract_schema_fields" if defect == "properties_root" else "contract_endpoints"
                    if target in prompt.splitlines()[0]:
                        if defect == "properties_root":
                            self.assertIn("必须保留最外层 properties 键", prompt)
                        else:
                            self.assertIn("error_codes 为字符串数组", prompt)
                        attempts.append(prompt)
                        if len(attempts) == 1:
                            value = json.loads(output)
                            if defect == "properties_root":
                                return json.dumps(value["properties"])
                            value["endpoints"][0]["error_codes"] = [{"code": "INVALID_STATE", "message": "当前状态不允许操作。"}]
                            return json.dumps(value)
                    return output

                with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=invoke), patch(
                    "app.graph.nodes.planning._project_plan_validation_errors", return_value=[]), patch(
                    "app.graph.nodes.planning._planning_token_callback"):
                    result, errors, _ = _generate_valid_technical_plan({}, requirement, None)
                self.assertIsNotNone(result, errors)
                self.assertEqual(result["api_contracts"], [contract])
                self.assertEqual(sum("contract_schema_plan" in prompt.splitlines()[0] for prompt in model.prompts), 1)
                self.assertEqual(len(attempts), 2 if defect == "properties_root" else 4)
