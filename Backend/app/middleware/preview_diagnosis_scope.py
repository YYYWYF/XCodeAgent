"""预览诊断只查询本轮证据，避免从自身修复记录中循环寻找根因。"""

from collections.abc import Awaitable, Callable
from typing import Any
import hashlib
import json

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.runnables.config import ensure_config
from langgraph.types import Command


def _reject_unrelated_runtime(request: ToolCallRequest) -> ToolMessage | None:
    """只在预览诊断作用域限制平台运行文件，不影响其他 RepairPlanner 调用。"""
    scope = ensure_config().get("metadata", {}).get("preview_diagnosis")
    if not isinstance(scope, dict):
        return None
    call = request.tool_call
    args = call.get("args") or {}
    path = str(args.get("file_path") or args.get("path") or "/").replace("\\", "/")
    normalized = "/" + path.lstrip("/")
    runtime_path = normalized.startswith("/.devagentstudio/runtime") or normalized.rstrip("/") == "/.devagentstudio"
    broad_search = call["name"] in {"grep", "glob"} and normalized == "/"
    if (runtime_path and normalized not in scope.get("logPaths", [])) or broad_search:
        return ToolMessage(
            content="本轮启动诊断请优先检查 failure.diagnostics、candidateFiles 和 sourceContext。"
                    "需要搜索时指定相关工程目录；仅可读取 failure.logPaths 中的当前日志，"
                    "不要读取旧修复记录或搜索平台错误文案的实现。证据不足时明确返回缺少的信息。",
            name=call["name"], tool_call_id=call["id"], status="error",
        )
    return None


def _observe_evidence(request: ToolCallRequest, result: ToolMessage | Command[Any]) -> ToolMessage | Command[Any]:
    """相同查询连续获得相同结果时明确停止，文件或查询变化仍可正常继续。"""
    scope = ensure_config().get("metadata", {}).get("preview_diagnosis")
    if not isinstance(scope, dict):
        return result
    messages = [result] if isinstance(result, ToolMessage) else (result.update.get("messages", []) if isinstance(result.update, dict) else [])
    contents = [message.content for message in messages if isinstance(message, ToolMessage)]
    if not contents:
        return result
    # 记录仅属于本次调用，不落盘、不跨会话共享，也不保存原始工具正文。
    observations = scope.get("observations")
    if not isinstance(observations, dict):
        return result
    query = hashlib.sha256(json.dumps({"name": request.tool_call["name"], "args": request.tool_call["args"]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    digest = hashlib.sha256(str(contents).encode()).hexdigest()
    if observations.get(query) == digest:
        observations["stalled"] = True
    observations[query] = digest
    return result


class PreviewDiagnosisScopeMiddleware(AgentMiddleware):
    """对同步和异步工具使用同一份本轮证据范围。"""

    def _require_progress(self) -> None:
        """在模型边界结束无新证据的循环，避免工具层把异常转成可继续重试的消息。"""
        scope = ensure_config().get("metadata", {}).get("preview_diagnosis")
        if isinstance(scope, dict) and (scope.get("observations") or {}).get("stalled"):
            raise RuntimeError("预览诊断重复执行相同查询且未获得新证据，已停止本轮诊断；请补充新的错误信息或检查日志后重新诊断。")

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步模型调用前校验是否仍有诊断进展。"""
        self._require_progress()
        return handler(request)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型调用保留相同的进展边界。"""
        self._require_progress()
        return await handler(request)

    def wrap_tool_call(
        self, request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """阻止同步诊断读取自身或历史运行记录。"""
        rejected = _reject_unrelated_runtime(request)
        return _observe_evidence(request, rejected if rejected is not None else handler(request))

    async def awrap_tool_call(
        self, request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """保持异步调用与同步调用的诊断范围一致。"""
        rejected = _reject_unrelated_runtime(request)
        return _observe_evidence(request, rejected if rejected is not None else await handler(request))
