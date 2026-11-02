"""向技术规划编辑模型提供原始文本窗口与确定性的候选语法反馈。"""

from typing import Callable
from deepagents.backends.state import StateBackend
from deepagents.backends.utils import file_data_to_string
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


class TechnicalPlanFileMiddleware(AgentMiddleware):
    """保留原生读取校验，只移除模型编辑时容易误复制的展示行号与分隔空格。"""

    def __init__(self, backend: StateBackend, candidate_feedback: Callable[[], str]) -> None:
        """复用隔离候选后端与语法检查，读取范围继续由后端统一限制。"""

        self.backend = backend
        self.candidate_feedback = candidate_feedback

    def wrap_tool_call(self, request, handler):
        """原生读取成功后返回同一窗口原文，元信息与可复制文本分开呈现。"""

        result = handler(request)
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
