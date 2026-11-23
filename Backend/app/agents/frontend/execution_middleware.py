"""串行执行前端工具并在真实调用边界核对任务或步骤授权。"""

from pathlib import Path
import re

from deepagents.middleware.filesystem import DEFAULT_READ_LIMIT
from deepagents.backends.utils import EMPTY_CONTENT_WARNING
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.agents.frontend.file_backend import FILE_ACCESS
from app.agents.frontend.execution_scope import FRONTEND_CORE_DOCS, FrontendTaskExecutionScope, _CONTROL, _OPERATIONS
from app.workspace.virtual_paths import is_host_workspace_virtual_path


_READ_RANGE_HEADER = re.compile(
    r"@@ lines (\d+)-(\d+) of (\d+)(?: \| ([^@\n]+))? @@"
)


def _complete_core_document_read(content: str, args: dict) -> bool:
    """按当前读取状态头确认核心文档从首行到末行完整返回。"""
    if (args.get("offset", 0) != 0 or "[Output was truncated" in content
            or EMPTY_CONTENT_WARNING in content):
        return False
    # 工具最多在状态头前放置两条说明，不能让正文中的同形文本冒充状态头。
    for row in content.split("\n", 2)[:3]:
        match = _READ_RANGE_HEADER.fullmatch(row)
        if match is None:
            continue
        start, end, total = (int(value) for value in match.group(1, 2, 3))
        fields = match.group(4) or ""
        limit = int(args.get("limit", DEFAULT_READ_LIMIT))
        return (start == 1 and end == total and end <= limit
                and "next offset" not in fields and "truncated" not in fields)
    return False


class FrontendExecutionMiddleware(AgentMiddleware):
    """覆盖 Agent 的所有工具入口并隔离步骤切换与普通调用。"""

    def __init__(self, scope: FrontendTaskExecutionScope):
        """绑定本次调用的授权状态。"""
        self.scope = scope

    def _permission(self, request) -> tuple[str, Path | None]:
        """控制工具必须单独调用，避免同一并发批次使用切换后的权限。"""
        name = request.tool_call["name"]
        if name in _CONTROL:
            if self.scope.task_level:
                raise ValueError("API 模块不提供步骤控制工具")
            messages = request.state.get("messages", [])
            calls = getattr(messages[-1], "tool_calls", []) if messages else []
            if len(calls) != 1 or calls[0]["id"] != request.tool_call["id"]:
                raise ValueError("步骤控制工具必须单独调用")
            return "", None
        if name in {"execute", "code_graph_context"}:
            return "", None  # 独立工具本身在执行前作最终裁决。
        args = request.tool_call["args"]
        return self.scope.authorize(name, args.get("path" if name == "grep" else "file_path"))

    def _denied(self, request, reason: str, path: str = "") -> ToolMessage:
        """用工具错误反馈拒绝决定，保留原始工具调用 ID。"""
        name = request.tool_call["name"]
        raw_path = request.tool_call["args"].get("path" if name == "grep" else "file_path")
        if (not path and isinstance(raw_path, str) and len(raw_path) <= 1024
            and raw_path.startswith("/") and not is_host_workspace_virtual_path(self.scope.root, raw_path)):
            path = raw_path
        self.scope.record("frontend_tool_permission", tool_name=name, virtual_path=path,
                          operation=_OPERATIONS.get(name, name), permission_decision="denied",
                          permission_denial_reason=reason)
        return ToolMessage(content=reason, status="error", name=name,
                           tool_call_id=request.tool_call["id"])

    def wrap_tool_call(self, request, handler):
        """串行校验同步工具及其完整执行。"""
        with self.scope.lock:
            path = ""
            try:
                path, physical, version = self._prepare(request)
                token = FILE_ACCESS.set((self.scope, path))
                try:
                    result = handler(request)
                finally:
                    FILE_ACCESS.reset(token)
                return self._finish(request, path, physical, version, result)
            except (ValueError, PermissionError) as exc:
                return self._denied(request, str(exc), path)

    async def awrap_tool_call(self, request, handler):
        """异步工具沿用步骤授权，并等待当前命令结束后再执行下一项。"""
        async with self.scope.async_lock:
            path = ""
            try:
                path, physical, version = self._prepare(request)
                token = FILE_ACCESS.set((self.scope, path))
                try:
                    result = await handler(request)
                finally:
                    FILE_ACCESS.reset(token)
                return self._finish(request, path, physical, version, result)
            except (ValueError, PermissionError) as exc:
                return self._denied(request, str(exc), path)

    def _prepare(self, request):
        """先验证授权，再采集实际读版本。"""
        path, physical = self._permission(request)
        self.scope.mutation_notice = None
        version = self.scope.reads.version(physical) if request.tool_call["name"] == "read_file" else None
        return path, physical, version

    def _finish(self, request, path, physical, version, result):
        """补充权限观测与读写统计，保持读取正文的兼容行为。"""
        name = request.tool_call["name"]
        fields = {"duplicate_read": False}
        if (name == "read_file" and isinstance(result, ToolMessage)
            and result.status == "success"):
            if path in FRONTEND_CORE_DOCS:
                if not _complete_core_document_read(result.content, request.tool_call["args"]):
                    return self._denied(request, "核心文档正文不完整或被截断，请报告任务失败；不得重试扩大读取范围或搜索替代文档", path)
                self.scope.mark_core_doc_read(path)
            if isinstance(result.content, str):
                fields.update(self.scope.reads.observe(
                    path, physical, version, request.tool_call["args"], result.content, self.scope.step,
                ))
        if name not in _CONTROL | {"execute", "code_graph_context"}:
            self.scope.record("frontend_tool_permission", tool_name=name, virtual_path=path,
                              operation=_OPERATIONS.get(name, name), permission_decision="allowed", **fields)
        return self._mutation_feedback(result)

    def _mutation_feedback(self, result):
        """保留原成功消息，用简短文字及可选字段报告无变化与版本回退。"""
        notice = self.scope.mutation_notice
        if not notice or not isinstance(result, ToolMessage) or result.status != "success":
            return result
        metadata = {key: value for key, value in notice.items() if key != "warn_revert"}
        content = result.content
        if notice["no_change"]:
            content += "\n文件内容完全相同，已跳过落盘。"
        if notice["warn_revert"]:
            content += "\n该文件在本步骤回到既有版本，请按确定的方案继续。"
        return result.model_copy(update={"content": content,
            "additional_kwargs": {**result.additional_kwargs, **metadata}})
