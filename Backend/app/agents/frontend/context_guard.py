"""DeepAgents 0.6.12 的受控前端局部摘要适配，不修改全局工厂。"""

import json
import re
from types import FunctionType

from deepagents import graph as deep_graph
from deepagents.middleware.summarization import _DeepAgentsSummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage

from app.agents.frontend.retry_guard import FrontendTaskRetryExhausted
from app.services.run_workers import run_worker

CONTEXT_ID = "frontend-build-platform-context"


class FrontendContextGuard(_DeepAgentsSummarizationMiddleware):
    """沿用唯一摘要器的事件、配对、预算和替换机制，提交前验证候选。"""

    def __init__(self, original, scope):
        """保留工厂选择的模型、触发阈值、后端和 Token 计数器。"""
        self.__dict__.update(original.__dict__)
        self.scope = scope
        self.pending_summary = False

    def _validate_summary(self, summary, before_count):
        """拒绝空白、框架占位和 helper 将异常转换成的普通文本。"""
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("empty_summary")
        if summary.startswith(("Error generating summary:", "No previous conversation history.",
                               "Previous conversation was too long to summarize.")):
            raise ValueError("invalid_summary")
        body = re.sub(r"</?summary>|[#*`]+|Here is a summary of the conversation to date:|A condensed summary follows:|\s", "", summary)
        if not body or body.casefold() in {"summary", "摘要", "none", "n/a", "..."}:
            raise ValueError("placeholder_summary")
        self.pending_summary = True
        self.scope.record("frontend_context_compression", summary_length=len(summary),
                          before_message_count=before_count)
        return summary

    def _create_summary(self, messages_to_summarize):
        """摘要请求异常或无效正文仅重试摘要，不发送主模型请求。"""
        while True:
            self.scope.retry.check()
            # 取消判定在异常适配之外，已停止运行不能被误记为摘要失败再重试。
            with run_worker():
                try:
                    return self._validate_summary(super()._create_summary(messages_to_summarize), len(messages_to_summarize))
                except FrontendTaskRetryExhausted:
                    raise
                except Exception as exc:
                    self.scope.retry.fail(("@call", "context_compression_failed"), type(exc).__name__)

    async def _acreate_summary(self, messages_to_summarize):
        """异步摘要与同步共用调用级独立预算，不捕获用户取消。"""
        while True:
            self.scope.retry.check()
            with run_worker():
                try:
                    return self._validate_summary(await super()._acreate_summary(messages_to_summarize), len(messages_to_summarize))
                except FrontendTaskRetryExhausted:
                    raise
                except Exception as exc:
                    self.scope.retry.fail(("@call", "context_compression_failed"), type(exc).__name__)

    def _projection(self, messages):
        """由 Scope 生成最新状态，完整契约仅在初始任务包不再可见时恢复。"""
        scope = self.scope
        task = scope.packets[scope.index] if scope.index < len(scope.packets) else None
        visible_packet = any(message.id == "frontend-build-initial-tasks" for message in messages)
        state = {
            "task_id": task["id"] if task else None, "kind": task["kind"] if task else None,
            "task_started": bool(scope.current), "mode": "task_level" if scope.task_level else "page_steps",
            "step": scope.step if not scope.task_level else None,
            "completed_steps": list(range(1, scope.step)) if not scope.task_level else [],
            "completed_tasks": scope.outcomes, "evidence": scope.evidence,
            "pending_task_ids": [packet["id"] for packet in scope.packets[scope.index:]],
            "readable": sorted(scope.readable()), "writable": scope.writable(),
            "failures": list(scope.retry.failures.values()), "acceptance_findings": scope.checks.results,
        }
        if task and not visible_packet:
            state.update(implementation_contract=task["implementation_contract"], change_scope=task["change_scope"])
        # 按调用 ID 判断正文是否仍可见，不比较文件内容、版本或摘要。
        visible_calls = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)
                         and message.status == "success" and isinstance(message.content, str)
                         and "[Output was truncated" not in message.content
                         and not message.content.startswith("Tool result too large,")}
        docs = {}
        for key in scope.core_docs_read:
            if key not in scope.core_docs_body:
                self.scope.retry.stop("core_context_missing", "已读核心规范正文缓存缺失，无法安全恢复。")
        for (task_id, path), (body, call_id) in scope.core_docs_body.items():
            if call_id not in visible_calls:
                docs[path] = body
        state["core_documents_read"] = [list(key) for key in sorted(scope.core_docs_read)]
        state["core_documents"] = docs if task else {}
        return ("平台状态为权威；不得猜测任务 ID。已启动任务从当前步骤继续，不重新 begin。"
                "平台负责终止。全部任务结束后仅输出唯一顶层 task_results JSON；"
                "每项 task_id、status、summary，失败含 failure_category、failure_reason。\n"
                + json.dumps(state, ensure_ascii=False, separators=(",", ":")))

    def _protected_request(self, request, *, actual=False):
        """摘要前加入状态参与预算；实际请求时再按有效历史刷新完整保护块。"""
        self.scope.retry.check()
        self._recover_documents(request.state.get("messages", []))
        effective = request.messages if actual else self._get_effective_messages(request)
        system = request.system_message
        blocks = [block for block in (system.content_blocks if system else [])
                  if block.get("id") != CONTEXT_ID]
        blocks.append({"type": "text", "text": self._projection(effective), "id": CONTEXT_ID})
        return request.override(system_message=SystemMessage(content=blocks))

    def _recover_documents(self, history):
        """缓存缺失时只从原始合法工具配对中恢复完整规范，禁止重新读取。"""
        missing = self.scope.core_docs_read - self.scope.core_docs_body.keys()
        if not missing:
            return
        from app.agents.frontend.execution_middleware import _core_document_read_error

        task = self.scope.packets[0]["id"] if self.scope.task_level else None
        calls = {}
        for message in history:
            for call in getattr(message, "tool_calls", []):
                calls[call["id"]] = call
            if not isinstance(message, ToolMessage) or message.status != "success":
                continue
            call = calls.get(message.tool_call_id, {})
            args = call.get("args", {})
            if call.get("name") == "begin_frontend_task":
                if args.get("task_id") in {packet["id"] for packet in self.scope.packets}:
                    task = args["task_id"]
            elif call.get("name") == "complete_frontend_task":
                task = None
            elif call.get("name") == "read_file":
                key = (task, args.get("file_path"))
                if key in missing and not _core_document_read_error(message.content, args):
                    self.scope.core_docs_body[key] = (message.content, message.tool_call_id)

    def _ready(self, request):
        """提交替换之前验证完整请求容量和工具配对，超窗时确定性失败。"""
        request = self._protected_request(request, actual=True)
        pending = set()
        for message in request.messages:
            for call in getattr(message, "tool_calls", []):
                pending.add(call["id"])
            if isinstance(message, ToolMessage):
                if message.tool_call_id not in pending:
                    self.scope.retry.stop("context_pairing_invalid", "摘要后的工具消息配对不完整。")
                pending.remove(message.tool_call_id)
        if pending:
            self.scope.retry.stop("context_pairing_invalid", "存在未返回结果的工具调用。")
        tokens = self._count_tokens(request.messages, request.system_message, request.tools)
        limit = self._get_profile_limits() or 200000
        if tokens >= limit:
            self.scope.retry.stop("context_budget_exceeded", "正式契约、核心规范与近期消息无法安全容纳上下文。")
        if self.pending_summary:
            self.scope.record("frontend_context_restored", after_message_count=len(request.messages),
                              estimated_input_tokens=tokens, token_measurement="estimated")
        return request

    def _committed(self):
        """仅有效候选完成替换提交后关闭压缩失败链，其他工具预算保持。"""
        if self.pending_summary:
            self.scope.retry.failures.pop(("@call", "context_compression_failed"), None)
            self.pending_summary = False

    def wrap_model_call(self, request, handler):
        """在同一个摘要组件内恢复状态，并在真正主模型请求前执行门禁。"""
        result = super().wrap_model_call(self._protected_request(request), handler)
        self._committed()
        return result

    async def awrap_model_call(self, request, handler):
        """异步与同步使用相同保护、提交点和独立失败预算。"""
        result = await super().awrap_model_call(self._protected_request(request), handler)
        self._committed()
        return result


class FrontendModelBoundary(AgentMiddleware):
    """在 Memory 和工具规格注入后核对真正的主模型请求。"""

    def __init__(self, guard):
        """复用同一摘要组件的保护状态，不建立另一套摘要机制。"""
        self.guard = guard

    def wrap_model_call(self, request, handler):
        """最后一个同步模型中间件负责完整请求预算与终止检查。"""
        return handler(self.guard._ready(request))

    async def awrap_model_call(self, request, handler):
        """最后一个异步模型中间件使用相同预算与终止门禁。"""
        return await handler(self.guard._ready(request))


def create_guarded_frontend_agent(scope, **kwargs):
    """在官方工厂的局部命名空间替换唯一摘要组件，保持其他 Agent 和全局不变。"""
    def assemble(*args, **options):
        """仅在此图的组装点替换摘要实例，保留其在原中间件链的位置。"""
        options["middleware"] = [FrontendContextGuard(item, scope)
                                 if type(item) is _DeepAgentsSummarizationMiddleware else item
                                 for item in options["middleware"]]
        if sum(isinstance(item, FrontendContextGuard) for item in options["middleware"]) != 1:
            raise RuntimeError("受控前端必须挂载且仅挂载一个摘要保护器")
        guard = next(item for item in options["middleware"] if isinstance(item, FrontendContextGuard))
        options["middleware"].append(FrontendModelBoundary(guard))
        return deep_graph.create_agent(*args, **options)

    # 0.6.12 没有单次工厂的摘要注入参数；复制函数命名空间，不改模块全局或注册表。
    original = deep_graph.create_deep_agent
    local = FunctionType(original.__code__, {**original.__globals__, "create_agent": assemble},
                         original.__name__, original.__defaults__, original.__closure__)
    local.__kwdefaults__ = original.__kwdefaults__
    from app.agents.execution_control import RunCancellationMiddleware

    kwargs["middleware"] = [RunCancellationMiddleware(kwargs.get("model")), *kwargs.get("middleware", [])]
    return local(**kwargs)
