"""只在本地构建 LangSmith 追踪；不创建上传客户端，不执行远程上报。"""

import logging
from typing import Any
from uuid import uuid4

from langsmith import RunTree

from .config import Settings
from .model_observation import safe_error

logger = logging.getLogger(__name__)


class TraceRecorder:
    """每次模型请求独立创建本地 SDK 对象，旧远程开关不影响采集或触发上传。"""

    def __init__(self, settings: Settings):
        """只保留本地配置；不初始化、注入或访问 LangSmith Client。"""
        self.settings = settings

    async def start(self, request: dict[str, Any], metadata: dict[str, Any]) -> tuple[str | None, Any]:
        """由 SDK 在内存生成独立根身份，不读取外层追踪或环境中的上传配置。"""
        try:
            # 身份由 LangSmith SDK 本地生成，不把 AG-UI runId 当作 Span ID。
            tree = RunTree(name="model-inference", run_type="llm", inputs=request,
                           extra={"metadata": {**metadata, "model_started": False,
                                               "ls_provider": self.settings.model_upstream,
                                               "ls_model_name": self.settings.active_model}},
                           project_name=self.settings.langsmith_project,
                           parent_run=None, parent_run_id=None, replicas=[])
        except Exception:
            logger.warning("LangSmith 本地追踪创建失败，推理继续")
            return None, None
        span_id = str(tree.id)
        return span_id, tree

    async def finish(self, tree: Any, output: dict[str, Any], status: str, error: dict[str, Any] | None) -> None:
        """先在内存结束追踪，保存安全错误和状态，供打印读取；绝不调用 post/patch。"""
        if tree is None:
            return
        try:
            execution_error = safe_error(error)
            tree.end(outputs=output, end_time=tree.end_time,
                     error=execution_error.message if execution_error is not None else None,
                     metadata={"status": status,
                               "execution_error": execution_error.model_dump() if execution_error is not None else None})
        except Exception:
            logger.warning("本地追踪结束失败，继续使用本次观测和本地历史")


def mock_analytics(record: dict[str, Any]) -> dict[str, Any]:
    """模拟埋点载荷与回执，保留服务端档位及扩展关联信息，不执行真实落库。"""
    return {
        "mode": "mock", "receipt": {"id": str(uuid4()), "inserted": False},
        "wouldInsert": {"table": "model_output_analysis", "payload": {
            "requestId": record["request_id"], "runId": record["run_id"],
            "sessionId": record["session_id"], "status": record["status"],
            "model": record["model_name"], "durationMs": record["duration_ms"],
            "usage": record["usage"], "outputReference": {"runId": record["run_id"]},
            "analysis": {}, "extensions": record["extensions"],
        }},
    }
