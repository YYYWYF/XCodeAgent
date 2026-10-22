"""外部映射分步编排、取消和独立 AG-UI 生命周期回归。"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.domain.endpoint_design_assist import MappingModelResult
from app.domain.endpoint_design_external_assist import ExternalGenerateRequest, ExternalRefineRequest
from app.protocols.endpoint_designs import build_endpoint_designs_ag_ui_stream, endpoint_designs_capabilities
from app.services.endpoint_design_external_assist import generate_external_mapping, refine_external_rule
from app.services.endpoint_design_assist_retry import MappingGenerationFailure
from app.services.endpoint_design_external_assist_validation import complete_external_mapping
from tests.test_endpoint_design_external_assist import fixture, patches, context_fixture, candidates_fixture


def model_result(context, *, step, **kwargs):
    """模拟模型转换后的候选，保留真实服务编排和确定性校验。"""
    candidates = [item for item in candidates_fixture(context) if item.area == step] if step != "description" else []
    return MappingModelResult(candidates=candidates, questions=[]).model_dump(by_alias=True)


async def collect(action, values):
    """消费标准流的完整生命周期，不引入产品侧手写解析器。"""
    payload = dict(runId="external-run", threadId="external-thread", forwardedProps=dict(endpointDesigns=dict(action=action, **values)))
    return [json.loads(frame.removeprefix("data: ").strip()) async for frame in build_endpoint_designs_ag_ui_stream(payload=payload)]


class ExternalAssistStreamTests(unittest.IsolatedAsyncioTestCase):
    """在无网络、无真实模型的条件下验证阶段顺序和事务边界。"""

    async def test_generation_order_and_scope(self):
        """模型依次收到入参、返回、说明，既不接收旧草稿，也不识别数据库操作。"""
        request, preparation, metadata = fixture()
        p1, p2 = patches(preparation, metadata)
        progress = AsyncMock()
        with p1, p2, patch("app.services.endpoint_design_external_assist.invoke_mapping_assistant", new=AsyncMock(side_effect=model_result)) as model:
            result = await generate_external_mapping(ExternalGenerateRequest(**request, instruction="查询商品"), progress)
        self.assertEqual([call.kwargs["step"] for call in model.await_args_list], ["request", "return", "description"])
        self.assertEqual([call.args[0].stage for call in progress.await_args_list], ["context", "request", "return", "description", "validating"])
        for call in model.await_args_list:
            self.assertNotIn("draft", call.args[0])
            self.assertNotIn("CONNECTION_SECRET", str(call.args[0]))
        self.assertIn("external:query:keyword", model.await_args_list[1].args[0]["generatedSummary"])
        self.assertIsNone(result["operation"])
        self.assertNotIn("databaseOperation", result["mapping"])

    async def test_retry_only_validated_predecessors_and_reject_changed_input(self):
        """失败重试只恢复服务端已校验的步骤，输入变化不能复用旧凭据。"""
        request, preparation, metadata = fixture()
        p1, p2 = patches(preparation, metadata)
        with p1, p2, patch("app.services.endpoint_design_external_assist.invoke_mapping_assistant", new=AsyncMock(side_effect=[
            model_result(context_fixture(), step="request"), ValueError("返回生成失败")])):
            with self.assertRaises(MappingGenerationFailure) as caught:
                await generate_external_mapping(ExternalGenerateRequest(**request, instruction="查询商品"), AsyncMock())
        failure = caught.exception.failure
        self.assertEqual(failure["failedStage"], "return")
        self.assertEqual(failure["completedStages"], ["request"])
        p1, p2 = patches(preparation, metadata)
        with p1, p2, patch("app.services.endpoint_design_external_assist.invoke_mapping_assistant", new=AsyncMock(side_effect=model_result)) as model:
            with self.assertRaisesRegex(ValueError, "过期"):
                await generate_external_mapping(ExternalGenerateRequest(**request, instruction="不同需求", retryToken=failure["retryToken"]), AsyncMock())
            result = await generate_external_mapping(ExternalGenerateRequest(**request, instruction="查询商品", retryToken=failure["retryToken"]), AsyncMock())
            self.assertEqual([call.kwargs["step"] for call in model.await_args_list], ["return", "description"])
            self.assertTrue(result["mapping"]["externalApiBindings"][0]["right"])

    async def test_cancel_terminates_current_model(self):
        """取消生成将取消正在等待的模型任务，不启动下一步或返回半套结果。"""
        request, preparation, metadata = fixture()
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def waiting_model(*args, **kwargs):
            """模拟可取消的模型等待，确认任务终止而非仅丢弃界面结果。"""
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        p1, p2 = patches(preparation, metadata)
        with p1, p2, patch("app.services.endpoint_design_external_assist.invoke_mapping_assistant", new=waiting_model):
            task = asyncio.create_task(generate_external_mapping(ExternalGenerateRequest(**request, instruction="查询商品"), AsyncMock()))
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(cancelled.is_set())

    async def test_rule_selected_dependencies_and_direction(self):
        """规则仅完善文字，入参不能依赖尚未取得的外部响应。"""
        request, preparation, metadata = fixture()
        dependency = dict(kind="endpoint", field=preparation["payload"]["endpointFields"][0])
        values = dict(**request, target=dict(area="request", key="external:query:keyword"), dependencies=[dependency], description="去除首尾空格")
        p1, p2 = patches(preparation, metadata)
        with p1, p2, patch("app.services.endpoint_design_external_assist.invoke_mapping_assistant", new=AsyncMock(return_value=dict(description="去除首尾空格后传递", questions=[]))) as model:
            result = await refine_external_rule(ExternalRefineRequest(**values), AsyncMock())
            self.assertEqual(set(result), {"description", "questions"})
            self.assertEqual(model.await_args.args[0]["dependencies"], [dependency])
            values["dependencies"] = [dict(kind="source", field=dict(**request["selection"], section="response_body", path="name", type="string", description="名称"))]
            with self.assertRaisesRegex(ValueError, "外部请求规则"):
                await refine_external_rule(ExternalRefineRequest(**values), AsyncMock())
            self.assertEqual(model.await_count, 1)

    async def test_all_actions_complete_standard_lifecycle(self):
        """三个动作均通过当前 Endpoint AG-UI 流交付标准消息、结果和状态。"""
        request, preparation, metadata = fixture()
        context = context_fixture()
        mapping = complete_external_mapping(candidates_fixture(context), context)
        for action, extra, key in (
            ("generate_mapping", dict(instruction="查询商品"), "assistance"),
            ("replace_mapping", dict(baseline=context["baseline"], mapping=mapping), "applied"),
            ("refine_rule", dict(target=dict(area="request", key="external:query:keyword"), dependencies=[], description="固定测试关键词"), "refinedRule"),
        ):
            p1, p2 = patches(preparation, metadata)
            model = AsyncMock(return_value=dict(description="固定测试关键词", questions=[])) if action == "refine_rule" else AsyncMock(side_effect=model_result)
            with self.subTest(action=action), p1, p2, patch("app.services.preview_runtime_guard.record_product_interaction"), patch("app.services.endpoint_design_external_assist.invoke_mapping_assistant", new=model):
                frames = await collect(action, {**request, **extra})
            types = [frame["type"] for frame in frames]
            self.assertEqual(types[0], "RUN_STARTED")
            self.assertEqual(types[-1], "RUN_FINISHED")
            for event in ("TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT"):
                self.assertIn(event, types)
            result = frames[-1]["result"]["endpointDesigns"]
            self.assertEqual(result["status"], "completed", result)
            self.assertIn(key, result)
        self.assertEqual(endpoint_designs_capabilities()["externalMappingGenerationSteps"], ["request", "return", "description"])

    async def test_business_failure_finishes_lifecycle(self):
        """非法请求与分步模型失败均完整结束，并保留明确的失败阶段。"""
        request, preparation, metadata = fixture()
        for instruction in ("", "查询商品"):
            p1, p2 = patches(preparation, metadata)
            with p1, p2, patch("app.services.endpoint_design_external_assist.invoke_mapping_assistant", new=AsyncMock(side_effect=ValueError("模型失败"))):
                frames = await collect("generate_mapping", {**request, "instruction": instruction})
            self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
            result = frames[-1]["result"]["endpointDesigns"]
            self.assertEqual(result["status"], "failed")
            if instruction:
                self.assertEqual(result["assistFailure"]["failedStage"], "request")
