from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.agents.main import planner, product_planner


class FakeCompleteMessageModel:
    """模拟 LangChain 在关闭原生流式时返回完整 AIMessage。"""

    def __init__(self, content: str) -> None:
        """保存本次模型调用应返回的完整正文。"""

        self.content = content

    def stream(self, _prompt: str):
        """通过 stream 接口返回单个完整消息。"""

        yield AIMessage(
            content=self.content,
            response_metadata={"finish_reason": "stop"},
        )


class PlanningStreamMessageCompatibilityTests(unittest.TestCase):
    """验证 ProductPlan 与 TechnicalPlan 的完整消息流式回退兼容。"""

    def test_product_planner_accepts_complete_ai_message(self) -> None:
        """ProductPlan 适配器不得丢弃 stream 返回的完整 AIMessage。"""

        expected = '{"app":{"name":"测试应用"}}'
        emitted: list[str] = []
        settings = type("Settings", (), {})()
        with (
            patch.object(
                product_planner.Settings,
                "from_env",
                return_value=settings,
            ),
            patch.object(
                product_planner,
                "create_chat_model",
                return_value=FakeCompleteMessageModel(expected),
            ),
        ):
            result = product_planner._invoke_product_planner(
                {"app_info": {"name": "测试应用"}, "pages": []},
                on_token=emitted.append,
            )

        self.assertEqual(result, expected)
        self.assertEqual(emitted, [expected])

    def test_technical_planner_accepts_complete_ai_message(self) -> None:
        """TechnicalPlan 适配器不得丢弃 stream 返回的完整 AIMessage。"""

        expected = '{"architecture":{"style":"layered"}}'
        emitted: list[str] = []
        settings = type("Settings", (), {})()
        with patch.object(
            planner,
            "create_chat_model",
            return_value=FakeCompleteMessageModel(expected),
        ):
            result = planner._invoke_prompt_with_chat_model(
                "生成技术规划",
                settings=settings,
                on_token=emitted.append,
            )

        self.assertEqual(result, expected)
        self.assertEqual(emitted, [expected])


if __name__ == "__main__":
    unittest.main()
