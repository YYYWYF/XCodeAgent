"""读写当前唯一支持的 TemplateState V3。"""
from __future__ import annotations
import json
from pathlib import Path
from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.workspace_bootstrap.models import TemplateStateError
from app.utils.atomic_json import atomic_write_json

TEMPLATE_STATE_V3_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "template-state.json"

def load_template_state_v3(workspace: str | Path) -> TemplateStateV3:
    """读取并严格拒绝任意非 V3 State。"""
    path = Path(workspace).expanduser().resolve() / TEMPLATE_STATE_V3_RELATIVE_PATH
    try:
        return TemplateStateV3.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise TemplateStateError("TEMPLATE_STATE_SCHEMA_UNSUPPORTED：工作区缺少有效的 V3 TemplateState。") from exc

def write_template_state_v3(workspace: str | Path, state: TemplateStateV3) -> None:
    """原子持久化 Engine 返回的 V3 State。"""
    atomic_write_json(Path(workspace).expanduser().resolve() / TEMPLATE_STATE_V3_RELATIVE_PATH, state.model_dump(mode="json"))
