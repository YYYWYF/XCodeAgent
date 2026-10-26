"""数据库字段映射辅助的局部校验与零写入回归。"""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.domain.endpoint_design_assist import ReplaceMappingRequest, RefineRuleRequest, GenerateMappingRequest
from app.services.endpoint_design_assist import replace_mapping, load_assist_context, refine_rule, generate_mapping
from app.services.endpoint_design_assist_retry import MappingGenerationFailure


def fixture(workspace: str) -> tuple[dict, dict, dict]:
    """提供完整请求参数、未完成返回字段及公开表结构。"""
    selection = {"sourceType": "database", "sourceId": "db", "schema": "shop", "table": "product"}
    parameter = {"side": "request", "location": "path", "path": "id", "type": "integer", "required": True, "description": "商品 ID"}
    response = {"side": "response", "location": "response_body", "path": "price", "type": "number", "required": True, "description": "售价"}
    draft = {"apiContractId": "products", "endpointId": "detail", "sourceBinding": selection,
             "databaseOperation": "read", "fieldMappings": [{"mappingType": "unconfigured", "endpointField": response}], "databaseWrites": []}
    request = {"workspaceRoot": workspace, "apiContractId": "products", "endpointId": "detail",
               "technicalPlanHash": "a" * 64, "selection": selection, "draft": draft}
    preparation = {"technicalPlanHash": "a" * 64, "artifactRevision": None,
                   "payload": {"endpoint": {"method": "GET", "path": "/product/{id}"}, "endpointFields": [parameter, response]}}
    metadata = {"schema": "shop", "columns": [{"name": "price", "type": "decimal", "description": "售价"},
                                                 {"name": "id", "type": "int", "description": "商品 ID"}]}
    return request, preparation, metadata


def generation_request(request: dict, **values) -> GenerateMappingRequest:
    """为生成用例显式提供主需求，不把生成输入混入替换或规则请求。"""
    return GenerateMappingRequest(**{**request, "instruction": "按接口约定处理当前商品", **values})


def patches(preparation: dict, metadata: dict):
    """在服务边界模拟契约、列与内置 DDL 投影，禁止回归访问实际数据库。"""
    return (patch("app.services.endpoint_design_assist.prepare_endpoint_design", return_value=preparation),
            patch("app.services.endpoint_design_assist.selected_tables", return_value=[{"sourceId": "db", "schema": "shop", "table": "product"}]),
            patch.multiple("app.services.endpoint_design_assist",
                           load_database_columns=Mock(return_value=metadata),
                           load_mapping_table_ddl=Mock(return_value=None)))


def price_candidate(request: dict, preparation: dict) -> dict:
    """构造合法返回建议，供差异和篡改回归共用。"""
    return {"area": "return", "key": "response:response_body:price", "reason": "类型及业务含义匹配",
            "value": {"mappingType": "source_mapping", "endpointField": preparation["payload"]["endpointFields"][1],
                      "sourceFields": [{**request["selection"], "column": "price", "type": "decimal", "usage": "read", "description": "售价"}], "processingType": "direct"}}


def step_model(candidates: list[dict] | None = None, questions: list[str] | None = None, *, operation: str = "read") -> AsyncMock:
    """模拟分步调用已完成转换的结果，各步骤只返回自身区域。"""
    def result(_context, *, step, **_kwargs):
        """按步骤返回候选，保留服务层范围与组合校验。"""
        if step == "operation":
            return {"operation": operation, "reason": "根据正式契约的业务用途判断", "questions": []}
        return {"candidates": [item for item in candidates or [] if item["area"] == step],
                "questions": questions or []}
    return AsyncMock(side_effect=result)


class EndpointDesignAssistTests(unittest.IsolatedAsyncioTestCase):
    """验证从零生成、完整替换、过期保护和无落盘边界。"""

    async def test_failed_stage_resumes_only_verified_predecessors(self):
        """失败阶段重跑不重复调用前序阶段，也不复用失败输出；整套完成后才交付结果。"""
        request, preparation, metadata = fixture("E:/test")
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=AsyncMock(side_effect=[
            {"operation": "read", "reason": "查询商品", "questions": []},
            {"candidates": [], "questions": ["请核对查询范围"]}, ValueError("返回步骤失败")])) as model:
            with self.assertRaises(MappingGenerationFailure) as caught:
                await generate_mapping(generation_request(request), AsyncMock())
            failure = caught.exception.failure
            self.assertEqual(failure["failedStage"], "return")
            self.assertEqual(failure["completedStages"], ["operation", "query"])
            self.assertEqual(model.await_count, 3)
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model([price_candidate(request, preparation)])) as model:
            result = await generate_mapping(generation_request(request, retryToken=failure["retryToken"]), AsyncMock())
            self.assertEqual([call.kwargs["step"] for call in model.await_args_list], ["return", "description"])
            self.assertIn("请核对查询范围", model.await_args_list[0].args[0]["generatedSummary"])
            self.assertIn("请核对查询范围", result["questions"])
            self.assertEqual(len(result["reviews"]), 1)
            with self.assertRaisesRegex(ValueError, "过期"):
                await generate_mapping(generation_request(request, retryToken=failure["retryToken"]), AsyncMock())

    async def test_retry_rejects_changed_requirements_before_model_call(self):
        """补充内容变化后必须整套生成，不能把旧前序结果视为已经使用了新要求。"""
        request, preparation, metadata = fixture("E:/test")
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=AsyncMock(side_effect=ValueError("失败"))):
            with self.assertRaises(MappingGenerationFailure) as caught:
                await generate_mapping(generation_request(request), AsyncMock())
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model()) as model:
            with self.assertRaisesRegex(ValueError, "输入已变化"):
                await generate_mapping(generation_request(request, instruction="新需求", retryToken=caught.exception.failure["retryToken"]), AsyncMock())
            model.assert_not_awaited()

    async def test_generate_ignores_old_config_and_replace_all(self):
        """旧查询、写入、说明及规则不进入提示词，也不会被静默保留。"""
        with tempfile.TemporaryDirectory() as workspace:
            request, preparation, metadata = fixture(workspace)
            request["draft"].update(databaseQuery={"join": "and", "items": []}, databaseWrites=[{"column": "old"}], implementationDescription="OLD_SECRET")
            request["draft"]["fieldMappings"][0] = {**price_candidate(request, preparation)["value"], "processingType": "single_field_description", "businessDescription": "OLD_RULE", "missingBehavior": "default", "defaultValue": 7}
            p1, p2, p3 = patches(preparation, metadata)
            with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model([price_candidate(request, preparation)])) as model:
                result = await generate_mapping(generation_request(request), AsyncMock())
                sent = model.call_args.args[0]
                self.assertNotIn("draft", sent)
                self.assertNotIn("baseline", sent)
                self.assertNotIn("OLD_", str(sent))
                self.assertEqual(sent["databaseOperation"], "read")
                applied = replace_mapping(ReplaceMappingRequest(**request, baseline=result["baseline"], mapping=result["mapping"]))
                self.assertNotIn("databaseQuery", applied["draft"])
                self.assertEqual(applied["draft"]["databaseWrites"], [])
                self.assertEqual(applied["draft"]["implementationDescription"], "")
                self.assertEqual(applied["draft"]["fieldMappings"][0]["processingType"], "direct")
                self.assertEqual(applied["draft"]["fieldMappings"][1]["endpointField"]["side"], "request")
                self.assertEqual(applied["draft"]["fieldMappings"][1]["mappingType"], "unconfigured")
                self.assertNotIn("defaultValue", applied["draft"]["fieldMappings"][0])
                self.assertEqual(request["draft"]["implementationDescription"], "OLD_SECRET")
                self.assertEqual(list(Path(workspace).iterdir()), [])

    async def test_unknown_targets_remain_unconfigured(self):
        """省略未知目标时返回完整契约字段和明确问题，不沿用旧取值。"""
        with tempfile.TemporaryDirectory() as workspace:
            request, preparation, metadata = fixture(workspace)
            request["draft"]["fieldMappings"] = [price_candidate(request, preparation)["value"]]
            p1, p2, p3 = patches(preparation, metadata)
            with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model(questions=["请明确价格单位"])):
                result = await generate_mapping(generation_request(request), AsyncMock())
                self.assertEqual(result["mapping"]["fieldMappings"][0]["mappingType"], "unconfigured")
                self.assertTrue(any("price" in question for question in result["questions"]))
                applied = replace_mapping(ReplaceMappingRequest(**request, baseline=result["baseline"], mapping=result["mapping"]))
                self.assertEqual(applied["draft"]["fieldMappings"][0]["mappingType"], "unconfigured")

    async def test_snapshot_changes_block_application(self):
        """草稿、实时列、契约和正式版本变化都阻止整体替换。"""
        with tempfile.TemporaryDirectory() as workspace:
            request, preparation, metadata = fixture(workspace)
            p1, p2, p3 = patches(preparation, metadata)
            with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model()):
                result = await generate_mapping(generation_request(request), AsyncMock())
                for change in ("draft", "metadata", "revision", "contract"):
                    modified = copy.deepcopy(request)
                    if change == "draft":
                        modified["draft"]["implementationDescription"] = "用户编辑"
                    if change == "metadata":
                        metadata["columns"][0]["type"] = "varchar"
                    if change == "revision":
                        preparation["artifactRevision"] = "b" * 32
                    if change == "contract":
                        preparation["technicalPlanHash"] = "b" * 64
                    with self.subTest(change=change), self.assertRaises(ValueError):
                        replace_mapping(ReplaceMappingRequest(**modified, baseline=result["baseline"], mapping=result["mapping"]))
                    metadata["columns"][0]["type"] = "decimal"
                    preparation["artifactRevision"] = None
                    preparation["technicalPlanHash"] = "a" * 64

    async def test_invalid_columns_targets_and_duplicates_rejected(self):
        """生成与替换均拒绝跨表、未知列、类型错误和重复返回目标。"""
        with tempfile.TemporaryDirectory() as workspace:
            request, preparation, metadata = fixture(workspace)
            valid = price_candidate(request, preparation)
            for key, value in (("table", "other"), ("column", "missing"), ("type", "varchar")):
                candidate = copy.deepcopy(valid)
                candidate["value"]["sourceFields"][0][key] = value
                p1, p2, p3 = patches(preparation, metadata)
                with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model([candidate])), self.assertRaises(ValueError):
                    await generate_mapping(generation_request(request), AsyncMock())
            p1, p2, p3 = patches(preparation, metadata)
            with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model([valid])):
                result = await generate_mapping(generation_request(request), AsyncMock())
                for mode in ("duplicate", "missing", "forged", "foreign"):
                    mapping = copy.deepcopy(result["mapping"])
                    if mode == "duplicate":
                        mapping["fieldMappings"] *= 2
                    elif mode == "missing":
                        mapping["fieldMappings"] = []
                    elif mode == "forged":
                        mapping["fieldMappings"] = [{"mappingType": "unconfigured", "endpointField": {**preparation["payload"]["endpointFields"][1], "type": "string"}}]
                    else:
                        mapping["fieldMappings"][0]["sourceFields"][0]["table"] = "foreign"
                    with self.subTest(mode=mode), self.assertRaises(ValueError):
                        replace_mapping(ReplaceMappingRequest(**request, baseline=result["baseline"], mapping=mapping))

    async def test_crud_and_grouped_query(self):
        """CRUD 范围严格约束，查询分组整体生成；未完成写入可替换为草稿。"""
        with tempfile.TemporaryDirectory() as workspace:
            request, preparation, metadata = fixture(workspace)
            condition = {**request["selection"], "kind": "condition", "column": "id", "type": "int", "operator": "eq",
                         "right": {"kind": "endpoint", "endpointField": preparation["payload"]["endpointFields"][0]}}
            tree = {"join": "and", "items": [{"kind": "group", "join": "or", "items": [condition]}]}
            query = {"area": "query", "key": "databaseQuery", "value": tree, "reason": "按 ID"}
            write = {"area": "write", "key": "id", "value": {**request["selection"], "column": "id", "type": "int", "right": condition["right"]}, "reason": "写入"}
            for operation in ("read", "create", "update", "delete"):
                request["draft"]["databaseOperation"] = operation
                candidates = ([query] if operation != "create" else []) + ([write] if operation in {"create", "update"} else [])
                p1, p2, p3 = patches(preparation, metadata)
                with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model(candidates, operation=operation)) as model:
                    result = await generate_mapping(generation_request(request), AsyncMock())
                    expected = ["operation"] + (["query"] if operation != "create" else []) + (["write"] if operation in {"create", "update"} else []) + ["return", "description"]
                    self.assertEqual([call.kwargs["step"] for call in model.await_args_list], expected)
                    applied = replace_mapping(ReplaceMappingRequest(**request, baseline=result["baseline"], mapping=result["mapping"]))
                    if operation != "create":
                        self.assertEqual(applied["draft"]["databaseQuery"]["items"][0]["join"], "or")
                    else:
                        self.assertNotIn("databaseQuery", applied["draft"])
                invalid = [query] if operation == "create" else [write] if operation in {"read", "delete"} else [query, query]
                p1, p2, p3 = patches(preparation, metadata)
                with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=AsyncMock(side_effect=[
                    {"operation": operation, "reason": "契约指定操作", "questions": []}, {"candidates": invalid}
                ])), self.assertRaises(ValueError):
                    await generate_mapping(generation_request(request), AsyncMock())

    async def test_generation_rejects_hidden_defaults_and_builtin_inputs(self):
        """新生成规则不能引入隐藏默认值、缺值策略或内置依赖。"""
        with tempfile.TemporaryDirectory() as workspace:
            request, preparation, metadata = fixture(workspace)
            for policy in ({"missingBehavior": "default", "defaultValue": 0}, {"builtinFields": ["current_user_id"]}):
                candidate = price_candidate(request, preparation)
                candidate["value"].update(processingType="single_field_description", businessDescription="处理售价", **policy)
                p1, p2, p3 = patches(preparation, metadata)
                with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model([candidate])), self.assertRaises(ValueError):
                    await generate_mapping(generation_request(request), AsyncMock())

    async def test_incomplete_update_draft_remains_explicit(self):
        """无法生成查询和写入时允许草稿预览，但必须返回未完成说明。"""
        with tempfile.TemporaryDirectory() as workspace:
            request, preparation, metadata = fixture(workspace)
            request["draft"]["databaseOperation"] = "update"
            p1, p2, p3 = patches(preparation, metadata)
            with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model(operation="update")):
                result = await generate_mapping(generation_request(request), AsyncMock())
                self.assertTrue(any("查询条件" in q for q in result["questions"]))
                self.assertTrue(any("写入" in q for q in result["questions"]))
                applied = replace_mapping(ReplaceMappingRequest(**request, baseline=result["baseline"], mapping=result["mapping"]))
                self.assertEqual(applied["draft"]["databaseWrites"], [])

    async def test_rule_depends_on_exact_selected_inputs(self):
        """独立规则完善仍只返回说明，不能改变依赖和隐藏策略。"""
        with tempfile.TemporaryDirectory() as workspace:
            request, preparation, metadata = fixture(workspace)
            source = price_candidate(request, preparation)["value"]["sourceFields"][0]
            args = {**request, "target": {"area": "return", "key": "response:response_body:price"},
                    "description": "保留两位小数", "dependencies": [{"kind": "source", "field": source}]}
            p1, p2, p3 = patches(preparation, metadata)
            with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=AsyncMock(return_value={"description": "保留两位小数", "questions": []})) as model:
                result = await refine_rule(RefineRuleRequest(**args), AsyncMock())
                self.assertEqual(set(result), {"description", "questions"})
                self.assertEqual(model.call_args.args[0]["dependencies"], args["dependencies"])
                args["draft"]["databaseOperation"] = "update"
                args["target"] = {"area": "write", "key": "price"}
                with self.assertRaises(ValueError):
                    await refine_rule(RefineRuleRequest(**args), AsyncMock())

    async def test_model_uses_small_step_output_without_schemas(self):
        """实际调用只要求当前步骤的简洁输出，规则输出拒绝额外依赖。"""
        from types import SimpleNamespace
        from app.agents.endpoint_design_assist import invoke_mapping_assistant
        request, preparation, metadata = fixture("E:/test")
        context = {"endpoint": preparation["payload"]["endpoint"], "endpointFields": preparation["payload"]["endpointFields"],
                   "columns": metadata["columns"], "selection": request["selection"], "databaseOperation": "read",
                   "tableDdl": None, "instruction": "根据商品 ID 查询售价"}
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content='{"returns":[{"field":"response:response_body:price","inputs":["column:price"]}],"questions":[]}')))
        with patch("app.agents.endpoint_design_assist.Settings.from_env", return_value=object()), patch("app.agents.endpoint_design_assist.create_chat_model", return_value=model):
            result = await invoke_mapping_assistant(context, step="return", thread_id="assist-thread")
            prompt = model.ainvoke.call_args.args[0][0].content
            self.assertNotIn("$defs", prompt)
            self.assertNotIn("sourceFields", prompt)
            self.assertIn('"returns"', prompt)
            self.assertEqual(result["candidates"][0]["value"]["sourceFields"][0]["type"], "decimal")
            model.ainvoke.return_value.content = '{"description":"规则","dependencies":[]}'
            with self.assertRaises(ValueError):
                await invoke_mapping_assistant({"target": {"area": "return", "key": "price"}, "dependencies": [], "description": "价格"}, step="rule")

    async def test_progress_summary_failure_and_cancellation(self):
        """分步进度按顺序报告，说明读取本轮摘要，失败或取消不进入后续步骤。"""
        import asyncio
        request, preparation, metadata = fixture("E:/test")
        p1, p2, p3 = patches(preparation, metadata)
        report = AsyncMock()
        with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=step_model([price_candidate(request, preparation)])) as model:
            await generate_mapping(generation_request(request), report)
            self.assertEqual([call.args[0].stage for call in report.await_args_list], ["context", "operation", "query", "return", "description", "validating"])
            self.assertEqual([call.args[0].percent for call in report.await_args_list], [10, 15, 30, 50, 70, 95])
            self.assertIn("column:price", model.await_args_list[-1].args[0]["generatedSummary"])
            self.assertNotIn("generatedSummary", model.await_args_list[0].args[0])
        for failure in (ValueError("模型失败"), asyncio.CancelledError()):
            p1, p2, p3 = patches(preparation, metadata)
            with p1, p2, p3, patch("app.services.endpoint_design_assist.invoke_mapping_assistant", new=AsyncMock(side_effect=[{"operation": "read", "reason": "查询", "questions": []}, failure])) as model:
                with self.assertRaises(type(failure)):
                    await generate_mapping(generation_request(request), AsyncMock())
                self.assertEqual(model.await_count, 2)
                self.assertNotIn("implementationDescription", request["draft"])

    def test_strict_requests_reject_old_scope_and_arbitrary_identity(self):
        """只支持当前完整替换协议，不接受局部范围或任意来源写入。"""
        from app.domain.endpoint_design_assist import GeneratedMapping
        request, _, _ = fixture("E:/test")
        with self.assertRaises(ValueError):
            generation_request(request, scope="return")
        with self.assertRaises(ValueError):
            GeneratedMapping(sourceBinding={})
