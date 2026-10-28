"""内部消息辅助入口仍使用同一个转发模型，不残留原生模型出口。"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from app.config import Settings
from app.services.llm_client import create_model_provider, _openai_messages, _openai_tool


class ModelProviderTests(unittest.IsolatedAsyncioTestCase):
    """工具格式和内部返回结构不变，只有底层连接改为转发。"""

    def test_tool_and_message_conversion(self):
        """保留工具 JSON 参数及工具结果身份。"""
        tool = _openai_tool({"name": "read", "input_schema": {"type": "object"}})
        self.assertEqual(tool["function"]["name"], "read")
        messages = _openai_messages([
            {"role": "assistant", "tool_calls": [{"id": "c1", "name": "read", "input": {"path": "README.md"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ])
        self.assertEqual(json.loads(messages[0]["tool_calls"][0]["function"]["arguments"]), {"path": "README.md"})
        self.assertEqual(messages[1]["tool_call_id"], "c1")

    async def test_auxiliary_provider_calls_factory_and_preserves_result(self):
        """旧辅助接口经统一工厂，保留正文与工具结果合同。"""
        with patch("app.services.llm_client.create_chat_model") as factory:
            factory.return_value.ainvoke = AsyncMock(return_value=AIMessage(
                content="answer", tool_calls=[{"id": "c1", "name": "read", "args": {"path": "a"}}]))
            provider = create_model_provider(Settings())
            response = await provider.complete(model="ignored-logical-model", messages=[{"role": "user", "content": "hi"}],
                                               system="system", max_tokens=100, temperature=0.1)
        self.assertEqual(response.text, "answer")
        self.assertEqual(response.tool_calls[0].input, {"path": "a"})
        self.assertEqual(factory.return_value.ainvoke.call_args.kwargs["max_tokens"], 100)
