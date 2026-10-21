"""可选 LangSmith 追踪；任何追踪故障均不得改变模型结果。"""

import asyncio
import logging
from typing import Any
from uuid import uuid4

from langsmith import Client, RunTree

from .config import Settings

logger = logging.getLogger(__name__)


class TraceRecorder:
    """仅显式开启时建立客户端，关闭时不产生 LangSmith 网络请求。"""

    def __init__(self, settings: Settings, client: Any = None):
        """接受离线测试客户端，真实客户端仅在启用追踪时创建。"""
        self.settings = settings
        self.client = None
        if settings.langsmith_enabled:
            try:
                self.client = client or Client(api_url=settings.langsmith_endpoint,
                                              api_key=settings.langsmith_api_key.get_secret_value(),
                                              timeout_ms=2000, auto_batch_tracing=False)
            except Exception:
                logger.warning("LangSmith 客户端初始化失败，推理与本地历史继续可用")

    async def start(self, request: dict[str, Any], metadata: dict[str, Any]) -> tuple[str | None, Any]:
        """将实际模型请求关联会话追踪，捕获并隔离网络或 SDK 故障。"""
        if self.client is None:
            return None, None
        run_id = str(uuid4())
        try:
            tree = RunTree(id=run_id, name="model-inference", run_type="llm", inputs=request,
                           extra={"metadata": metadata}, project_name=self.settings.langsmith_project,
                           ls_client=self.client)
            await asyncio.to_thread(tree.post)
            return run_id, tree
        except Exception:
            logger.warning("LangSmith 调用开始追踪失败，推理继续")
            return run_id, None

    async def finish(self, tree: Any, output: dict[str, Any], status: str, error: dict[str, Any] | None) -> None:
        """提交最终或部分输出，并把追踪失败限制在观测层。"""
        if tree is None:
            return
        try:
            tree.end(outputs=output, error=error.get("message") if error else None, metadata={"status": status})
            await asyncio.to_thread(tree.patch)
        except Exception:
            logger.warning("LangSmith 调用结束追踪失败，历史已在本地保存")


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
