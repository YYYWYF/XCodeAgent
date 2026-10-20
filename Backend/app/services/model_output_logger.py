from __future__ import annotations

import json
import re
import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

# UI 设计稿生成池会并发（默认 3 个 worker）调用模型，各自的 handler 同时向 stdout
# 打印流式 token，会字符级交错成乱码。用一把进程级锁把每次 print 原子化。
_print_lock = threading.Lock()

# GLM 网关会把模型的 thinking 以 Python dict repr 碎片形式逐 token 混进流式
# content（形如 `][{'thinking': '...', 'type': 'thinking', 'index': 0}]`，且一个
# block 常跨多个 token：首个 token 带 `{'thinking':` 特征，中间 token 是纯思考
# 文本，末尾 token 带 `'type': 'thinking'...}]`）。这些是模型内部推理，不属于
# 业务输出；原样打印会刷屏数千行、淹没正文。与
# app.agents.messages.strip_thinking_fragments 对完整文本的处理对应，这里在
# 流式 token 级别做等价过滤。
_THINKING_OPEN_RE = re.compile(r"[\[\],]?\s*\{['\"](?:thinking|reasoning)['\"]\s*:")
_THINKING_CLOSE_RE = re.compile(
    r"['\"]type['\"]\s*:\s*['\"](?:thinking|reasoning)['\"][^\n]*?\}\s*\]?"
)


def _print(*args: Any, **kwargs: Any) -> None:
    with _print_lock:
        print(*args, **kwargs)


class ModelOutputLogHandler(BaseCallbackHandler):
    """Stream model outputs from LangChain model calls to the console."""

    def __init__(self) -> None:
        super().__init__()
        self._streamed_runs: set[UUID] = set()
        # run_id → 该 run 当前是否处于 thinking repr 碎片内（吞 token 直到块尾）。
        self._thinking_runs: set[UUID] = set()
        # run_id → 尚未确定是否 thinking 碎片的暂存 token（短前缀，避免把正常
        # 正文开头的 `[`/`{` 误判成 thinking 而吞掉正文）。
        self._pending_tokens: dict[UUID, str] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: Any,
        **kwargs: Any,
    ) -> None:
        self._start_run(kwargs.get("run_id"))

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        self._start_run(kwargs.get("run_id"))

    def on_llm_new_token(self, token: Any, **kwargs: Any) -> None:
        if not token:
            return
        run_id = kwargs.get("run_id")
        # langchain-core 的 token 类型是 str | list[str | dict]：GLM 网关把
        # thinking 以 content block 列表传入（block 形如
        # {'thinking': '...', 'type': 'thinking', 'index': 0}），正文是 text block。
        # list 形态下按 block 分流：thinking/reasoning block 取其推理文本加
        # [thinking] 前缀打印（与正文区分，不再是 dict repr 刷屏），text block
        # 只打印其文本。
        if isinstance(token, list):
            parts: list[str] = []
            for block in token:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type in ("thinking", "reasoning"):
                        thought = block.get("thinking") or block.get("reasoning")
                        if isinstance(thought, str) and thought:
                            parts.append(f"\n[thinking] {thought}\n")
                        continue
                    if block_type is not None and block_type != "text":
                        continue
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(block, str):
                    parts.append(block)
            printable = "".join(parts)
            if isinstance(run_id, UUID):
                self._streamed_runs.add(run_id)
            if printable:
                _print(printable, end="", flush=True)
            return
        if not isinstance(token, str):
            return
        printable = self._filter_thinking_token(run_id, token)
        if isinstance(run_id, UUID):
            self._streamed_runs.add(run_id)
        if printable:
            _print(printable, end="", flush=True)

    def _filter_thinking_token(self, run_id: Any, token: str) -> str:
        """把流式 token 里的 thinking repr 碎片转成 `[thinking] 文本` 打印，正文透传。

        三种状态：已确认在 thinking 块内（打印推理文本、剥掉块尾结构垃圾）、
        疑似块开头（暂存前缀）、正常正文（直接透传）。GLM 的 thinking 块常以
        `][{'thinking': '` 开头、以 `', 'type': 'thinking', 'index': 0}]` 结尾，
        中间跨多个纯文本 token——中间的 token 本身就是推理内容，直接打印即可，
        只需剥掉开头的 `][{'thinking': '` 与结尾的 `', 'type': ...}]` 结构。
        """

        key = run_id if isinstance(run_id, UUID) else None
        if key is not None and key in self._thinking_runs:
            # 已确认在块内：token 是推理文本（可能带块尾结构）。剥掉块尾后打印
            # 剩余文本；块尾出现则退出 thinking 状态并换行，与后续正文分隔。
            if _THINKING_CLOSE_RE.search(token):
                self._thinking_runs.discard(key)
                text = _THINKING_CLOSE_RE.sub("", token)
                # 块尾前常带 repr 的引号/逗号（`...文本', `），一并剥掉。
                text = re.sub(r"['\",\s]+$", "", text)
                return (text + "\n") if text else "\n"
            return token
        # 暂存前缀 + 当前 token 拼接判断：能判出 thinking 开头则进入 thinking 态。
        pending = (self._pending_tokens.get(key, "") if key else "") + token
        open_match = _THINKING_OPEN_RE.search(pending)
        if open_match:
            if key is not None:
                self._thinking_runs.add(key)
                self._pending_tokens.pop(key, None)
            # 块开头结构（`][{'thinking': '`）之前的部分属于正文，照常打印；
            # 之后的部分是推理文本开头，加 [thinking] 前缀。
            head = pending[: open_match.start()]
            # 块开头前的结构性括号残留（] [ , 空白）不属于正文。
            head = re.sub(r"[\[\],\s]+$", "", head)
            thought = pending[open_match.end() :]
            prefix = ("\n[thinking] ") if thought else ""
            # 短 thinking 一块发完：块尾在同一 token 里，剥掉块尾并退出状态。
            if key is not None and _THINKING_CLOSE_RE.search(thought):
                self._thinking_runs.discard(key)
                thought = _THINKING_CLOSE_RE.sub("", thought)
                thought = re.sub(r"['\",\s]+$", "", thought)
                return head + ("\n[thinking] " + thought + "\n" if thought else "")
            return head + prefix + thought
        # 前缀本身可能是 thinking 开头的前半（如 token 边界把 `{'thinking':`
        # 切开）：暂存、暂不打印，下一个 token 再判定。前缀很短（< 20 字符），
        # 不会长时间卡住正文。
        if key is not None and re.search(r"[\[\{,]\s*$", pending) and len(pending) < 20:
            self._pending_tokens[key] = pending
            return ""
        # 确认为正文：连同之前暂存的前缀一起输出。
        if key is not None:
            self._pending_tokens.pop(key, None)
        return pending if key is not None and pending != token else token

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        streamed = isinstance(run_id, UUID) and run_id in self._streamed_runs
        if streamed:
            _print(flush=True)

        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                content = getattr(message, "content", None)
                tool_calls = getattr(message, "tool_calls", None)
                if content is None:
                    content = getattr(generation, "text", "")
                if streamed:
                    if tool_calls:
                        _print("[model-tool-calls]")
                        _print(_stringify_content(tool_calls), flush=True)
                else:
                    log_model_output(content=content, tool_calls=tool_calls)

        if isinstance(run_id, UUID):
            self._streamed_runs.discard(run_id)
            self._thinking_runs.discard(run_id)
            self._pending_tokens.pop(run_id, None)

    def _start_run(self, run_id: Any) -> None:
        if isinstance(run_id, UUID):
            self._streamed_runs.discard(run_id)
        _print("[model-output]", flush=True)


def log_model_output(
    *,
    content: Any,
    tool_calls: Any = None,
    prefix: str = "[model-output]",
) -> None:
    _print(prefix, flush=True)
    text = _stringify_content(content)
    if text:
        _print(text, flush=True)
    else:
        _print("(empty content)", flush=True)
    if tool_calls:
        _print("[model-tool-calls]", flush=True)
        _print(_stringify_content(tool_calls), flush=True)


def _stringify_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)
