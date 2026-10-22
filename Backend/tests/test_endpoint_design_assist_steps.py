"""简洁提示、分步结果转换和现有映射校验的同步回归，不调用模型或数据库。"""

import unittest
from unittest.mock import patch

from app.agents.endpoint_design_assist_prompts import build_assist_messages
from app.domain.endpoint_design_assist_steps import ASSIST_STEP_MODELS, StepValue
from app.services.endpoint_design_assist_steps import expand_step_result, generated_summary, generation_steps, step_model_result, mapping_reviews
from app.services.endpoint_design_assist_validation import complete_mapping, validate_generated_mapping
from app.domain.endpoint_design_assist import GeneratedMapping, GenerateMappingRequest
from tests.test_endpoint_design_assist import fixture


def context_fixture(operation: str = "read") -> dict:
    """用正式请求快照与公开列元数据构造纯内存上下文。"""
    request, preparation, metadata = fixture("E:/test")
    request["draft"]["databaseOperation"] = operation
    return {"endpoint": preparation["payload"]["endpoint"], "endpointFields": preparation["payload"]["endpointFields"],
            "columns": metadata["columns"], "selection": request["selection"], "draft": request["draft"],
            "databaseOperation": operation, "instruction": "根据商品 ID 查询商品售价"}


def candidates(step: str, raw: dict, context: dict):
    """测试当前步骤的严格解析与权威字段回填。"""
    return expand_step_result(ASSIST_STEP_MODELS[step].model_validate(raw), context)


class EndpointDesignAssistStepsTests(unittest.TestCase):
    """验证简洁模型输出仍能准确保留平台映射能力和边界。"""

    def test_business_values_need_evidence_without_breaking_query_groups(self):
        """无依据的处理留空提问；不能删掉 OR 中一项而改变原始查询语义。"""
        context = context_fixture()
        query = ASSIST_STEP_MODELS["query"].model_validate({"query": {"join": "or", "items": [
            {"column": "id", "op": "eq", "inputs": ["request:path:id"]}, {"column": "price", "op": "eq", "value": 5}]}})
        result = step_model_result(query, context)
        self.assertEqual(result.candidates, [])
        self.assertTrue(result.questions)
        query.query.items[1].basis = "用户明确要求价格为 5"
        supported = step_model_result(query, context)
        self.assertEqual(supported.candidates[0].value["join"], "or")
        self.assertEqual(len(supported.candidates[0].value["items"]), 2)
        raw = {"returns": [{"field": "response:response_body:price", "inputs": ["column:price"], "rule": "乘以 100"}]}
        unsupported = step_model_result(ASSIST_STEP_MODELS["return"].model_validate(raw), context)
        self.assertEqual(unsupported.candidates, [])
        self.assertIn("依据", unsupported.questions[0])
        raw["returns"][0]["basis"] = "用户要求金额转为分"
        supported = step_model_result(ASSIST_STEP_MODELS["return"].model_validate(raw), context)
        review = mapping_reviews(supported.candidates)[0]
        self.assertEqual(review["reason"], "用户要求金额转为分")
        self.assertTrue(review["needsReview"])
        mapping = complete_mapping(supported.candidates, context)
        self.assertNotIn("reviews", mapping)
        self.assertNotIn("basis", str(mapping))
        raw["returns"][0].update(field="response:response_body:missing", basis="")
        with self.assertRaises(ValueError):
            step_model_result(ASSIST_STEP_MODELS["return"].model_validate(raw), context)

    def test_column_constraints_and_prior_steps_are_visible_without_old_draft(self):
        """后序提示只附本轮已完成区域；主键、自增、默认值未知时不补造。"""
        context = context_fixture("update")
        context["columns"][1].update(nullable=False, primaryKey=True, autoIncrement=True, defaultValue=None)
        mapping = complete_mapping(candidates("query", {"query": {"items": [
            {"column": "id", "op": "eq", "inputs": ["request:path:id"]}]}}, context), context)
        context["generatedSummary"] = generated_summary(mapping, ["待明确写入规则"], completed=["query"])
        human = build_assist_messages(context, "write")[1].content
        for expected in ("id eq", "待明确写入规则", "PRIMARY KEY (`id`)", "AUTO_INCREMENT", "NOT NULL", "默认值：无非空默认值", "主键：未知"):
            self.assertIn(expected, human)
        self.assertNotIn("返回 response:", context["generatedSummary"])
        writes = ASSIST_STEP_MODELS["write"].model_validate({"writes": [{"column": "id", "inputs": ["request:path:id"]}]})
        self.assertEqual(step_model_result(writes, context).candidates, [])

    def test_small_outputs_expand_to_current_mapping_and_round_trip(self):
        """模型不复制快照，平台补齐身份、说明和类型后仍通过完整替换校验。"""
        context = context_fixture()
        raw_query = {"query": {"join": "and", "items": [{"column": "id", "op": "eq", "inputs": ["request:path:id"]}]}}
        raw_return = {"returns": [{"field": "response:response_body:price", "inputs": ["column:price"]}]}
        items = candidates("query", raw_query, context) + candidates("return", raw_return, context)
        items += candidates("description", {"description": "根据商品 ID 查询售价。"}, context)
        mapping = complete_mapping(items, context)
        condition = mapping["databaseQuery"]["items"][0]
        self.assertEqual(condition["sourceId"], "db")
        self.assertEqual(condition["type"], "int")
        self.assertEqual(condition["right"]["endpointField"]["description"], "商品 ID")
        source = mapping["fieldMappings"][0]["sourceFields"][0]
        self.assertEqual(source, {**context["selection"], "column": "price", "type": "decimal", "description": "售价", "usage": "read"})
        self.assertEqual(mapping, validate_generated_mapping(GeneratedMapping(**mapping), context))

    def test_query_group_and_null_conditions_keep_semantics(self):
        """一层括号分组与无右值判断不因简化格式而丢失语义。"""
        context = context_fixture()
        raw = {"query": {"join": "and", "items": [
            {"column": "id", "op": "is_not_null"},
            {"join": "or", "items": [{"column": "price", "op": "between", "value": [1, 10]},
                                      {"column": "price", "op": "eq", "value": 20}]},
        ]}}
        mapping = complete_mapping(candidates("query", raw, context), context)
        query = mapping["databaseQuery"]
        self.assertNotIn("right", query["items"][0])
        self.assertEqual(query["items"][1]["kind"], "group")
        self.assertEqual(query["items"][1]["join"], "or")
        self.assertEqual(query["items"][1]["items"][0]["right"]["value"], [1, 10])

    def test_write_and_query_rules_only_use_request_inputs(self):
        """查询和写入可以使用自然语言规则，但不能读取尚未返回的列。"""
        context = context_fixture("update")
        value = {"column": "id", "inputs": ["request:path:id"], "rule": "使用商品 ID，不改变其数值"}
        items = candidates("query", {"query": {"items": [{**value, "op": "eq"}]}}, context)
        items += candidates("write", {"writes": [value]}, context)
        mapping = complete_mapping(items, context)
        self.assertEqual(mapping["databaseWrites"][0]["right"]["missingBehavior"], "error")
        self.assertEqual(mapping["databaseWrites"][0]["right"]["endpointFields"][0]["path"], "id")
        for step, raw in (("write", {"writes": [{**value, "inputs": ["column:price"]}]}),
                          ("query", {"query": {"items": [{**value, "op": "eq", "inputs": ["column:price"]}]}})):
            with self.subTest(step=step), self.assertRaises(ValueError):
                candidates(step, raw, context)

    def test_return_rules_cover_single_multiple_and_request_dependencies(self):
        """平台按真实列数量选择处理类型，保留规则使用的请求参数。"""
        context = context_fixture()
        for inputs, processing in ((["column:price"], "single_field_description"),
                                    (["column:price", "column:id"], "multi_field_description"),
                                    (["column:price", "request:path:id"], "single_field_description")):
            raw = {"returns": [{"field": "response:response_body:price", "inputs": inputs, "rule": "按所给规则处理售价"}]}
            with self.subTest(inputs=inputs):
                mapping = complete_mapping(candidates("return", raw, context), context)
                field = mapping["fieldMappings"][0]
                self.assertEqual(field["processingType"], processing)
                self.assertEqual(len(field["endpointFields"]), int("request:path:id" in inputs))
                self.assertNotIn("defaultValue", field)

    def test_value_returns_support_fixed_parameter_and_business_rules(self):
        """不依赖表列的返回值仍使用现有统一取值契约。"""
        context = context_fixture()
        for value, kind in (({"value": 5}, "fixed"), ({"inputs": ["request:path:id"]}, "endpoint"),
                            ({"inputs": [], "rule": "返回用户明确约定的结果"}, "business")):
            with self.subTest(value=value):
                mapping = complete_mapping(candidates("return", {"returns": [{"field": "response:response_body:price", **value}]}, context), context)
                self.assertEqual(mapping["fieldMappings"][0]["right"]["kind"], kind)
        fixed = StepValue.model_validate({"value": None})
        empty = StepValue.model_validate({})
        self.assertIn("value", fixed.model_fields_set)
        self.assertNotIn("value", empty.model_fields_set)

    def test_invalid_references_and_mixed_formats_are_rejected(self):
        """拒绝未知字段、旧完整配置、隐藏策略、跨区域输出与歧义取值。"""
        context = context_fixture()
        invalid = [
            {"returns": [{"field": "response:response_body:missing", "inputs": ["column:price"]}]},
            {"returns": [{"field": "response:response_body:price", "inputs": ["column:other"]}]},
            {"returns": [{"field": "response:response_body:price", "inputs": ["builtin:current_user_id"]}]},
            {"returns": [{"field": "response:response_body:price", "inputs": ["response:response_body:price"]}]},
            {"returns": [{"field": "response:response_body:price", "inputs": ["column:price"], "value": 1}]},
            {"returns": [{"field": "response:response_body:price", "inputs": ["column:price"], "defaultValue": 1}]},
            {"returns": [{"field": "response:response_body:price", "inputs": ["column:price", "column:id"]}]},
            {"returns": [{"field": "response:response_body:price", "inputs": ["column:price", "column:price"], "rule": "处理"}]},
            {"returns": [{"field": "response:response_body:price"}]},
            {"returns": [], "writes": []}, {"candidates": []},
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                complete_mapping(candidates("return", raw, context), context)

    def test_existing_type_duplicate_and_crud_checks_still_apply(self):
        """简洁输出仍受既有目标类型、重复目标和 CRUD 校验约束。"""
        context = context_fixture()
        context["columns"].append({"name": "name", "type": "varchar", "description": "名称"})
        item = {"field": "response:response_body:price", "inputs": ["column:price"]}
        for raw in ({"returns": [item, item]}, {"returns": [{**item, "inputs": ["column:name"]}]}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                complete_mapping(candidates("return", raw, context), context)
        with self.assertRaises(ValueError):
            complete_mapping(candidates("write", {"writes": [{"column": "id", "value": 1}]}, context), context)
        context["databaseOperation"] = "create"
        with self.assertRaises(ValueError):
            complete_mapping(candidates("query", {"query": {"items": [{"column": "id", "op": "eq", "value": 1}]}}, context), context)

    def test_unknown_returns_remain_unconfigured_and_summary_is_current(self):
        """未知项清空，说明步骤只接收本轮配置与问题的可读摘要。"""
        context = context_fixture()
        context["draft"]["implementationDescription"] = "OLD_DESCRIPTION"
        context["draft"]["databaseWrites"] = [{"column": "OLD_COLUMN"}]
        mapping = complete_mapping([], context)
        self.assertEqual(mapping["fieldMappings"][0]["mappingType"], "unconfigured")
        summary = generated_summary(mapping, ["请明确售价单位"])
        self.assertIn("未配置", summary)
        self.assertIn("请明确售价单位", summary)
        self.assertNotIn("OLD_", summary)
        context["generatedSummary"] = summary
        human = build_assist_messages(context, "description")[1].content
        self.assertIn(summary, human)
        self.assertNotIn("OLD_", human)

    def test_prompts_are_short_readable_and_scoped_without_json_schemas(self):
        """提示只含短示例，输入使用字段清单，各步骤不混入其他区域配置。"""
        context = context_fixture()
        context["selection"]["sourceId"] = "PRIVATE_SOURCE_ID"
        for step in ("operation", "query", "write", "return", "description"):
            messages = build_assist_messages(context, step)
            system, human = messages[0].content, messages[1].content
            with self.subTest(step=step):
                self.assertLess(len(system), 1800)
                for forbidden in ("$defs", "additionalProperties", "sourceFields", "endpointField", "PRIVATE_SOURCE_ID"):
                    self.assertNotIn(forbidden, system + human)
                self.assertIn("接口：", human)
                if step in {"query", "write"}:
                    self.assertNotIn("response:response_body:price", human)
                if step == "return":
                    self.assertIn("response:response_body:price", human)
                    self.assertIn("column:price", human)

    def test_rule_prompt_only_uses_selected_dependencies(self):
        """规则完善使用独立短提示，不携带查询或返回结构说明。"""
        context = {"target": {"area": "write", "key": "price", "type": "decimal"},
                   "dependencies": [{"kind": "endpoint", "field": context_fixture()["endpointFields"][0]},
                                    {"kind": "builtin", "field": {"key": "current_time"}}], "description": "处理售价"}
        system, human = [message.content for message in build_assist_messages(context, "rule")]
        self.assertIn("request:path:id", human)
        self.assertIn("builtin:current_time", human)
        self.assertNotIn('"query"', system)
        self.assertNotIn("column:price", human)
        with self.assertRaises(ValueError):
            ASSIST_STEP_MODELS["rule"].model_validate({"description": "规则", "dependencies": []})

    def test_step_order_and_query_depth_limits(self):
        """每种 CRUD 自动跳过不适用区域，查询不增加第三层嵌套。"""
        self.assertEqual(generation_steps("create"), ["write", "return", "description"])
        self.assertEqual(generation_steps("read"), ["query", "return", "description"])
        self.assertEqual(generation_steps("update"), ["query", "write", "return", "description"])
        self.assertEqual(generation_steps("delete"), ["query", "return", "description"])
        with self.assertRaises(ValueError):
            generation_steps("unknown")
        raw = {"query": {"items": [{"items": [{"items": [{"column": "id", "op": "is_null"}]}]}]}}
        with self.assertRaises(ValueError):
            ASSIST_STEP_MODELS["query"].model_validate(raw)
        with self.assertRaises(ValueError):
            ASSIST_STEP_MODELS["query"].model_validate({"query": {"items": [{"column": "id", "op": "is_null", "value": None}]}})

    def test_conversion_uses_production_context_without_draft(self):
        """真实模型上下文没有草稿，转换仍校验类型、目标及重复依赖。"""
        context = context_fixture()
        del context["draft"]
        result = step_model_result(ASSIST_STEP_MODELS["query"].model_validate({"query": {"items": [
            {"column": "id", "op": "eq", "inputs": ["request:path:id"]}]}}), context)
        self.assertEqual(result.candidates[0].value["items"][0]["column"], "id")
        for step, raw in (("query", {"query": None}), ("description", {"description": "商品详情"}),
                          ("return", {"returns": [{"field": "response:response_body:price", "inputs": ["column:price"]}]})):
            with self.subTest(step=step):
                step_model_result(ASSIST_STEP_MODELS[step].model_validate(raw), context)
        invalid = {"returns": [{"field": "response:response_body:price", "inputs": ["column:price"]}] * 2}
        with self.assertRaises(ValueError):
            step_model_result(ASSIST_STEP_MODELS["return"].model_validate(invalid), context)

    def test_operation_is_scoped_and_ambiguity_requires_questions(self):
        """操作提示隔离映射策略和旧操作；歧义不可默认成物理删除或继续映射。"""
        context = context_fixture("delete")
        with patch("app.agents.endpoint_design_assist_prompts._POLICY", "MAPPING_ONLY_POLICY"):
            system, human = [message.content for message in build_assist_messages(context, "operation")]
            self.assertNotIn("MAPPING_ONLY_POLICY", system)
            self.assertIn("MAPPING_ONLY_POLICY", build_assist_messages(context, "write")[0].content)
        self.assertNotIn("数据库操作：delete", human)
        self.assertNotIn("response:response_body:price", human)
        for raw in ({"operation": None, "reason": "删除含义不明"}, {"operation": "drop", "reason": "猜测"},
                    {"operation": "read", "reason": " "}, {"operation": "read", "reason": "查询", "draft": {}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ASSIST_STEP_MODELS["operation"].model_validate(raw)
        decision = ASSIST_STEP_MODELS["operation"].model_validate({"operation": "delete", "reason": "删除方式不明", "questions": ["物理删除还是软删除？"]})
        self.assertIsNone(decision.operation)

    def test_operation_input_only_needs_summary_and_separate_requirements(self):
        """操作提示不读取字段清单，后续映射仍接收真实字段和已填补充。"""
        context = context_fixture()
        context["clarification"] = '返回字段 categoryName：固定值 "test"'
        minimal = {key: context[key] for key in ("endpoint", "selection", "instruction", "clarification")}
        human = build_assist_messages(minimal, "operation")[1].content
        for label in ("接口：", "名称：", "接口说明：", "当前数据表：", "需求说明：", "后续补充说明："):
            self.assertIn(label, human)
        for field_list in ("请求参数（", "返回字段（", "表字段（", "request:path:id", "column:price"):
            self.assertNotIn(field_list, human)
        mapped = build_assist_messages(context, "return")[1].content
        self.assertIn("request:path:id", mapped)
        self.assertIn("column:price", mapped)
        self.assertIn("后续补充说明：", mapped)

    def test_generation_requires_main_instruction_and_bounds_combined_input(self):
        """缺省、空白或只有补充均不能生成，分别传入的两段文本受同一上限约束。"""
        request, _, _ = fixture("E:/test")
        with self.assertRaises(ValueError):
            GenerateMappingRequest(**request)
        for instruction in ("", " \n\t "):
            with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                GenerateMappingRequest(**request, instruction=instruction, clarification="补充不能替代主需求")
        valid = GenerateMappingRequest(**request, instruction=" 查询商品 ", clarification=" 返回匹配总数 ")
        self.assertEqual(valid.instruction, "查询商品")
        self.assertEqual(valid.clarification, "返回匹配总数")
        GenerateMappingRequest(**request, instruction="需" * 3999, clarification="补")
        with self.assertRaisesRegex(ValueError, "合计"):
            GenerateMappingRequest(**request, instruction="需" * 3999, clarification="补充")

    def test_replacement_validates_against_new_operation(self):
        """旧草稿为查询时允许整体替换为新增，同时拒绝保留新增不适用的查询。"""
        context = context_fixture()
        generated_context = {**context, "databaseOperation": "create"}
        mapping = complete_mapping(candidates("write", {"writes": [{"column": "id", "inputs": ["request:path:id"]}]}, generated_context), generated_context)
        self.assertEqual(validate_generated_mapping(GeneratedMapping(**mapping), context)["databaseOperation"], "create")
        query = candidates("query", {"query": {"items": [{"column": "id", "op": "eq", "inputs": ["request:path:id"]}]}}, context)[0].value
        with self.assertRaises(ValueError):
            validate_generated_mapping(GeneratedMapping(**{**mapping, "databaseQuery": query}), context)
