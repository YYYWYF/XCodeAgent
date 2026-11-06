"""在代码任务验收后同步辅助文档，变更独立归属平台而非模型写入。"""

from app.services.backend_implementation_notes import merge_implementation_notes, retry_implementation_document
from app.services.unit_task_manifest import BACKEND_MODULE_PREFIX
from app.workspace.code_changes import capture_workspace_changes


def _without_document_diff(change_set: dict | None) -> dict | None:
    """保留变更归属和路径，不把辅助 Markdown 正文复制进 AG-UI 快照。"""
    if change_set is None:
        return None
    return {**change_set, "files": [{**item, "diff": ""} for item in change_set.get("files", [])]}


def sync_backend_notes(workspace: str, tasks: list[dict], results: list[dict]) -> tuple[list[dict], dict | None]:
    """持久化成功任务说明并捕获 Markdown 差异；任何辅助错误都不改变代码状态。"""
    by_id = {task["id"]: task for task in tasks}
    output = []

    def synchronize() -> None:
        """在平台变更捕获范围内合并各模块文档。"""
        for result in results:
            task = by_id.get(result.get("task_id"), {})
            sanitized = {key: value for key, value in result.items() if key != "implementation_notes"}
            if (str(task.get("unit_id", "")).startswith(BACKEND_MODULE_PREFIX)
                    and result.get("status") in {"completed", "already_satisfied"}):
                document = merge_implementation_notes(workspace, task, result)
                sanitized["implementation_document"] = document
                if document["status"] != "updated":
                    sanitized["agent_note"] = str(sanitized.get("agent_note") or "") + "\n" + document["summary"]
            output.append(sanitized)

    try:
        captured = capture_workspace_changes(
            workspace=workspace, source_tool="backend_implementation_notes", action=synchronize,
            included_roots=("backend/docs/implementation",),
        )
        return output, _without_document_diff(captured.code_change_set)
    except Exception as exc:
        output = []
        for result in results:
            sanitized = {key: value for key, value in result.items() if key != "implementation_notes"}
            if "implementation_notes" in result and result.get("status") in {"completed", "already_satisfied"}:
                sanitized["implementation_document"] = {"status": "unavailable", "summary": f"辅助文档同步失败：{exc}"}
            output.append(sanitized)
        return output, None


def retry_backend_documents(workspace: str, tasks: list[dict]) -> tuple[list[dict], dict | None]:
    """在本轮构建收尾重试已成功模块的未写入说明，包含完全复用的运行。"""
    contracts = sorted({
        task["unit_id"].removeprefix(BACKEND_MODULE_PREFIX) for task in tasks
        if str(task.get("unit_id", "")).startswith(BACKEND_MODULE_PREFIX)
        and task.get("status") in {"completed", "already_satisfied"}
    })
    if not contracts:
        return [], None
    try:
        captured = capture_workspace_changes(
            workspace=workspace, source_tool="backend_implementation_notes",
            action=lambda: [result for contract in contracts if (result := retry_implementation_document(workspace, contract))],
            included_roots=("backend/docs/implementation",),
        )
        return captured.value, _without_document_diff(captured.code_change_set)
    except Exception as exc:
        return [{"status": "unavailable", "summary": f"辅助文档未能合并：{exc}"}], None
