"""把当前用户的能力目标与应用配置快照解析为只读配置变更提案。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.application_config_change import ApplicationConfigChange, ApplicationConfigPath
from app.services.access_control_intent import CapabilityIntent, resolve_capability_intents


class ApplicationConfigChangeResolutionError(ValueError):
    """表示当前配置无效或用户目标存在冲突，不能生成确定的配置提案。"""


def resolve_application_config_changes(
    request: str,
    *,
    workspace_root: str | Path,
) -> list[ApplicationConfigChange]:
    """读取工作区当前 application.json 并生成只读 Delta，不读取需求或写入文件。"""

    return _resolve_application_config_changes_from_snapshot(
        request,
        current_application=_load_current_application(workspace_root),
    )


def _load_current_application(workspace_root: str | Path) -> dict[str, Any]:
    """只读取工作区 canonical application.json，拒绝缺失、损坏或非对象配置。"""

    application_file = Path(workspace_root).expanduser().resolve() / ".xcodeagent" / "application.json"
    if not application_file.is_file():
        raise ApplicationConfigChangeResolutionError("当前工作区缺少 .xcodeagent/application.json。")
    try:
        current_application = json.loads(application_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplicationConfigChangeResolutionError(
            "当前工作区的 application.json 无法读取或格式无效。"
        ) from exc
    if not isinstance(current_application, dict):
        raise ApplicationConfigChangeResolutionError("当前工作区的 application.json 必须是 JSON 对象。")
    return current_application


def _resolve_application_config_changes_from_snapshot(
    request: str,
    *,
    current_application: dict[str, Any],
) -> list[ApplicationConfigChange]:
    """依据已读取的配置快照编译 Delta，确保解析规则与文件读取职责分离。"""

    if (
        not isinstance(current_application, dict)
        or type(current_application.get("schemaVersion")) is not int
        or current_application["schemaVersion"] != 5
    ):
        raise ApplicationConfigChangeResolutionError("仅支持当前 schemaVersion 5 的应用配置。")
    intents = resolve_capability_intents(request)
    targets: dict[ApplicationConfigPath, CapabilityIntent] = {}
    for intent in intents:
        previous = targets.get(intent.path)
        if previous is not None and previous.enabled != intent.enabled:
            raise ApplicationConfigChangeResolutionError(f"{intent.path} 同时包含启用与停用请求，请明确目标。")
        targets[intent.path] = intent
    # 权限依赖登录是平台规则；显式冲突必须澄清，不得悄悄覆盖关闭登录的请求。
    authorization = targets.get("authorization.enabled")
    login = targets.get("auth.enable")
    if authorization is not None and authorization.enabled:
        if login is not None and not login.enabled:
            raise ApplicationConfigChangeResolutionError("启用权限管理不能同时关闭登录认证。")
        if login is None:
            targets = {
                "auth.enable": CapabilityIntent("auth.enable", True, authorization.evidence),
                **targets,
            }
    changes: list[ApplicationConfigChange] = []
    for path, intent in targets.items():
        section, field = path.split(".")
        current_section = current_application.get(section)
        current_value = current_section.get(field) if isinstance(current_section, dict) else None
        if type(current_value) is not bool:
            raise ApplicationConfigChangeResolutionError(f"application.json 的 {path} 必须是布尔值。")
        if current_value == intent.enabled:
            continue
        changes.append(
            ApplicationConfigChange(
                path=path,
                operation="set",
                from_value=current_value,
                to_value=intent.enabled,
                reason=(
                    "启用权限管理需要同时启用登录认证"
                    if path == "auth.enable" and login is None and authorization is not None
                    else f"用户明确要求{'启用' if intent.enabled else '停用'} {path}"
                ),
                evidence=intent.evidence,
            )
        )
    return changes
