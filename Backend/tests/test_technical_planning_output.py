"""验证技术规划公开输出在执行结束前到达真实 AG-UI 流。"""

import asyncio
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.main.planner import _invoke_prompt_with_chat_model
from app.agents.main.technical_plan_editor import edit_technical_plan_with_agent
from app.config import Settings
from app.graph.nodes.planning import _planning_token_callback
from app.persistence.checkpoints import close_workflow_checkpointer
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.technical_planning_output import OUTPUT_EVENT, emit_technical_planning_activity
from tests.test_technical_plan_fragment_editor import ScriptedEditorModel, candidate_plan, tool_response


class TechnicalPlanningOutputTests(TestCase):
    """真实编辑工具发出开始和结果，公开文本分片不包含思考内容。"""

    def test_streams_text_blocks_without_exposing_reasoning(self):
        """启用思考的规划片段也实时发送正文，不等待 invoke 收齐，不泄露推理块。"""
        received = []
        model = SimpleNamespace(stream=lambda _: iter([
            AIMessageChunk(content=[{"type": "thinking", "thinking": "PRIVATE_REASONING"}]),
            AIMessageChunk(content=[{"type": "text", "text": "第一段"}]),
            AIMessageChunk(content="第二段"),
        ]))
        with patch("app.agents.main.planner.create_chat_model", return_value=model):
            text = _invoke_prompt_with_chat_model("生成", settings=Settings(), on_token=received.append, thinking_enabled=True)
        self.assertEqual(received, ["第一段", "第二段"])
        self.assertEqual(text, "第一段第二段")

    def test_real_editor_reports_failed_and_successful_tool_calls(self):
        """真实 Agent 工具结果驱动状态，不把失败编辑包装成成功进度。"""
        from langchain_core.messages import AIMessage

        model = ScriptedEditorModel(responses=[
            tool_response("edit_file", file_path="/technical-plan.json", old_string="missing", new_string="x"),
            tool_response("edit_file", file_path="/technical-plan.json", old_string='"default": 20', new_string='"default": 8'),
            AIMessage(content="修改完成"),
        ])
        activity = []
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model), \
             patch("app.services.technical_planning_output.get_stream_writer", return_value=activity.append):
            result = edit_technical_plan_with_agent(
                {"confirmed_product_plan": {"pages": []}, "planning_adjustment_request": "分页改为8"},
                candidate_plan(), settings=Settings(),
            )
        self.assertEqual([item["status"] for item in activity], ["running", "failed", "running", "completed"])
        self.assertTrue(all(item["type"] == OUTPUT_EVENT for item in activity))
        self.assertNotIn("old_string", json.dumps(activity))
        self.assertEqual(result["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"], 8)

    def test_same_batch_searches_and_reads_share_rows_but_next_batch_is_separate(self):
        """真实并发工具按本轮同类调用合并；不同种类和下一轮同名工具不混在一起。"""

        searches = [{"name": "grep", "args": {"pattern": pattern, "output_mode": "content"}, "id": f"grep-{index}"}
            for index, pattern in enumerate(("orders", "pageSize", "订单"))]
        reads = [{"name": "read_file", "args": {"file_path": "/technical-plan.json", "offset": offset, "limit": 5}, "id": f"read-{index}"}
            for index, offset in enumerate((0, 10, 20))]
        model = ScriptedEditorModel(responses=[
            AIMessage(content="", tool_calls=searches + reads),
            AIMessage(content="", tool_calls=reads),
            tool_response("edit_file", file_path="/technical-plan.json", old_string='"default": 20', new_string='"default": 8'),
            AIMessage(content="修改完成"),
        ])
        activity = []
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model), \
             patch("app.services.technical_planning_output.get_stream_writer", return_value=activity.append):
            result = edit_technical_plan_with_agent(
                {"confirmed_product_plan": {"pages": []}, "planning_adjustment_request": "分页改为8"},
                candidate_plan(), settings=Settings(),
            )
        grouped = {}
        for item in activity:
            if item["text"].startswith(("定位相关内容", "读取相关片段")):
                grouped.setdefault(item["id"], []).append(item)
        self.assertEqual(len(grouped), 3)
        for events in grouped.values():
            self.assertEqual([item["status"] for item in events], ["running", "running", "running", "completed"])
            self.assertIn("（3", events[-1]["text"])
        self.assertEqual(sum(events[-1]["text"] == "定位相关内容完成（3项）" for events in grouped.values()), 1)
        self.assertEqual(sum(events[-1]["text"] == "读取相关片段完成（3段）" for events in grouped.values()), 2)
        tools = [item for item in model.seen_messages[-1] if isinstance(item, ToolMessage)]
        self.assertEqual(sum(item.name == "grep" for item in tools), 3)
        self.assertEqual(sum(item.name == "read_file" for item in tools), 6)
        self.assertEqual(len(model.seen_messages), 4)
        self.assertEqual(result["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"], 8)
        self.assertNotIn("file_path", json.dumps(activity))
        self.assertNotIn("pattern", json.dumps(activity))

    def test_read_batch_failure_is_visible_and_not_reported_as_success(self):
        """批内部分读取失败仍保留失败计数，其他真实工具继续返回原始结果。"""

        calls = [{"name": "read_file", "args": {"file_path": path, "offset": 0, "limit": 1}, "id": f"read-{index}"}
            for index, path in enumerate(("/technical-plan.json", "/missing.json", "/context/product-plan.json"))]
        model = ScriptedEditorModel(responses=[
            AIMessage(content="", tool_calls=calls),
            tool_response("finish_review", summary="读取现有规划与产品事实后确认无需修改。"),
        ])
        activity = []
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model), \
             patch("app.services.technical_planning_output.get_stream_writer", return_value=activity.append):
            edit_technical_plan_with_agent(
                {"confirmed_product_plan": {"pages": []}, "planning_adjustment_request": "保持规划不变。"},
                candidate_plan(), settings=Settings(),
            )
        self.assertEqual(len({item["id"] for item in activity}), 1)
        self.assertEqual([item["status"] for item in activity], ["running", "running", "running", "failed"])
        self.assertEqual(activity[-1]["text"], "读取相关片段未全部成功（3段，1段失败）")
        errors = [item for item in model.seen_messages[-1] if isinstance(item, ToolMessage) and item.status == "error"]
        self.assertEqual(len(errors), 1)


class TechnicalPlanningOutputStreamTests(IsolatedAsyncioTestCase):
    """真实 LangGraph writer、协议编码和执行阻塞共同验证即时到达。"""

    async def test_real_editor_batch_updates_one_activity_in_ag_ui_stream(self):
        """嵌套编辑 Agent 的批量搜索沿原 AG-UI 流更新同一条活动，不增加协议字段。"""

        calls = [{"name": "grep", "args": {"pattern": pattern, "output_mode": "content"}, "id": f"grep-{index}"}
            for index, pattern in enumerate(("orders", "pageSize", "订单"))]
        model = ScriptedEditorModel(responses=[
            AIMessage(content="", tool_calls=calls),
            tool_response("finish_review", summary="当前规划已满足要求，无需修改。"),
        ])
        builder = StateGraph(dict)

        async def generate(state):
            """真实执行受控内存编辑器，让各工具活动穿透父图的 custom stream。"""

            await asyncio.to_thread(edit_technical_plan_with_agent,
                {"confirmed_product_plan": {"pages": []}, "planning_adjustment_request": "保持规划不变。"},
                candidate_plan(), settings=Settings())
            return {**state, "phase": "technical_planning", "status": "completed"}

        builder.add_node("technical_planning_generate", generate)
        builder.add_edge(START, "technical_planning_generate")
        builder.add_edge("technical_planning_generate", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        frames = []
        try:
            with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model):
                async with asyncio.timeout(10):
                    async for raw in build_workflow_ag_ui_stream(graph=graph, payload={
                        "runId": "batch-output-run", "threadId": "batch-output-thread",
                        "messages": [{"role": "user", "content": "复核技术规划"}],
                    }):
                        frames.extend(json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:"))
        finally:
            await close_workflow_checkpointer()
        output = [frame["value"] for frame in frames if frame.get("name") == OUTPUT_EVENT]
        self.assertEqual(len({item["id"] for item in output}), 1)
        self.assertEqual([item["status"] for item in output], ["running", "running", "running", "completed"])
        self.assertEqual(output[-1]["text"], "定位相关内容完成（3项）")
        self.assertTrue(all(item["runId"] == "batch-output-run" and item["threadId"] == "batch-output-thread" for item in output))
        self.assertNotIn("pattern", json.dumps(output))
        self.assertEqual(frames[-1]["type"], "RUN_FINISHED")

    async def test_output_arrives_before_graph_finishes_and_is_not_repeated_in_snapshots(self):
        """先收到正文与工具事件才能放行节点，结束帧仍遵守原完整生命周期。"""
        released = asyncio.Event()
        builder = StateGraph(dict)

        async def begin(state):
            """先发布技术阶段，后续正文归属当前技术规划运行。"""
            return {**state, "phase": "technical_planning_begin", "status": "running"}

        async def generate(state):
            """发送真实分片后阻塞，证明客户端可在节点结束前读取。"""
            _planning_token_callback("实时第一段")
            emit_technical_planning_activity("读取规划片段", call_id="read-1")
            await asyncio.wait_for(released.wait(), timeout=5)
            _planning_token_callback("实时第二段")
            return {**state, "phase": "technical_planning", "status": "completed"}

        builder.add_node("technical_planning_begin", begin)
        builder.add_node("technical_planning_generate", generate)
        builder.add_edge(START, "technical_planning_begin")
        builder.add_edge("technical_planning_begin", "technical_planning_generate")
        builder.add_edge("technical_planning_generate", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        frames = []
        try:
            async with asyncio.timeout(10):
                async for raw in build_workflow_ag_ui_stream(graph=graph, payload={
                    "runId": "stream-output-run", "threadId": "stream-output-thread",
                    "messages": [{"role": "user", "content": "生成技术规划"}],
                }):
                    for line in raw.splitlines():
                        if not line.startswith("data:"):
                            continue
                        frame = json.loads(line[5:])
                        frames.append(frame)
                        if frame.get("name") == OUTPUT_EVENT:
                            self.assertFalse(any(item["type"] == "RUN_FINISHED" for item in frames))
                            if frame["value"].get("id") == "read-1":
                                released.set()
        finally:
            released.set()
            await close_workflow_checkpointer()
        output = [frame["value"] for frame in frames if frame.get("name") == OUTPUT_EVENT]
        self.assertEqual([item["text"] for item in output], ["实时第一段", "读取规划片段", "实时第二段"])
        self.assertTrue(all(item["runId"] == "stream-output-run" and item["threadId"] == "stream-output-thread" for item in output))
        self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
        self.assertNotIn("实时第一段", json.dumps([item for item in frames if item.get("name") == "workflow-run"], ensure_ascii=False))
