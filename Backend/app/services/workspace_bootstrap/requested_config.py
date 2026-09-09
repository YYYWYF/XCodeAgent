"""把已确认应用事实确定性映射为 Template Engine RequestedConfig。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.workspace_bootstrap.models import TemplateConfigError
from app.services.template_reconcile.desired import (
    TemplateCapabilityError,
    requested_config_from_technical_plan,
)


def compile_template_requested_config(workspace_root: str | Path) -> dict[str, Any]:
    """读取 confirmed TechnicalPlan，生成不做依赖解析的当前 Engine 请求。"""

    root = Path(workspace_root).expanduser().resolve()
    technical_plan = _load_object(
        root / ".xcodeagent/plans/technical-plan.json", "technical-plan.json"
    )
    _validate_technical_plan(technical_plan)
    try:
        return requested_config_from_technical_plan(technical_plan)
    except TemplateCapabilityError as exc:
        raise TemplateConfigError(str(exc)) from exc


def _load_object(path: Path, label: str) -> dict[str, Any]:
    """读取一个正式 JSON 对象，避免把损坏文件传给 Engine。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateConfigError(f"缺少或无法读取 {label}。") from exc
    if not isinstance(value, dict):
        raise TemplateConfigError(f"{label} 必须是 JSON 对象。")
    return value


def _validate_technical_plan(plan: dict[str, Any]) -> None:
    """确认 TechnicalPlan 是当前已确认的正式产物。"""

    if plan.get("artifact_type") != "technical-plan" or plan.get("confirmation_status") != "confirmed":
        raise TemplateConfigError("Template 请求必须使用已确认的 TechnicalPlan。")
