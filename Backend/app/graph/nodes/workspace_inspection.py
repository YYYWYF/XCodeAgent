from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

from app.graph.state import ProjectState
from app.services.code_graph.manager import get_code_graph_manager
from app.services.frontend_scaffold import scaffold_frontend_pages
from app.services.workspace_inspector import inspect_workspace as inspect_workspace_service
from app.services.workspace_inspector import snapshot_hash
from app.workspace.spec_documents import workspace_root
from app.workspace.workspace_snapshot_documents import workspace_snapshot_cache_root


def inspect_workspace(state: ProjectState) -> dict:
    """扫描正式工作流的用户工作区，并把代码图进度写入 Graph custom stream。

    detail_confirmation 完成后首次进入时，根据 ProjectPlan 自动为前端模板工程
    生成菜单（menus.ts）和页面占位目录（src/pages/<key>/index.tsx）。
    """

    _scaffold_frontend_once(state)
    return _scan_workspace(state, node_name="inspect_workspace")


def scan_workspace_code(state: ProjectState) -> dict:
    """扫描快速修改流的用户工作区，复用正式流的索引和降级策略。"""

    return _scan_workspace(state, node_name="scan_workspace_code")


def _scan_workspace(state: ProjectState, *, node_name: str) -> dict[str, Any]:
    """执行一次共享工作区扫描；没有显式 workspaceRoot 时完全跳过 CRG。"""

    root = workspace_root(state)
    has_explicit_workspace = bool(
        str(state.get("workspace") or state.get("workspace_path") or "").strip()
    )
    writer = _stream_writer()

    def on_progress(progress: Any) -> None:
        """把代码图服务的进度转换为稳定的 Graph custom 事件。"""

        detail = progress.as_dict() if hasattr(progress, "as_dict") else {}
        writer(
            {
                "type": "workspace_inspection.progress",
                "node_name": node_name,
                "message": str(detail.get("message") or "正在扫描用户工作区代码…"),
                "detail": detail,
            }
        )

    provider = get_code_graph_manager() if has_explicit_workspace else None
    snapshot, snapshot_path, cache_hit = inspect_workspace_service(
        root,
        cache_root=workspace_snapshot_cache_root(state),
        code_graph_provider=provider,
        on_progress=on_progress,
    )
    return {
        "phase": node_name,
        "status": "completed",
        "message": str(
            ((snapshot.get("code_graph") or {}).get("message"))
            or "代码扫描完成，已建立工作区代码索引。"
        ),
        "workspace_snapshot_summary": _snapshot_summary(snapshot),
        "workspace_snapshot_path": snapshot_path,
        "workspace_snapshot_hash": snapshot_hash(snapshot),
        "workspace_revision": str(snapshot.get("workspace_revision") or ""),
        "timeline": [
            f"{node_name}:cache_hit" if cache_hit else node_name
        ],
    }


def _snapshot_summary(snapshot: dict) -> dict:
    return {
        "schema_version": snapshot.get("schema_version"),
        "workspace_revision": snapshot.get("workspace_revision"),
        "tech_stack": snapshot.get("tech_stack", []),
        "entrypoints": snapshot.get("entrypoints", []),
        "project_roots": snapshot.get("project_roots", []),
        "file_manifest": snapshot.get("file_manifest", {}),
        "code_graph": {
            key: value
            for key, value in (snapshot.get("code_graph") or {}).items()
            if key
            in {
                "provider",
                "providerVersion",
                "status",
                "reason",
                "available",
                "buildType",
                "filesIndexed",
                "symbolsIndexed",
                "relationsIndexed",
                "languages",
                "nodesByKind",
                "relationsByKind",
                "sampleSymbols",
                "warningCount",
                "warnings",
                "message",
                "durationMs",
                "cacheHit",
            }
        },
    }


def _stream_writer() -> Any:
    """获取 LangGraph custom writer，单元测试或非 Graph 调用时使用空实现。"""

    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _event: None


def _scaffold_frontend_once(state: ProjectState) -> None:
    """在 detail_confirmation 完成后的首次 workspace 检查中执行前端脚手架。

    仅在 resume_from != 'inspect_workspace' 时执行（避免每次恢复都重复
    生成脚手架文件）。
    """

    if state.get("resume_from") == "inspect_workspace":
        return
    project_plan = state.get("project_plan")
    if not isinstance(project_plan, dict):
        return
    workspace = str(workspace_root(state) or "").strip()
    if not workspace:
        return
    try:
        result = scaffold_frontend_pages(workspace, project_plan)
        logger.info(
            "frontend_scaffold status=%s pages=%d",
            result.get("status"),
            len(result.get("pages", [])),
        )
    except Exception:
        logger.exception("frontend_scaffold_failed")
