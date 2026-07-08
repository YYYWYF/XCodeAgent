from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class ModelOutputLogHandler(BaseCallbackHandler):
    """Stream model outputs from LangChain model calls to the console."""

    def __init__(self) -> None:
        super().__init__()
        self._streamed_runs: set[UUID] = set()

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

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if not token:
            return
        run_id = kwargs.get("run_id")
        if isinstance(run_id, UUID):
            self._streamed_runs.add(run_id)
        print(token, end="", flush=True)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        streamed = isinstance(run_id, UUID) and run_id in self._streamed_runs
        if streamed:
            print(flush=True)

        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                content = getattr(message, "content", None)
                tool_calls = getattr(message, "tool_calls", None)
                if content is None:
                    content = getattr(generation, "text", "")
                if streamed:
                    if tool_calls:
                        print("[model-tool-calls]")
                        print(_stringify_content(tool_calls), flush=True)
                else:
                    log_model_output(content=content, tool_calls=tool_calls)

        if isinstance(run_id, UUID):
            self._streamed_runs.discard(run_id)

    def _start_run(self, run_id: Any) -> None:
        if isinstance(run_id, UUID):
            self._streamed_runs.discard(run_id)
        print("[model-output]", flush=True)


def log_model_output(
    *,
    content: Any,
    tool_calls: Any = None,
    prefix: str = "[model-output]",
) -> None:
    print(prefix, flush=True)
    text = _stringify_content(content)
    if text:
        print(text, flush=True)
    else:
        print("(empty content)", flush=True)
    if tool_calls:
        print("[model-tool-calls]", flush=True)
        print(_stringify_content(tool_calls), flush=True)


def _stringify_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)
