"""向技术规划编辑模型提供原始文本窗口与确定性的候选语法反馈。"""

from dataclasses import dataclass, field
from threading import Lock
from typing import Callable
from uuid import uuid4
from deepagents.backends.state import StateBackend
from deepagents.backends.utils import file_data_to_string
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from app.services.technical_planning_output import emit_technical_planning_activity


@dataclass
class _ToolActivityGroup:
    """同一模型回复内同类只读工具共用一条活动，保留实际完成与失败计数。"""

    label: str
    unit: str
    pending: set[str]
    total: int
    identity: str = field(default_factory=lambda: uuid4().hex)
    failed: int = 0


class TechnicalPlanFileMiddleware(AgentMiddleware):
    """保留原生读取校验，只移除模型编辑时容易误复制的展示行号与分隔空格。"""

    def __init__(self, backend: StateBackend, candidate_feedback: Callable[[], str]) -> None:
        """复用隔离候选后端与语法检查，读取范围继续由后端统一限制。"""

        self.backend = backend
        self.candidate_feedback = candidate_feedback
        self._activity_batch_id: str | None = None
        self._activity_groups: dict[str, _ToolActivityGroup] = {}
        self._activity_lock = Lock()

    def _activity_group(self, request) -> _ToolActivityGroup | None:
        """在原工具入口按当前模型消息分组，避免为展示新增 Graph 节点或消耗执行轮次。"""

        message = request.state["messages"][-1]
        if not isinstance(message, AIMessage) or not message.id:
            return None
        with self._activity_lock:
            if self._activity_batch_id != message.id:
                self._activity_batch_id = message.id
                self._activity_groups = {}
                for name, label, unit in (("grep", "定位相关内容", "项"), ("read_file", "读取相关片段", "段")):
                    calls = [call for call in message.tool_calls if call["name"] == name]
                    identities = [call.get("id") for call in calls]
                    # 缺少可关联身份时维持独立活动，不能把不同调用误算成已完成的同一项。
                    if len(calls) < 2 or any(not identity for identity in identities) or len(set(identities)) != len(calls):
                        continue
                    group = _ToolActivityGroup(label=label, unit=unit, pending=set(identities), total=len(calls))
                    for identity in identities:
                        self._activity_groups[identity] = group
                    emit_technical_planning_activity(f"{label}（{group.total}{unit}）", call_id=group.identity)
            return self._activity_groups.get(request.tool_call.get("id"))

    def _complete_activity_group(self, group: _ToolActivityGroup, call_id: str, *, failed: bool) -> None:
        """并发工具按真实结果原位更新同一活动，全部返回前不提前显示成功。"""

        with self._activity_lock:
            if call_id not in group.pending:
                return
            group.pending.remove(call_id)
            group.failed += int(failed)
            failures = f"，{group.failed}{group.unit}失败" if group.failed else ""
            if group.pending:
                text = f"{group.label}（已完成{group.total - len(group.pending)}/{group.total}{group.unit}{failures}）"
            else:
                result = "未全部成功" if group.failed else "完成"
                text = f"{group.label}{result}（{group.total}{group.unit}{failures}）"
            emit_technical_planning_activity(text, call_id=group.identity,
                status="running" if group.pending else "failed" if group.failed else "completed")

    def wrap_tool_call(self, request, handler):
        """原生读取成功后返回同一窗口原文，元信息与可复制文本分开呈现。"""

        name = request.tool_call["name"]
        label = {"grep": "定位规划内容", "read_file": "读取规划片段", "edit_file": "应用局部修改"}.get(name)
        call_id = request.tool_call.get("id")
        group = self._activity_group(request)
        identity = None
        if label and group is None:
            identity = emit_technical_planning_activity(label, call_id=call_id)
        try:
            result = handler(request)
        except Exception:
            if group:
                self._complete_activity_group(group, call_id, failed=True)
            elif identity:
                emit_technical_planning_activity(f"{label}失败", call_id=identity, status="failed")
            raise
        failed = isinstance(result, ToolMessage) and result.status == "error"
        if group:
            self._complete_activity_group(group, call_id, failed=failed)
        elif identity:
            emit_technical_planning_activity(
                f"{label}未成功，等待模型修正" if failed else f"{label}完成",
                call_id=identity, status="failed" if failed else "completed",
            )
        if (
            not isinstance(result, ToolMessage)
            or result.status == "error"
        ):
            return result
        if request.tool_call["name"] == "edit_file":
            return result.model_copy(update={"content": f"{result.content}\n{self.candidate_feedback()}"})
        if request.tool_call["name"] != "read_file":
            return result
        arguments = request.tool_call["args"]
        offset = arguments.get("offset", 0)
        raw = self.backend.read(
            arguments["file_path"], offset=offset, limit=arguments.get("limit", 120),
        )
        if raw.error or raw.file_data is None:
            return result
        content = file_data_to_string(raw.file_data)
        # 不解析库的行号格式，也不改动原文缩进；长行同样保持完整，供 edit_file 精确匹配。
        header = f"offset={max(offset, 0)}; returned_lines={len(content.splitlines())}"
        return result.model_copy(update={
            "content": f"{header}\n原始文本（代码块内可直接复制）：\n```\n{content}\n```\n{self.candidate_feedback()}",
        })
