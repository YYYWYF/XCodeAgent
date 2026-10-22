"""外部映射 AI 辅助的严格边界、提示隔离和无落盘替换回归。"""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.endpoint_design_assist_prompts import build_assist_messages
from app.domain.endpoint_design_assist_steps import ReturnStepResult
from app.domain.endpoint_design_external_assist import ExternalGenerateRequest, ExternalGeneratedMapping, ExternalReplaceMappingRequest, ExternalRequestStepResult
from app.services.endpoint_design_external_assist import load_external_assist_context, replace_external_mapping
from app.services.endpoint_design_external_assist_validation import expand_external_step, complete_external_mapping, validate_external_mapping


def fixture(workspace="E:/external-test"):
    """提供真实形状的双向契约与仅用于隔离断言的连接值。"""
    selection = dict(sourceType="external_api", sourceId="vendor", directoryId="catalog", operationId="products")
    fields = [dict(side="request", location="query", path="keyword", type="string", required=False, description="关键词"),
              dict(side="response", location="response_body", path="name", type="string", required=True, description="商品名")]
    preparation = dict(technicalPlanHash="a" * 64, artifactRevision=None,
                       payload=dict(endpoint=dict(name="商品查询", method="GET", path="/products"), endpointFields=fields))
    metadata = dict(operation=dict(name="供应商商品", method="GET", path="/search", description="搜索商品", requestSample="SAMPLE_SECRET"),
                    fields=[dict(section="query", path="keyword", type="string", required=True, description="关键词"),
                            dict(section="response_body", path="name", type="string", required=True, description="名称")],
                    connection=dict(headers=[dict(name="Authorization", value="CONNECTION_SECRET")]))
    request = dict(workspaceRoot=workspace, apiContractId="products", endpointId="list", selection=selection, technicalPlanHash="a" * 64,
                   draft=dict(apiContractId="products", endpointId="list", sourceBinding=selection, fieldMappings=[], implementationDescription="OLD_DRAFT_SECRET"))
    return request, preparation, metadata


def patches(preparation, metadata):
    """在结构读取边界模拟已保存数据，不联网、不读取真实凭据。"""
    return patch("app.services.endpoint_design_external_assist.prepare_endpoint_design", return_value=preparation), patch(
        "app.services.endpoint_design_external_assist.load_external_operation", return_value=metadata)


def context_fixture():
    """通过真实上下文装配取得测试用权威字段。"""
    request, preparation, metadata = fixture()
    p1, p2 = patches(preparation, metadata)
    with p1, p2:
        return load_external_assist_context(ExternalGenerateRequest(**request, instruction="查询商品"))


def candidates_fixture(context):
    """使用真实简洁模型和转换器生成入参与返回候选。"""
    request = expand_external_step(ExternalRequestStepResult(requests=[dict(field="external:query:keyword", inputs=["request:query:keyword"])]), context)
    response = expand_external_step(ReturnStepResult(returns=[dict(field="response:response_body:name", inputs=["external:response_body:name"])]), context)
    return request.candidates + response.candidates


class ExternalAssistTests(unittest.TestCase):
    """局部配置可不完整，但不能突破来源、字段、方向与正式版本边界。"""

    def test_direct_mapping_and_partial_targets(self):
        """两侧映射补齐权威快照；空候选显式留空，绝不沿用旧值。"""
        context = context_fixture()
        blank = complete_external_mapping([], context)
        self.assertNotIn("right", blank["externalApiBindings"][0])
        self.assertEqual(blank["implementationDescription"], "")
        result = complete_external_mapping(candidates_fixture(context), context)
        self.assertEqual(result["externalApiBindings"][0]["right"]["kind"], "endpoint")
        self.assertEqual(result["fieldMappings"][1]["sourceFields"][0]["operationId"], "products")
        self.assertEqual(validate_external_mapping(ExternalGeneratedMapping(**result), context), result)

    def test_invalid_targets_dependencies_duplicates_and_types(self):
        """拒绝反向依赖、伪造字段、重复目标和直接类型不兼容。"""
        context = context_fixture()
        invalid = [dict(field="external:query:keyword", inputs=["external:response_body:name"]),
                   dict(field="external:response_body:name", inputs=["request:query:keyword"]),
                   dict(field="external:query:missing", inputs=["request:query:keyword"]),
                   dict(field="external:query:keyword", value=12, basis="用户明确指定")]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                expand_external_step(ExternalRequestStepResult(requests=[item]), context)
        item = dict(field="external:query:keyword", inputs=["request:query:keyword"])
        with self.assertRaises(ValueError):
            expand_external_step(ExternalRequestStepResult(requests=[item, item]), context)
        with self.assertRaises(ValueError):
            expand_external_step(ReturnStepResult(returns=[dict(field="response:response_body:name", inputs=["external:query:keyword"])]), context)

    def test_fixed_and_business_require_basis(self):
        """固定值和业务处理需要明确依据，没有依据则提问且保持目标未配置。"""
        context = context_fixture()
        for value in (dict(value="test"), dict(inputs=["request:query:keyword"], rule="去除首尾空格")):
            output = expand_external_step(ExternalRequestStepResult(requests=[dict(field="external:query:keyword", **value)]), context)
            self.assertFalse(output.candidates)
            self.assertTrue(output.questions)
            explicit = expand_external_step(ExternalRequestStepResult(requests=[dict(field="external:query:keyword", basis="需求明确要求", **value)]), context)
            self.assertEqual(len(explicit.candidates), 1)

    def test_replacement_rejects_forged_snapshots_hidden_policy_and_omissions(self):
        """整体替换拒绝伪造来源、隐藏策略、未配置快照和漏项。"""
        context = context_fixture()
        result = complete_external_mapping(candidates_fixture(context), context)
        mutations = []
        value = copy.deepcopy(result); value["externalApiBindings"][0]["externalField"]["operationId"] = "other"; mutations.append(value)
        value = copy.deepcopy(result); value["fieldMappings"][1]["sourceFields"][0]["description"] = "伪造"; mutations.append(value)
        value = copy.deepcopy(result); value["fieldMappings"].pop(); mutations.append(value)
        value = copy.deepcopy(result); value["externalApiBindings"].clear(); mutations.append(value)
        value = copy.deepcopy(result); value["fieldMappings"][1]["missingBehavior"] = "default"; value["fieldMappings"][1]["defaultValue"] = "fallback"; mutations.append(value)
        value = complete_external_mapping([], context); value["fieldMappings"][1]["endpointField"]["type"] = "integer"; mutations.append(value)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_external_mapping(ExternalGeneratedMapping(**value), context)

    def test_context_and_prompts_exclude_connection_samples_and_old_draft(self):
        """模型输入白名单按步骤投影，说明阶段不重复字段表。"""
        context = context_fixture()
        model_context = {key: context[key] for key in ("endpoint", "endpointFields", "selection", "externalOperation", "externalFields")}
        model_context.update(instruction="查询商品\n按关键词匹配", clarification="名称直接返回", generatedSummary="入参已配置")
        for step in ("request", "return", "description"):
            messages = build_assist_messages(model_context, step)
            text = messages[1].content
            for secret in ("CONNECTION_SECRET", "SAMPLE_SECRET", "OLD_DRAFT_SECRET"):
                self.assertNotIn(secret, str(messages))
            self.assertIn("【补充说明】\n名称直接返回", text)
            self.assertNotIn("数据库操作", text)
            if step == "description":
                self.assertNotIn("【可用输入】", text)
            else:
                self.assertLess(text.index("【处理目标】"), text.index("【可用输入】"))

    def test_revision_source_and_metadata_conflicts_do_not_write(self):
        """替换不写文件；契约、外部结构或草稿变化后拒绝旧结果。"""
        with tempfile.TemporaryDirectory() as root:
            request, preparation, metadata = fixture(root)
            p1, p2 = patches(preparation, metadata)
            with p1, p2:
                context = load_external_assist_context(ExternalGenerateRequest(**request, instruction="查询商品"))
                mapping = complete_external_mapping(candidates_fixture(context), context)
                replacement = ExternalReplaceMappingRequest(**request, baseline=context["baseline"], mapping=mapping)
                applied = replace_external_mapping(replacement)
                self.assertEqual(applied["areas"], ["request", "return", "description"])
                self.assertFalse(list(Path(root).iterdir()))
                metadata["fields"][0]["description"] = "已修改"
                with self.assertRaisesRegex(ValueError, "结构已变化"):
                    replace_external_mapping(replacement)
                metadata["fields"][0]["description"] = "关键词"
                replacement.draft["implementationDescription"] = "用户后续编辑"
                with self.assertRaisesRegex(ValueError, "草稿"):
                    replace_external_mapping(replacement)
                preparation["technicalPlanHash"] = "b" * 64
                with self.assertRaisesRegex(ValueError, "契约"):
                    replace_external_mapping(replacement)

    def test_strict_request_boundaries(self):
        """空需求、未知参数、来源切换和数据库结果不能混入外部动作。"""
        request, preparation, metadata = fixture()
        for extra in (dict(instruction=" "), dict(instruction="查询", unexpected=True)):
            with self.assertRaises(ValueError):
                ExternalGenerateRequest(**request, **extra)
        p1, p2 = patches(preparation, metadata)
        with p1, p2:
            request["draft"]["databaseOperation"] = "read"
            with self.assertRaisesRegex(ValueError, "混合"):
                load_external_assist_context(ExternalGenerateRequest(**request, instruction="查询"))
        with self.assertRaises(ValueError):
            ExternalGeneratedMapping(fieldMappings=[], databaseOperation="read")
