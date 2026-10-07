"""OpenCode Go 的可选请求元数据，使用 SDK 的 extra_headers，不修改共享模型状态。"""

from typing import Any
from uuid import uuid4

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import ensure_config
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr


class _OpenCodeGoHeaders:
    """在调用边界读取会话配置，避免缓存模型把不同会话的请求头混在一起。"""

    def _request_kwargs(self, config: RunnableConfig | None, kwargs: dict[str, Any]) -> dict[str, Any]:
        """合并当前调用的请求头；独立调用在模型实例生命周期内使用稳定 UUID。"""

        active_config = ensure_config(config)
        session_id = active_config.get("configurable", {}).get("thread_id")
        # Graph 配置会传递到嵌套 runnable；显式 config 优先，不能用 run_id 替代会话 ID。
        headers = dict(kwargs.get("extra_headers") or {})
        headers.update({
            "User-Agent": "devagentstudio/0.1",
            "x-opencode-session": str(session_id or self._standalone_session_id),
        })
        return {**kwargs, "extra_headers": headers}

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        """为同步非流式调用附加当前会话请求头。"""

        return super().invoke(input, config=config, **self._request_kwargs(config, kwargs))

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        """为异步非流式调用附加当前会话请求头。"""

        return await super().ainvoke(input, config=config, **self._request_kwargs(config, kwargs))

    def stream(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        """为同步流式调用附加当前会话请求头，保持原有流式生命周期。"""

        yield from super().stream(input, config=config, **self._request_kwargs(config, kwargs))

    async def astream(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        """为异步流式调用附加当前会话请求头，保持每次并发调用配置独立。"""

        async for chunk in super().astream(input, config=config, **self._request_kwargs(config, kwargs)):
            yield chunk


class OpenCodeGoChatOpenAI(_OpenCodeGoHeaders, ChatOpenAI):
    """支持 OpenCode Go 会话请求头的 OpenAI 兼容模型。"""

    _standalone_session_id: str = PrivateAttr(default_factory=lambda: str(uuid4()))


class OpenCodeGoChatAnthropic(_OpenCodeGoHeaders, ChatAnthropic):
    """支持 OpenCode Go 会话请求头的 Anthropic 协议模型。"""

    _standalone_session_id: str = PrivateAttr(default_factory=lambda: str(uuid4()))
