from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from uuid import uuid4

from app.services.model_output_logger import ModelOutputLogHandler


class ModelOutputLogHandlerTests(unittest.TestCase):
    def _stream(self, tokens: list[object]) -> str:
        handler = ModelOutputLogHandler()
        run_id = uuid4()
        buf = io.StringIO()
        with redirect_stdout(buf):
            for token in tokens:
                handler.on_llm_new_token(token, run_id=run_id)
        return buf.getvalue()

    def test_thinking_repr_tokens_printed_with_prefix(self) -> None:
        """thinking repr 碎片以 [thinking] 前缀打印推理文本，不再出现 dict repr 结构。

        回归：GLM 网关逐 token 泄漏的 thinking 曾以
        `[{'thinking': '...', 'type': 'thinking', 'index': 0}]` 碎片原样刷屏；
        一度改为整体丢弃，但用户需要观察推理过程。现在剥掉 repr 结构、以
        [thinking] 前缀打印推理文本，与正文明确区分。
        """

        out = self._stream(
            [
                "][{'thinking': ', so it should find the attributes regardless', 'type': 'thinking', 'index': 0}]",
                "[{'thinking': ' of runtime forwarding. Good', 'type': 'thinking', 'index': 0}]",
                "import React from 'react';\n",
                "export default Page;\n",
            ]
        )

        self.assertIn("[thinking]", out)
        self.assertIn("so it should find the attributes regardless", out)
        self.assertIn("of runtime forwarding. Good", out)
        self.assertNotIn("'type'", out, "不应出现 dict repr 结构")
        self.assertNotIn("'index'", out, "不应出现 dict repr 结构")
        self.assertIn("import React from 'react';", out)
        self.assertIn("export default Page;", out)

    def test_thinking_block_split_across_tokens_printed_as_text(self) -> None:
        """thinking repr 跨 token 切分时，中间的纯思考文本以推理内容打印。"""

        out = self._stream(
            [
                "const x = 1;",
                "[{'thinking': 'split",
                " block middle",
                " text', 'type': 'thinking', 'index': 0}]",
                "const y = 2;",
            ]
        )

        self.assertIn("[thinking]", out)
        self.assertIn("split", out)
        self.assertIn("block middle", out)
        self.assertNotIn("'type'", out)
        self.assertIn("const x = 1;", out)
        self.assertIn("const y = 2;", out)

    def test_normal_content_with_brackets_not_swallowed(self) -> None:
        """正文里的括号/数组字面量不得被误判为 thinking 碎片。"""

        out = self._stream(
            [
                "const list = [1, 2, 3];",
                "const obj = { label: '成功' };",
                "arr.map((m) => m.itemId)",
            ]
        )

        self.assertIn("[1, 2, 3]", out)
        self.assertIn("{ label: '成功' }", out)
        self.assertIn("m.itemId", out)
        self.assertNotIn("[thinking]", out)

    def test_reasoning_type_tokens_printed_with_prefix(self) -> None:
        """reasoning 变体（部分网关字段名）同样以 [thinking] 前缀打印。"""

        out = self._stream(
            [
                "[{'reasoning': 'some thought', 'type': 'reasoning', 'index': 0}]",
                "real content",
            ]
        )

        self.assertIn("[thinking]", out)
        self.assertIn("some thought", out)
        self.assertNotIn("'type'", out)
        self.assertIn("real content", out)

    def test_list_content_block_tokens_printed_by_block_type(self) -> None:
        """list 形态的 content block token：thinking block 加前缀打印，text block 打印文本。

        回归：langchain-core 的 on_llm_new_token token 类型是
        str | list[str | dict]，GLM 网关把 thinking 以 block 列表传入。旧实现
        假设 token 恒为 str，对 list 做 str 拼接抛 TypeError（每个 thinking
        chunk 打一条 callback 错误）；且直接 print list 会把 dict repr 刷满
        终端——这正是用户看到的 thinking 泄漏。
        """

        out = self._stream(
            [
                [{"thinking": "internal reasoning", "type": "thinking", "index": 0}],
                [
                    {"type": "text", "text": "import React"},
                    {"thinking": "more reasoning", "type": "thinking", "index": 0},
                    {"type": "text", "text": " from 'react';"},
                ],
            ]
        )

        self.assertIn("[thinking] internal reasoning", out)
        self.assertIn("[thinking] more reasoning", out)
        self.assertNotIn("'type'", out, "不应出现 dict repr")
        # 两段 text block 被中间的 thinking block 分隔，分别断言。
        self.assertIn("import React", out)
        self.assertIn(" from 'react';", out)


if __name__ == "__main__":
    unittest.main()
