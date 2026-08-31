from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.main import requirements_analyzer
from app.services.requirement_spec import create_requirement_spec


class RequirementResponseProtocolTests(unittest.TestCase):
    """验证 RequirementSpec 模型响应纠正、流式元数据与普通应用边界。"""

    def test_streaming_fallback_accepts_complete_ai_message(self) -> None:
        """流式接口回退为完整 AIMessage 时不得丢弃模型正文。"""

        class FakeStreamingModel:
            """模拟 LangChain 在关闭原生流式时返回完整消息。"""

            def bind_tools(self, _tools: list[object]) -> "FakeStreamingModel":
                """保持与 LangChain ChatModel bind_tools 接口兼容。"""

                return self

            def stream(self, _prompt: str):
                """返回完整 AIMessage，而不是 AIMessageChunk。"""

                yield AIMessage(
                    content="OK",
                    response_metadata={"finish_reason": "stop"},
                )

        settings = type("Settings", (), {"model_name": "test-model"})()
        with patch.object(
            requirements_analyzer,
            "create_chat_model",
            return_value=FakeStreamingModel(),
        ):
            result = requirements_analyzer._invoke_live_chat_model(
                "创建应用",
                settings=settings,
                on_token=lambda _token: None,
            )

        message = result["messages"][0]
        self.assertEqual(message.content, "OK")
        self.assertEqual(message.response_metadata["finish_reason"], "stop")

    def test_invalid_response_uses_format_correction_on_retry(self) -> None:
        """首次返回普通文本时，第二次调用必须追加协议纠正而非原样重试。"""

        valid_spec = create_requirement_spec("创建库存助手")

        class FakeStreamingModel:
            """先返回非 JSON 澄清文本，再返回完整 RequirementSpec。"""

            def __init__(self) -> None:
                self.prompts: list[str] = []

            def bind_tools(self, _tools: list[object]) -> "FakeStreamingModel":
                """保持与 LangChain ChatModel bind_tools 接口兼容。"""

                return self

            def stream(self, prompt: str):
                """记录两次提示，并让首次响应稳定触发现有解析错误。"""

                self.prompts.append(prompt)
                if len(self.prompts) == 1:
                    yield AIMessageChunk(content="请确认库存助理是否属于业务智能体？")
                    return
                yield AIMessageChunk(
                    content=json.dumps(valid_spec, ensure_ascii=False),
                )

        model = FakeStreamingModel()
        settings = type("Settings", (), {"model_name": "test-model"})()
        with (
            patch.object(
                requirements_analyzer.Settings,
                "from_env",
                return_value=settings,
            ),
            patch.object(
                requirements_analyzer,
                "create_chat_model",
                return_value=model,
            ),
            patch.object(
                requirements_analyzer,
                "_extract_authorization_facts",
                return_value={},
            ),
        ):
            result = requirements_analyzer.analyze_requirements_with_chat_model(
                "管理库存的应用，并且拥有库存助理，可以快速查询库存、提供补货建议",
                on_token=lambda _token: None,
            )

        self.assertEqual(
            result["requirement_spec"]["app_info"]["name"],
            valid_spec["app_info"]["name"],
        )
        self.assertEqual(len(model.prompts), 2)
        self.assertNotIn("FORMAT CORRECTION", model.prompts[0])
        self.assertIn("FORMAT CORRECTION", model.prompts[1])
        self.assertIn("call ask_user", model.prompts[1])
        self.assertIn("complete JSON object", model.prompts[1])

    def test_streaming_response_preserves_completion_metadata(self) -> None:
        """流式消息重组后必须保留结束原因和 token 用量，供脱敏诊断使用。"""

        class FakeStreamingModel:
            """返回携带 Provider 完成元数据的单个流式块。"""

            def bind_tools(self, _tools: list[object]) -> "FakeStreamingModel":
                """保持与 LangChain ChatModel bind_tools 接口兼容。"""

                return self

            def stream(self, _prompt: str):
                """模拟模型因 token 上限结束的流式响应。"""

                yield AIMessageChunk(
                    content='{"app_info":',
                    response_metadata={"finish_reason": "length"},
                    usage_metadata={
                        "input_tokens": 1200,
                        "output_tokens": 8192,
                        "total_tokens": 9392,
                    },
                )

        settings = type("Settings", (), {"model_name": "test-model"})()
        with patch.object(
            requirements_analyzer,
            "create_chat_model",
            return_value=FakeStreamingModel(),
        ):
            result = requirements_analyzer._invoke_live_chat_model(
                "创建库存助手",
                settings=settings,
                on_token=lambda _token: None,
            )

        message = result["messages"][0]
        self.assertEqual(message.response_metadata["finish_reason"], "length")
        self.assertEqual(message.usage_metadata["output_tokens"], 8192)

    def test_diagnostics_do_not_log_business_content(self) -> None:
        """需求响应诊断必须记录解析元数据，但不得输出用户业务正文。"""

        business_content = "这是不得写入日志的库存业务正文"
        message = AIMessage(
            content=business_content,
            response_metadata={"finish_reason": "length"},
            usage_metadata={
                "input_tokens": 1200,
                "output_tokens": 8192,
                "total_tokens": 9392,
            },
        )

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            requirements_analyzer._log_requirement_model_response_diagnostics(
                message,
                business_content,
                {"app_info": {}},
                configured_max_tokens=8192,
            )

        joined = "\n".join(captured.output)
        self.assertIn(f"response_chars={len(business_content)}", joined)
        self.assertIn("parsed_keys=['app_info']", joined)
        self.assertIn("finish_reason=length", joined)
        self.assertIn("output_tokens=8192", joined)
        self.assertNotIn(business_content, joined)

    def test_valid_non_agent_response_keeps_single_normal_call(self) -> None:
        """普通应用首次返回合法 JSON 时不得进入格式纠正，智能体需求保持为空。"""

        valid_spec = create_requirement_spec("创建库存管理系统")

        class FakeStreamingModel:
            """返回普通应用的完整 RequirementSpec。"""

            def __init__(self) -> None:
                self.prompts: list[str] = []

            def bind_tools(self, _tools: list[object]) -> "FakeStreamingModel":
                """保持与 LangChain ChatModel bind_tools 接口兼容。"""

                return self

            def stream(self, prompt: str):
                """记录首次提示并返回合法 JSON。"""

                self.prompts.append(prompt)
                yield AIMessageChunk(
                    content=json.dumps(valid_spec, ensure_ascii=False),
                )

        model = FakeStreamingModel()
        settings = type("Settings", (), {"model_name": "test-model"})()
        with (
            patch.object(
                requirements_analyzer.Settings,
                "from_env",
                return_value=settings,
            ),
            patch.object(
                requirements_analyzer,
                "create_chat_model",
                return_value=model,
            ),
            patch.object(
                requirements_analyzer,
                "_extract_authorization_facts",
                return_value={},
            ),
        ):
            result = requirements_analyzer.analyze_requirements_with_chat_model(
                "创建库存管理系统",
                on_token=lambda _token: None,
            )

        self.assertEqual(len(model.prompts), 1)
        self.assertNotIn("FORMAT CORRECTION", model.prompts[0])
        self.assertEqual(result["requirement_spec"]["agent_requirements"], [])


if __name__ == "__main__":
    unittest.main()
