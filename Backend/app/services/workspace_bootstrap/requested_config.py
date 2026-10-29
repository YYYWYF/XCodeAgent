"""把已确认应用事实确定性映射为 Template Engine RequestedConfig。"""

from __future__ import annotations

from app.branding import WORKSPACE_ARTIFACT_DIR

import json
from pathlib import Path
from typing import Any

from app.services.workspace_bootstrap.models import TemplateConfigError
from app.services.application_config import read_application_config
from app.services.template_reconcile.desired import TemplateCapabilityError, requested_config_from_application_config
from app.services.workspace_bootstrap.template_layout import compile_workspace_template_capabilities


def compile_template_requested_config(
    workspace_root: str | Path,
    *,
    include_application_config: bool = False,
) -> dict[str, Any]:
    """验证 confirmed TechnicalPlan 后生成当前 Engine 请求，仅首次 Generate 携带基础应用配置。"""

    root = Path(workspace_root).expanduser().resolve()
    technical_plan = _load_object(
        root / WORKSPACE_ARTIFACT_DIR / 'plans/technical-plan.json', "technical-plan.json"
    )
    _validate_technical_plan(technical_plan)
    try:
        application_config = read_application_config(root)
        source_revision = technical_plan.get("sourceConfigRevision")
        if source_revision != application_config.get("configRevision"):
            raise TemplateConfigError(
                "TechnicalPlan 基于过期 application.json 配置生成，必须先重新规划。"
            )
        requested = requested_config_from_application_config(application_config)
        requested["capabilities"] = compile_workspace_template_capabilities(
            root, application_config, plan=technical_plan,
        )
        if not include_application_config:
            # 设置页变更只提示用户手工更新工程环境变量；后续 Reconcile 不能借由
            # applicationConfig 覆写用户已维护的 frontend/.env。
            requested.pop("applicationConfig", None)
        return requested
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
