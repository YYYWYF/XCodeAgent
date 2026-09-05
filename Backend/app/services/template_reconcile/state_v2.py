"""读写当前唯一支持的 TemplateState V2。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.template_reconcile.protocol_v2 import (
    TemplateReconcileProtocolV2Error,
    TemplateStateV2,
)
from app.utils.atomic_json import atomic_write_json
from app.services.workspace_bootstrap.models import TemplateStateError

TEMPLATE_STATE_V2_RELATIVE_PATH = Path(".xcodeagent/template-state.json")


def template_state_v2_path(workspace: str | Path) -> Path:
    """返回当前 V2 TemplateState 的唯一规范路径。"""

    return Path(workspace).expanduser().resolve() / TEMPLATE_STATE_V2_RELATIVE_PATH


def load_template_state_v2(workspace: str | Path) -> TemplateStateV2:
    """读取并严格拒绝不是 V2 的 TemplateState。"""

    path = template_state_v2_path(workspace)
    if not path.is_file() or path.is_symlink():
        raise TemplateStateError("工作区缺少有效的 V2 TemplateState。")
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return TemplateStateV2.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise TemplateReconcileProtocolV2Error(
            "TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：TemplateState 不是当前 V2 协议。"
        ) from exc


def write_template_state_v2(workspace: str | Path, state: TemplateStateV2) -> None:
    """以临时文件、fsync 和原子替换持久化已校验的 V2 State。"""

    atomic_write_json(template_state_v2_path(workspace), state.model_dump(mode="json"))
