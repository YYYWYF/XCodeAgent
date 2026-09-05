"""解析当前权限资源 capability 的唯一 Task provider 或外部满足事实。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.authorization_resource_catalog import (
    compile_frontend_resource_catalog,
    resource_catalog_fingerprint,
)
from app.services.planning_issues import ValidationIssue


AUTH_GUARD_UNIT_ID = "frontend:auth-guard"
AUTH_RESOURCE_CAPABILITY_PREFIX = "frontend.auth.resources:"


@dataclass(frozen=True)
class AuthCapabilityDependencyResolution:
    """保存当前 R 的唯一 provider、外部满足状态及结构化 Global 问题。"""

    capability_id: str
    provider_task_ids: tuple[str, ...]
    external_satisfied: bool
    issues: tuple[ValidationIssue, ...]


def current_auth_resource_capability(project_plan: Mapping[str, Any]) -> str | None:
    """从当前完整权限目录计算版本化 capability；权限关闭时返回 None。"""

    manifest = project_plan.get("authorization_manifest")
    if manifest is None:
        return None
    catalog = compile_frontend_resource_catalog(
        dict(manifest) if isinstance(manifest, Mapping) else manifest
    )
    if catalog is None:
        return None
    return f"{AUTH_RESOURCE_CAPABILITY_PREFIX}{resource_catalog_fingerprint(catalog)}"


def resolve_auth_capability_dependency(
    *,
    capability_id: str,
    tasks: Sequence[Mapping[str, Any]],
    external_capabilities: Sequence[Mapping[str, Any]],
    consumer_unit_ids: Sequence[str],
    consumer_task_ids: Sequence[str],
) -> AuthCapabilityDependencyResolution:
    """为当前 R 解析唯一 auth Task provider；外部满足时不产生 Task 依赖。"""

    providers = tuple(
        str(task.get("id"))
        for task in tasks
        if task.get("id")
        and task.get("unit_id") == AUTH_GUARD_UNIT_ID
        and capability_id in _task_provided_capabilities(task)
    )
    external_satisfied = any(
        capability.get("unit_id") == AUTH_GUARD_UNIT_ID
        and capability.get("capability_id") == capability_id
        for capability in external_capabilities
        if isinstance(capability, Mapping)
    )
    involved_units = tuple(dict.fromkeys((AUTH_GUARD_UNIT_ID, *consumer_unit_ids)))
    if len(providers) > 1:
        issue = ValidationIssue(
            code="GLOBAL_AUTH_CAPABILITY_PROVIDER_CONFLICT",
            level="global",
            category="generation",
            unit_ids=involved_units,
            task_ids=providers,
            retryable=False,
            message=f"权限资源能力 {capability_id} 存在多个 Task provider。",
            details={"capability_id": capability_id},
        )
        return AuthCapabilityDependencyResolution(
            capability_id, providers, external_satisfied, (issue,)
        )
    if not providers and not external_satisfied:
        issue = ValidationIssue(
            code="GLOBAL_AUTH_CAPABILITY_PROVIDER_MISSING",
            level="global",
            category="generation",
            unit_ids=involved_units,
            task_ids=tuple(dict.fromkeys(consumer_task_ids)),
            retryable=False,
            message=f"权限资源能力 {capability_id} 缺少 Task provider，且未由 workspace 外部满足。",
            details={"capability_id": capability_id},
        )
        return AuthCapabilityDependencyResolution(
            capability_id, (), False, (issue,)
        )
    return AuthCapabilityDependencyResolution(
        capability_id,
        () if external_satisfied else providers,
        external_satisfied,
        (),
    )


def _task_provided_capabilities(task: Mapping[str, Any]) -> set[str]:
    """汇总 Task 与 deliverable 的显式 capability，不把 Unit ID 当作 provider。"""

    result = set(_string_values(task.get("provides_capabilities")))
    deliverables = task.get("deliverables")
    if isinstance(deliverables, (list, tuple)):
        for deliverable in deliverables:
            if isinstance(deliverable, Mapping):
                result.update(_string_values(deliverable.get("provides")))
    return result


def _string_values(value: Any) -> tuple[str, ...]:
    """只读取精确非空字符串数组，非法声明不能成为 capability 证据。"""

    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        item
        for item in value
        if isinstance(item, str) and item and item == item.strip()
    )
