"""提供当前唯一支持的 V3 TemplateState 读取与 Build 绑定工具。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.template_reconcile.state_v3 import (
    TEMPLATE_STATE_V3_RELATIVE_PATH,
    load_template_state_v3,
)
from app.services.workspace_bootstrap.models import TemplateStateError

TEMPLATE_STATE_RELATIVE_PATH = TEMPLATE_STATE_V3_RELATIVE_PATH
_TEMPLATE_CONTEXT_FIELDS = frozenset({"state_path", "template_revision", "effective_capabilities"})


def template_state_path(workspace: str | Path) -> Path:
    """返回 V3 TemplateState 的唯一规范路径。"""

    return Path(workspace).expanduser().resolve() / TEMPLATE_STATE_RELATIVE_PATH


def load_template_state(workspace: str | Path) -> dict[str, Any]:
    """读取 V3 State 并返回隔离的 JSON 语义副本。"""

    return load_template_state_v3(workspace).model_dump(mode="json")


def validate_template_state(value: Any) -> dict[str, Any]:
    """严格验证 V3 State 并返回规范 JSON，不接受历史 State。"""

    try:
        return TemplateStateV3.model_validate(value).model_dump(mode="json")
    except ValueError as exc:
        raise TemplateStateError("TEMPLATE_STATE_SCHEMA_UNSUPPORTED：TemplateState 不是当前 V3 协议。") from exc


def template_revision(state: dict[str, Any]) -> str:
    """返回已验证 V3 State 的模板 revision。"""

    return str(validate_template_state(state)["templateRevision"])


def effective_capabilities(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回按 ID 稳定排序的 V3 effective Capability 副本。"""

    effective = validate_template_state(state)["effective"]
    return {identifier: deepcopy(effective[identifier]) for identifier in sorted(effective)}


def requested_capabilities(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回按 ID 稳定排序的 V3 requested Capability 副本。"""

    requested = validate_template_state(state)["requested"]
    return {identifier: deepcopy(requested[identifier]) for identifier in sorted(requested)}


def has_capability(state: dict[str, Any], capability_id: str) -> bool:
    """判断 Capability 是否存在于 V3 effective 闭包。"""

    return capability_id in effective_capabilities(state)


def template_context(state: dict[str, Any]) -> dict[str, Any]:
    """生成 Build 所需的 V3 State revision/effective 只读绑定快照。"""

    validated = validate_template_state(state)
    return {"state_path": TEMPLATE_STATE_RELATIVE_PATH.as_posix(), "template_revision": str(validated["templateRevision"]), "effective_capabilities": effective_capabilities(validated)}


def validate_template_context(value: Any) -> dict[str, Any]:
    """验证已持久化的 Build V3 TemplateState 绑定快照。"""

    if not isinstance(value, dict) or set(value) != _TEMPLATE_CONTEXT_FIELDS:
        raise TemplateStateError("template_context 字段不符合当前 V3 Build 绑定契约。")
    if value.get("state_path") != TEMPLATE_STATE_RELATIVE_PATH.as_posix():
        raise TemplateStateError("template_context.state_path 不是唯一 V3 TemplateState 路径。")
    revision = value.get("template_revision")
    capabilities = value.get("effective_capabilities")
    if not isinstance(revision, str) or not revision or not isinstance(capabilities, dict):
        raise TemplateStateError("template_context 的 revision 或 effective_capabilities 无效。")
    state = {"schemaVersion": 3, "templateRevision": revision, "requested": {}, "effective": capabilities, "installedArtifacts": {}, "managedContributions": {}}
    return {"state_path": TEMPLATE_STATE_RELATIVE_PATH.as_posix(), "template_revision": revision, "effective_capabilities": effective_capabilities(state)}


def assert_template_context_matches(state: dict[str, Any], expected_context: Any) -> dict[str, Any]:
    """要求 Build 绑定的 revision/effective 与当前 V3 State 完全一致。"""

    current = template_context(state)
    expected = validate_template_context(expected_context)
    if current != expected:
        raise TemplateStateError("TemplateState 与 Build 绑定快照不一致。")
    return current
