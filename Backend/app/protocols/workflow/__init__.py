"""主工作流 AG-UI 协议的统一公共入口。"""

from app.protocols.workflow.definition import workflow_capabilities
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream

__all__ = [
    "build_workflow_ag_ui_stream",
    "workflow_capabilities",
]
