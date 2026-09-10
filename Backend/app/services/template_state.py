"""提供当前唯一支持的 V2 TemplateState 读取与 Build 绑定工具。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services.template_reconcile.protocol_v2 import TemplateStateV2
from app.services.template_reconcile.state_v2 import (
    TEMPLATE_STATE_V2_RELATIVE_PATH,
    load_template_state_v2,
)
from app.services.workspace_bootstrap.models import TemplateStateError

TEMPLATE_STATE_RELATIVE_PATH = TEMPLATE_STATE_V2_RELATIVE_PATH
_TEMPLATE_CONTEXT_FIELDS = frozenset({"state_path", "template_revision", "effective_capabilities"})


def template_state_path(workspace: str | Path) -> Path:
    """返回 V2 TemplateState 的唯一规范路径。"""

    return Path(workspace).expanduser().resolve() / TEMPLATE_STATE_RELATIVE_PATH


def load_template_state(workspace: str | Path) -> dict[str, Any]:
    """读取 V2 State 并返回隔离的 JSON 语义副本，旧 State 不提供兼容读取。"""

    return load_template_state_v2(workspace).model_dump(mode="json")


def validate_template_state(value: Any) -> dict[str, Any]:
    """严格验证 V2 State 并返回规范 JSON，不再接受 managedFiles 旧结构。"""

    try:
        return TemplateStateV2.model_validate(value).model_dump(mode="json")
    except ValueError as exc:
        raise TemplateStateError("TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：TemplateState 不是当前 V2 协议。") from exc


def template_revision(state: dict[str, Any]) -> str:
    """返回已验证 V2 State 的模板 revision。"""

    return str(validate_template_state(state)["templateRevision"])


def effective_capabilities(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回按 ID 稳定排序的 V2 effective Capability 副本。"""

    effective = validate_template_state(state)["effective"]
    return {identifier: deepcopy(effective[identifier]) for identifier in sorted(effective)}


def requested_capabilities(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回按 ID 稳定排序的 V2 requested Capability 副本。"""

    requested = validate_template_state(state)["requested"]
    return {identifier: deepcopy(requested[identifier]) for identifier in sorted(requested)}


def has_capability(state: dict[str, Any], capability_id: str) -> bool:
    """判断 Capability 是否存在于 V2 effective 闭包。"""

    return capability_id in effective_capabilities(state)


def template_context(state: dict[str, Any]) -> dict[str, Any]:
    """生成 Build 所需的 V2 State revision/effective 只读绑定快照。"""

    validated = validate_template_state(state)
    return {"state_path": TEMPLATE_STATE_RELATIVE_PATH.as_posix(), "template_revision": str(validated["templateRevision"]), "effective_capabilities": effective_capabilities(validated)}


def validate_template_context(value: Any) -> dict[str, Any]:
    """验证已持久化的 Build V2 TemplateState 绑定快照。"""

    if not isinstance(value, dict) or set(value) != _TEMPLATE_CONTEXT_FIELDS:
        raise TemplateStateError("template_context 字段不符合当前 V2 Build 绑定契约。")
    if value.get("state_path") != TEMPLATE_STATE_RELATIVE_PATH.as_posix():
        raise TemplateStateError("template_context.state_path 不是唯一 V2 TemplateState 路径。")
    revision = value.get("template_revision")
    capabilities = value.get("effective_capabilities")
    if not isinstance(revision, str) or not revision or not isinstance(capabilities, dict):
        raise TemplateStateError("template_context 的 revision 或 effective_capabilities 无效。")
    state = {"schemaVersion": 2, "templateRevision": revision, "releaseDigest": "sha256:" + "0" * 64, "requested": {}, "effective": capabilities, "appliedAdditions": {}}
    return {"state_path": TEMPLATE_STATE_RELATIVE_PATH.as_posix(), "template_revision": revision, "effective_capabilities": effective_capabilities(state)}


def assert_template_context_matches(state: dict[str, Any], expected_context: Any) -> dict[str, Any]:
    """要求 Build 绑定的 revision/effective 与当前 V2 State 完全一致。"""

    current = template_context(state)
    expected = validate_template_context(expected_context)
    if current != expected:
        raise TemplateStateError("TemplateState 与 Build 绑定快照不一致。")
    return current
