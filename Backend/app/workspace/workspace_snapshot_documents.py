from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workflow_artifact_root


def workspace_snapshot_cache_root(state: dict[str, Any]) -> Path:
    return workflow_artifact_root(state) / "cache"


def load_workspace_snapshot_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
