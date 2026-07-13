from __future__ import annotations

from app.graph.state import ProjectState
from app.services.workspace_inspector import inspect_workspace as inspect_workspace_service
from app.services.workspace_inspector import snapshot_hash
from app.workspace.spec_documents import workspace_root
from app.workspace.workspace_snapshot_documents import workspace_snapshot_cache_root


def inspect_workspace(state: ProjectState) -> dict:
    root = workspace_root(state)
    snapshot, snapshot_path, cache_hit = inspect_workspace_service(
        root,
        cache_root=workspace_snapshot_cache_root(state),
    )

    return {
        "phase": "inspect_workspace",
        "status": "completed",
        "workspace_snapshot_summary": _snapshot_summary(snapshot),
        "workspace_snapshot_path": snapshot_path,
        "workspace_snapshot_hash": snapshot_hash(snapshot),
        "workspace_revision": snapshot["workspace_revision"],
        "timeline": ["inspect_workspace:cache_hit" if cache_hit else "inspect_workspace"],
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
            "provider": (snapshot.get("code_graph") or {}).get("provider"),
            "available": bool((snapshot.get("code_graph") or {}).get("available")),
        },
    }
