"""只在本地构建 LangSmith 追踪；不创建上传客户端，不执行远程上报。"""

import logging
from collections import OrderedDict
from threading import Lock
from typing import Any
from uuid import UUID

from langsmith import RunTree

from .config import Settings
from .model_observation import safe_error

logger = logging.getLogger(__name__)
_MAX_LOCAL_ROOTS = 4096


class TraceRecorder:
    """在转发进程内按业务 Run 关联独立的模型 Span。"""

    def __init__(self, settings: Settings):
        """保存本地配置和有界的业务根缓存。"""
        self.settings = settings
        self._roots: OrderedDict[UUID, RunTree] = OrderedDict()
        self._root_lock = Lock()

    def _business_root(self, run_id: Any) -> RunTree | None:
        """使用请求中的标准 UUID 获取业务根，并保留首次接收的根时间。"""
        if not isinstance(run_id, str):
            return None
        try:
            root_id = UUID(run_id)
        except ValueError:
            return None
        if str(root_id) != run_id:
            return None
        with self._root_lock:
            root = self._roots.get(root_id)
            if root is None:
                root = RunTree(id=root_id, name="business-run", run_type="chain",
                               project_name=self.settings.langsmith_project, replicas=[])
                self._roots[root_id] = root
                if len(self._roots) > _MAX_LOCAL_ROOTS:
                    self._roots.popitem(last=False)
            else:
                self._roots.move_to_end(root_id)
            return root

    async def start(self, request: dict[str, Any], metadata: dict[str, Any]) -> tuple[str | None, Any]:
        """创建独立模型 Span，有业务根时继承其 Trace ID。"""
        try:
            parent = self._business_root(metadata.get("run_id"))
            # 通过构造参数继承根身份，避免将每次模型调用累积到缓存根的 child_runs。
            tree = RunTree(name="model-inference", run_type="llm", inputs=request,
                           extra={"metadata": {**metadata, "model_started": False,
                                               "ls_provider": self.settings.model_upstream,
                                               "ls_model_name": self.settings.active_model}},
                           project_name=self.settings.langsmith_project,
                           parent_run=parent, replicas=[])
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
            logger.warning("本地追踪结束失败，继续使用本次观测")
