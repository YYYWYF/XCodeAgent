"""为模型生成文档的保存边界复用异步上报，不拦截人工保存及确认提升。"""

from pathlib import Path
from typing import Any, Callable

from langchain_core.runnables.config import ensure_config

from app.config import Settings
from app.services.inclusion_rate import (
    bind_inclusion_identity,
    capture_inclusion_before,
    report_inclusion_rate,
)
from app.workspace.spec_documents import workspace_root


def write_generated_documents(
    state: dict[str, Any],
    paths: list[Path],
    writer: Callable,
    *args,
    **kwargs,
):
    """保存成功后上报指定 Markdown；写盘错误照常传播，统计准备失败不影响保存。"""
    try:
        # 这些文档生成器均使用主模型；业务会话身份在提交异步发送前固定。
        model = Settings.from_env().model_api_name
        thread_id = str(ensure_config().get("configurable", {}).get("thread_id")
                        or state.get("active_thread_id") or "")
        root = workspace_root(state).resolve()
    except Exception:
        return writer(*args, **kwargs)
    if not thread_id or not model:
        return writer(*args, **kwargs)
    with bind_inclusion_identity(model, thread_id=thread_id):
        snapshots = [(path, capture_inclusion_before(path)) for path in paths]
        result = writer(*args, **kwargs)
        # 仅统计实际落盘差异；确认搬运、重放和内部 JSON 不在调用方的生成范围内。
        for path, before in snapshots:
            report_inclusion_rate(root, path, before)
        return result
