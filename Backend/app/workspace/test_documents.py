from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workspace_root


def test_report_json_path(state: dict[str, Any]) -> Path:
    return workspace_root(state) / "reports" / "test-report.json"


def write_test_report_json(state: dict[str, Any], test_report: dict[str, Any]) -> str:
    path = test_report_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(test_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_test_report_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
