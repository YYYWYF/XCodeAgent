"""验证技术规划公开输出在执行结束前到达真实 AG-UI 流。"""

import asyncio
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessageChunk
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


class TechnicalPlanningOutputStreamTests(IsolatedAsyncioTestCase):
    """真实 LangGraph writer、协议编码和执行阻塞共同验证即时到达。"""

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
