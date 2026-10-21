"""操作识别驱动真实模型转换与服务编排，仅模拟模型输出和权威读取边界。"""

import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.endpoint_design_assist import ReplaceMappingRequest
from app.services.endpoint_design_assist import generate_mapping, replace_mapping
from app.services.endpoint_design_assist_retry import MappingGenerationFailure
from tests.test_endpoint_design_assist import fixture, generation_request, patches


def answer(content: str):
    """模拟聊天模型消息，保留真实 JSON 提取、严格解析和权威字段转换。"""
    return SimpleNamespace(content=content)


class EndpointDesignAssistOperationTests(unittest.IsolatedAsyncioTestCase):
    """覆盖不含旧草稿的实际模型链路与操作变化后的编排边界。"""

    async def test_inferred_operation_replaces_unselected_or_different_draft(self):
        """新增操作从零生成写入、返回和说明，不因旧查询操作生成查询条件。"""
        for old_operation in (None, "read"):
            request, preparation, metadata = fixture("E:/test")
            request["draft"]["databaseOperation"] = old_operation
            before = copy.deepcopy(request["draft"])
            model = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
                answer('{"operation":"create","reason":"契约要求新增商品"}'),
                answer('{"writes":[{"column":"id","inputs":["request:path:id"]}]}'),
                answer('{"returns":[{"field":"response:response_body:price","inputs":["column:price"]}]}'),
                answer('{"description":"按约定新增商品并返回售价"}'),
            ]))
            p1, p2, p3 = patches(preparation, metadata)
            with p1, p2, p3, patch("app.agents.endpoint_design_assist.Settings.from_env", return_value=object()), patch(
                "app.agents.endpoint_design_assist.create_chat_model", return_value=model,
            ):
                report = AsyncMock()
                result = await generate_mapping(generation_request(request), report)
                self.assertEqual([call.args[0].stage for call in report.await_args_list],
                                 ["context", "operation", "write", "return", "description", "validating"])
                first_prompt = str(model.ainvoke.await_args_list[0].args[0])
                self.assertNotIn("本轮识别的数据库操作", first_prompt)
                self.assertNotIn("request:path:id", first_prompt)
                self.assertNotIn("column:price", first_prompt)
                self.assertIn("request:path:id", str(model.ainvoke.await_args_list[1].args[0]))
                self.assertEqual(result["mapping"]["databaseOperation"], "create")
                applied = replace_mapping(ReplaceMappingRequest(**request, baseline=result["baseline"], mapping=result["mapping"]))
                self.assertEqual(applied["draft"]["databaseOperation"], "create")
                self.assertNotIn("databaseQuery", applied["draft"])
                self.assertEqual(request["draft"], before)

    async def test_ambiguity_stops_before_any_mapping(self):
        """软删除含义不明确时交付问题，不返回可应用的映射。"""
        request, preparation, metadata = fixture("E:/test")
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=answer(
            '{"operation":null,"reason":"未约定删除方式","questions":["物理删除还是软删除？"]}')))
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3, patch("app.agents.endpoint_design_assist.Settings.from_env", return_value=object()), patch(
            "app.agents.endpoint_design_assist.create_chat_model", return_value=model,
        ):
            result = await generate_mapping(generation_request(request), AsyncMock())
        self.assertIsNone(result["mapping"])
        self.assertIsNone(result["operation"])
        self.assertEqual(model.ainvoke.await_count, 1)
        self.assertEqual(result["questions"], ["物理删除还是软删除？"])

    async def test_retry_reuses_verified_operation_and_query(self):
        """写入失败重试保留新识别操作，从失败步骤继续，不重用旧草稿操作。"""
        request, preparation, metadata = fixture("E:/test")
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
            answer('{"operation":"update","reason":"根据商品 ID 修改"}'),
            answer('{"query":{"items":[{"column":"id","op":"eq","inputs":["request:path:id"]}]}}'),
            ValueError("写入生成失败"),
        ]))
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3, patch("app.agents.endpoint_design_assist.Settings.from_env", return_value=object()), patch(
            "app.agents.endpoint_design_assist.create_chat_model", return_value=model,
        ):
            with self.assertRaises(MappingGenerationFailure) as caught:
                await generate_mapping(generation_request(request), AsyncMock())
            self.assertEqual(caught.exception.failure["completedStages"], ["operation", "query"])
            model.ainvoke.side_effect = [answer('{"writes":[]}'), answer('{"returns":[]}'), answer('{"description":"写入待明确"}')]
            report = AsyncMock()
            result = await generate_mapping(generation_request(request, retryToken=caught.exception.failure["retryToken"]), report)
            self.assertEqual([call.args[0].stage for call in report.await_args_list], ["context", "write", "return", "description", "validating"])
            self.assertEqual(result["mapping"]["databaseOperation"], "update")

    async def test_cancellation_does_not_become_retry_failure(self):
        """操作识别取消直接结束任务，不转成业务失败或继续映射。"""
        request, preparation, metadata = fixture("E:/test")
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3, patch("app.agents.endpoint_design_assist.create_chat_model", return_value=SimpleNamespace(
            ainvoke=AsyncMock(side_effect=asyncio.CancelledError()),
        )), patch("app.agents.endpoint_design_assist.Settings.from_env", return_value=object()):
            with self.assertRaises(asyncio.CancelledError):
                await generate_mapping(generation_request(request), AsyncMock())
