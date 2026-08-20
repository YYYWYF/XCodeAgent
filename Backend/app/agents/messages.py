from __future__ import annotations

import re
from typing import Any


NO_AGENT_TEXT = "Agent completed without a text message."

# 网关（OpenAI 兼容协议）会把模型的 thinking/reasoning 以逐 token 的
# Python dict 列表 repr 字符串形式拼进 content（形如
# [{'thinking': '...', 'type': 'thinking', 'index': 0}]，流式中断时还可能是
# 缺 `]`/缺 `}` 的残缺碎片，且常与正文无换行粘连）。这些是模型内部推理，
# 不属于业务输出（JSON/代码），混入会损坏下游解析：import 行首正则失配
# 导致组件被误判"未 import"、JSON 被 thinking 切断、esbuild 校验失败。
# 与 list 型 content 跳过非 text block 等价，str 型 content 同样需要剥离。
# 完整块：`[{'thinking': '...', 'type': 'thinking', 'index': 0}]`（含半完整缺 `]`）。
# 锚定块尾的 `'type': 'thinking'` 特征而非 `[^{}]*`——真实碎片内容常含花括号
# （如 `{{ marginBottom: 16 }}`、`{ label: '成功' }`），用 `[^{}]*` 会在内容里的
# 第一个花括号处卡死导致块匹配失败、碎片残留。`.*?` 非贪婪 + 锚定
# `'type': 'thinking'` 可跨花括号正确匹配到块尾；思考正文里出现
# `'type': 'thinking'` 字样的概率≈0，不会误停。
_THINKING_FRAGMENT_RE = re.compile(
    r"\[\s*\{.*?'type'\s*:\s*['\"](?:thinking|reasoning)['\"].*?\}\s*\]?",
    re.DOTALL,
)
# 流式中断的残缺碎片（无闭合 `}`/`]`），只删到行尾或下一个换行，避免吞掉正文。
_THINKING_OPEN_TAIL_RE = re.compile(
    r"\[\s*\{['\"](?:thinking|reasoning)['\"]\s*:[^\n\]]*?(?=\n|$)",
)


def strip_thinking_fragments(text: str) -> str:
    """从模型返回的 str content 中剥离 thinking 碎片。

    先剥完整块（含半完整缺 `]` 的块），再剥行尾残缺块；最后清理与正文粘连
    处的括号残留（``}]`` 与 ``import`` 同行时，残留 ``}`` 会让 import 行首
    正则失配）。
    """

    if not text:
        return text
    cleaned = _THINKING_FRAGMENT_RE.sub("", text)
    cleaned = _THINKING_OPEN_TAIL_RE.sub("", cleaned)
    # 粘连残留：行首只剩 ] } , ' " 与空格/Tab（不含换行，避免跨行吞掉空行）
    # + 代码关键字，剥掉残留字符。
    cleaned = re.sub(
        r"^[\[\]\}\{,'\" \t]+(?=import\s|export\s|const\s|function\s|type\s)",
        "",
        cleaned,
        flags=re.MULTILINE,
    )
    return cleaned


def optional_last_agent_text(result: dict[str, Any]) -> str | None:
    """读取最后一条 Agent 文本；结果不含消息时返回空值供流式回退处理。"""

    messages = result.get("messages", [])
    if not messages:
        return None
    content = getattr(messages[-1], "content", "")
    text = _coerce_content_text(content)
    return text or None


def last_agent_text(result: dict[str, Any]) -> str:
    """读取最后一条 Agent 文本，并为无文本结果提供稳定占位说明。"""

    return optional_last_agent_text(result) or NO_AGENT_TEXT


def _coerce_content_text(content: Any) -> str:
    """把模型消息 content 规整为纯文本，正确处理 Anthropic 的 content block 列表。

    content 可能是纯字符串，也可能是 content block 列表（如
    ``[{"type": "text", "text": "..."}]``）。后者若直接 ``str()`` 会得到
    Python repr，破坏其中嵌入的 JSON，导致下游 ``extract_json_object`` 解析失败、
    被派发任务被误判为 omitted。这里按 block 顺序拼接 text 字段，保留原始文本。

    带 thinking/reasoning 的模型（如 glm-5.2）会把思考过程作为
    ``{"type": "thinking", "text": "..."}`` block 返回。这些是模型内部推理，
    不属于业务输出（JSON/代码），混入会损坏下游解析（如 .tsx 夹带 thinking 片段
    导致 esbuild 校验失败、JSON 被 thinking 切断）。因此只取 ``type == "text"``
    的 block，跳过 thinking/reasoning 等非文本 block。
    """

    if isinstance(content, str):
        return strip_thinking_fragments(content)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                # 跳过 thinking/reasoning 等非文本 block，只取正文 text block。
                block_type = block.get("type")
                if block_type is not None and block_type != "text":
                    continue
                block_text = block.get("text")
                if isinstance(block_text, str):
                    parts.append(block_text)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""
