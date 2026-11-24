"""首增量识别与毫秒加和的确定性测试，不请求真实模型。"""

import unittest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk
from app.observability.model_stream_timing import ModelStreamTiming, has_effective_model_delta


class ModelStreamTimingTests(unittest.TestCase):
    """覆盖 SDK 实际分块结构及零、空和非法证据。"""

    def test_known_delta_shapes(self):
        """文本、Anthropic thinking、OpenAI reasoning summary 和工具参数均有效。"""
        contents = [" ", [{"type": "text", "text": "a"}],
                    [{"type": "thinking", "thinking": "思考", "index": 0}],
                    [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "a"}]}],
                    [{"type": "input_json_delta", "partial_json": "{", "index": 1}]]
        for content in contents:
            with self.subTest(content=content):
                self.assertTrue(has_effective_model_delta("", ChatGenerationChunk(message=AIMessageChunk(content=content))))
                self.assertTrue(has_effective_model_delta(content))
        for fields in ({"tool_call_chunks": [{"name": "lookup", "args": "", "id": "call", "index": 0}]},
                       {"tool_call_chunks": [{"name": None, "args": "{", "id": None, "index": 0}]},
                       {"additional_kwargs": {"reasoning_content": "reason"}},
                       {"additional_kwargs": {"function_call": {"arguments": "{"}}},
                       {"additional_kwargs": {"tool_calls": [{"function": {"name": "lookup"}}]}}):
            self.assertTrue(has_effective_model_delta("", ChatGenerationChunk(message=AIMessageChunk(content="", **fields))))

    def test_control_and_final_messages_are_not_deltas(self):
        """签名、ID、usage、空工具和未知非空对象不会触发首增量。"""
        for content in ("", [], [{}], [{"type": "text", "text": ""}],
                        [{"type": "thinking", "signature": "signed"}],
                        [{"type": "reasoning", "summary": [{"type": "summary_text", "text": ""}]}],
                        [{"type": "tool_use", "id": "id", "input": {}}],
                        [{"type": "unknown", "text": "opaque"}]):
            self.assertFalse(has_effective_model_delta(content))
        chunk = ChatGenerationChunk(message=AIMessageChunk(
            content="", tool_call_chunks=[{"name": None, "args": "", "id": "id", "index": 0}],
            usage_metadata={"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            response_metadata={"finish_reason": "stop"}))
        self.assertFalse(has_effective_model_delta("", chunk))
        self.assertFalse(has_effective_model_delta("complete", ChatGeneration(message=AIMessage(content="complete"))))
        self.assertFalse(has_effective_model_delta("complete", ChatGenerationChunk(
            message=AIMessageChunk(content="complete"), generation_info={"_ttft_synthetic": True})))

    def test_first_only_rounding_and_missing(self):
        """可控纳秒时钟证明两个独立取整口径均保持加和，缺失不填零。"""
        timing = ModelStreamTiming(0)
        self.assertEqual(timing.finish(10_900_000, duration_ms=10), {"ttft_ms": None, "output_duration_ms": None})
        timing.observe("", observed_ns=1_000_000)
        timing.observe("a", observed_ns=2_600_000)
        timing.observe("b", observed_ns=8_000_000)
        self.assertEqual(timing.finish(10_900_000, duration_ms=10), {"ttft_ms": 2, "output_duration_ms": 8})
        self.assertEqual(timing.finish(10_900_000, duration_ms=11, rounded=True), {"ttft_ms": 3, "output_duration_ms": 8})
        zero = ModelStreamTiming(0)
        zero.observe(" ", observed_ns=0)
        self.assertEqual(zero.finish(0, duration_ms=0), {"ttft_ms": 0, "output_duration_ms": 0})


if __name__ == "__main__":
    unittest.main()
