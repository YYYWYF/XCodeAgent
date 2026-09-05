"""把已确认应用事实确定性映射为 Template Engine RequestedConfig。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.workspace_bootstrap.models import TemplateConfigError


def compile_template_requested_config(workspace_root: str | Path) -> dict[str, Any]:
    """读取 application 与 confirmed TechnicalPlan，生成不做依赖解析的请求。"""

    root = Path(workspace_root).expanduser().resolve()
    application = _load_object(root / ".xcodeagent/application.json", "application.json")
    technical_plan = _load_object(
        root / ".xcodeagent/plans/technical-plan.json", "technical-plan.json"
    )
    _validate_technical_plan(technical_plan)
    auth = _object_field(application, "auth", "application.json")
    authorization = _object_field(application, "authorization", "application.json")
    login_enabled = _bool_field(auth, "enable", "application.json.auth")
    authorization_enabled = _bool_field(
        authorization, "enabled", "application.json.authorization"
    )
    if authorization_enabled and not login_enabled:
        raise TemplateConfigError("启用 authorization 时 application.auth.enable 必须为 true。")
    manifest = _object_field(technical_plan, "authorization_manifest", "TechnicalPlan")
    if _bool_field(manifest, "enabled", "TechnicalPlan.authorization_manifest") != authorization_enabled:
        raise TemplateConfigError("application 与 TechnicalPlan 的 authorization 状态不一致。")
    return {
        "capabilities": {
            "login": {"enabled": login_enabled, "config": {}},
            "authorization": {"enabled": authorization_enabled, "config": {}},
        }
    }


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


def _object_field(value: dict[str, Any], name: str, label: str) -> dict[str, Any]:
    """读取对象字段并统一报出当前契约错误。"""

    item = value.get(name)
    if not isinstance(item, dict):
        raise TemplateConfigError(f"{label}.{name} 必须是对象。")
    return item


def _bool_field(value: dict[str, Any], name: str, label: str) -> bool:
    """读取布尔字段，禁止用字符串或数字宽松转换。"""

    item = value.get(name)
    if not isinstance(item, bool):
        raise TemplateConfigError(f"{label}.{name} 必须是布尔值。")
    return item
