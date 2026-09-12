"""把当前用户的能力目标与应用配置快照解析为只读配置变更提案。"""

from __future__ import annotations

from typing import Any

from app.domain.application_config_change import ApplicationConfigChange, ApplicationConfigPath
from app.services.access_control_intent import CapabilityIntent, resolve_capability_intents
from app.services.application_config import ApplicationConfigService
from app.services.application_config.schema import ApplicationConfigError, CURRENT_APPLICATION_SCHEMA_VERSION


class ApplicationConfigChangeResolutionError(ValueError):
    """表示当前配置无效或用户目标存在冲突，不能生成确定的配置提案。"""


def resolve_application_config_changes(
    request: str,
    *,
    workspace_root: str | Path,
) -> list[ApplicationConfigChange]:
    """读取工作区当前 application.json 并生成只读 Delta，不读取需求或写入文件。"""

    service = ApplicationConfigService(workspace_root)
    return _resolve_application_config_changes_from_snapshot(
        request,
        current_application=_load_current_application(service),
        service=service,
    )


def _load_current_application(service: ApplicationConfigService) -> dict[str, Any]:
    """只读取工作区 canonical application.json，拒绝缺失、损坏或非对象配置。"""

    try:
        return service.read()
    except ApplicationConfigError as exc:
        raise ApplicationConfigChangeResolutionError(str(exc)) from exc


def _resolve_application_config_changes_from_snapshot(
    request: str,
    *,
    current_application: dict[str, Any],
    service: ApplicationConfigService,
) -> list[ApplicationConfigChange]:
    """依据已读取的配置快照编译 Delta，确保解析规则与文件读取职责分离。"""

    if (
        not isinstance(current_application, dict)
        or type(current_application.get("schemaVersion")) is not int
        or current_application["schemaVersion"] != CURRENT_APPLICATION_SCHEMA_VERSION
    ):
        raise ApplicationConfigChangeResolutionError(
            f"仅支持当前 schemaVersion {CURRENT_APPLICATION_SCHEMA_VERSION} 的应用配置。"
        )
    if type(current_application.get("configRevision")) is not int or current_application["configRevision"] < 1:
        raise ApplicationConfigChangeResolutionError("application.json 的 configRevision 必须是大于零的整数。")
    intents = resolve_capability_intents(request)
    targets: dict[ApplicationConfigPath, CapabilityIntent] = {}
    for intent in intents:
        previous = targets.get(intent.path)
        if previous is not None and previous.enabled != intent.enabled:
            raise ApplicationConfigChangeResolutionError(f"{intent.path} 同时包含启用与停用请求，请明确目标。")
        targets[intent.path] = intent
    authorization = targets.get("authorization.enabled")
    login = targets.get("auth.enable")
    if authorization is not None and authorization.enabled and login is not None and not login.enabled:
        raise ApplicationConfigChangeResolutionError("启用权限管理不能同时关闭登录认证。")
    try:
        return service.changes_for_target_details(
            {path: intent.enabled for path, intent in targets.items()},
            reasons={
                path: f"用户明确要求{'启用' if intent.enabled else '停用'} {path}"
                for path, intent in targets.items()
            },
            evidence={path: intent.evidence for path, intent in targets.items()},
        )
    except ApplicationConfigError as exc:
        raise ApplicationConfigChangeResolutionError(str(exc)) from exc
