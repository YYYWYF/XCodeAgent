"""字段映射 AI 辅助的 AG-UI 生命周期、失败与取消回归。"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.protocols.ag_ui_action_stream import AgUiActionProgress
from app.protocols.endpoint_designs import build_endpoint_designs_ag_ui_stream, endpoint_designs_capabilities
from tests.test_endpoint_design_assist import fixture
from app.services.endpoint_design_assist_retry import MappingGenerationFailure


def payload(action: str) -> dict:
    """构造独立 Endpoint 请求，不使用真实工作区或运行主 Workflow。"""
    request, _, _ = fixture("E:/test-workspace")
    extra = {"instruction": "查询商品售价", "clarification": "售价保留两位小数"} if action == "generate_mapping" else {
        "target": {"area": "return", "key": "response:response_body:price"}, "dependencies": [], "description": "保留两位小数"
    } if action == "refine_rule" else {
        "baseline": {"draftHash": "a" * 64, "metadataHash": "b" * 64},
        "mapping": {"databaseOperation": "read", "fieldMappings": request["draft"]["fieldMappings"], "implementationDescription": "商品详情"}
    }
    return {"runId": "assist-run", "threadId": "assist-thread", "forwardedProps": {"endpointDesigns": {"action": action, **request, **extra}}}


async def collect(input_data: dict) -> list[dict]:
    """解析测试协议帧以断言标准生命周期，不模拟前端传输。"""
    stream = build_endpoint_designs_ag_ui_stream(payload=input_data)
    return [json.loads(frame.removeprefix("data: ").strip()) async for frame in stream]


class EndpointDesignAssistStreamTests(unittest.IsolatedAsyncioTestCase):
    """验证三种辅助动作共用原协议，不遗漏业务失败的终帧。"""

    async def test_stage_failure_projects_resume_info_with_complete_lifecycle(self):
        """失败阶段及恢复凭据跟随标准失败结果和快照，不伪装成生成成功。"""
        failure = MappingGenerationFailure("返回步骤失败", token="a" * 32, stage="return", completed=["operation", "query"])
        with patch("app.protocols.endpoint_designs.generate_mapping", new=AsyncMock(side_effect=failure)):
            frames = await collect(payload("generate_mapping"))
        result = frames[-1]["result"]["endpointDesigns"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["assistFailure"], failure.failure)
        snapshot = next(frame["snapshot"]["endpointDesigns"] for frame in frames if frame["type"] == "STATE_SNAPSHOT")
        self.assertEqual(snapshot["assistFailure"], failure.failure)
        self.assertEqual(frames[-1]["type"], "RUN_FINISHED")

    async def test_success_for_all_actions(self):
        """建议、规则与应用成功都提供助手消息、结果、快照和运行结束。"""
        for action, operation, result_key in (
            ("generate_mapping", "generate_mapping", "assistance"),
            ("refine_rule", "refine_rule", "refinedRule"),
            ("replace_mapping", "replace_mapping", "applied"),
        ):
            with self.subTest(action=action), patch("app.services.preview_runtime_guard.record_product_interaction"), patch(
                f"app.protocols.endpoint_designs.{operation}", new=AsyncMock(return_value={"marker": action, "mapping": {}}) if action != "replace_mapping" else lambda _request: {"marker": action}
            ):
                frames = await collect(payload(action))
                types = [frame["type"] for frame in frames]
                self.assertEqual(types[0], "RUN_STARTED")
                self.assertEqual(types[-1], "RUN_FINISHED")
                for event in ("TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT"):
                    self.assertIn(event, types)
                result = frames[-1]["result"]["endpointDesigns"]
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result[result_key]["marker"], action)

    async def test_model_failure_and_invalid_input_finish_run(self):
        """模型失败及严格请求校验失败都返回结构化业务错误。"""
        for invalid in (False, True):
            input_data = payload("generate_mapping")
            if invalid:
                input_data["forwardedProps"]["endpointDesigns"]["unexpected"] = True
            with patch("app.protocols.endpoint_designs.generate_mapping", new=AsyncMock(side_effect=ValueError("模型结果无效"))):
                frames = await collect(input_data)
            result = frames[-1]["result"]["endpointDesigns"]
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["action"], "generate_mapping")
            self.assertIn("error", result)
            self.assertEqual(frames[-1]["type"], "RUN_FINISHED")

    async def test_empty_main_instruction_fails_before_generation_with_complete_lifecycle(self):
        """只有后续补充不能替代必填需求，失败仍交付结果、快照和完整结束帧。"""
        for instruction in (None, "", " \n\t "):
            input_data = payload("generate_mapping")
            values = input_data["forwardedProps"]["endpointDesigns"]
            if instruction is None:
                values.pop("instruction")
            else:
                values["instruction"] = instruction
            with patch("app.protocols.endpoint_designs.generate_mapping", new=AsyncMock()) as model:
                frames = await collect(input_data)
                model.assert_not_awaited()
            self.assertEqual(frames[0]["type"], "RUN_STARTED")
            self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
            result = frames[-1]["result"]["endpointDesigns"]
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["action"], "generate_mapping")
            self.assertIn("error", result)
            for event in ("TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT"):
                self.assertIn(event, [frame["type"] for frame in frames])

    async def test_operation_ambiguity_finishes_with_questions(self):
        """待明确操作通过正常生命周期交付问题，不冒充完整映射或失败重试。"""
        result = {"mapping": None, "operation": None, "operationReason": "删除方式不明", "questions": ["是否软删除？"], "reviews": []}
        with patch("app.protocols.endpoint_designs.generate_mapping", new=AsyncMock(return_value=result)):
            frames = await collect(payload("generate_mapping"))
        value = frames[-1]["result"]["endpointDesigns"]
        self.assertEqual(value["status"], "completed")
        self.assertIsNone(value["assistance"]["mapping"])
        self.assertIn("STATE_SNAPSHOT", [frame["type"] for frame in frames])

    async def test_progress_and_cancel_stop_model_task(self):
        """关闭运行流应取消正在等待的模型任务，不产生应用结果。"""
        cancelled = asyncio.Event()

        async def waiting(_request, report, _thread_id):
            """发送阶段后等待取消，用于检验真实公共流的任务收口。"""
            try:
                await report(AgUiActionProgress("generating", "正在生成映射建议", 35))
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch("app.protocols.endpoint_designs.generate_mapping", new=waiting):
            stream = build_endpoint_designs_ag_ui_stream(payload=payload("generate_mapping"))
            self.assertIn("RUN_STARTED", await anext(stream))
            self.assertIn("TEXT_MESSAGE_START", await anext(stream))
            progress = await anext(stream)
            self.assertIn('"status":"in_progress"', progress)
            await stream.aclose()
            await asyncio.wait_for(cancelled.wait(), timeout=1)

    def test_capabilities_publish_assist_actions(self):
        """能力元数据继续指向原独立端点并声明全部辅助动作。"""
        capabilities = endpoint_designs_capabilities()
        self.assertEqual(capabilities["endpoint"], "/endpoint-designs/run")
        self.assertTrue({"generate_mapping", "refine_rule", "replace_mapping"}.issubset(capabilities["actions"]))
        self.assertEqual(capabilities["mappingGenerationSteps"][0], "operation")
