"""技术候选的显式无变更复核结束，不写正式产物或跳过外层校验。"""

from threading import Lock

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class ReviewCompletionInput(BaseModel):
    """要求模型明确提交无需修改的结论，普通回复文字不构成复核完成。"""

    summary: str = Field(min_length=1, max_length=600, description="说明为什么当前候选已满足原始要求、无需修改。")


class TechnicalPlanReview:
    """允许明确结束无变更复核，文件是否修改仍以候选后端为准。"""

    def __init__(self, backend) -> None:
        """保存当前候选后端，复用其编辑与错误状态。"""
        self.backend = backend
        self.completed = False
        self._lock = Lock()

    def completion_tool(self):
        """提供明确的无变更结束动作；完整业务校验与正式确认仍由既有 Graph 执行。"""
        @tool("finish_review", args_schema=ReviewCompletionInput, return_direct=True)
        def finish_review(summary: str) -> str:
            """仅在原候选已满足用户要求、无需修改时说明结论并结束复核；后续仍要全量校验和用户确认。"""
            with self._lock:
                if self.backend.successful_edits or self.backend.last_edit_error:
                    raise ValueError("已有编辑或未解决的工具错误，请完成修改后正常结束，不能声明无变更。")
                if not summary.strip():
                    raise ValueError("无变更复核必须提供核对结论。")
                self.completed = True
            return "已复核当前候选，无需修改。仍将执行完整合同校验，并等待用户确认。"

        return finish_review
