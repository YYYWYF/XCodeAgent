"""计算 V3 State 与 Package 的稳定 SHA-256 摘要。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from app.services.template_reconcile.protocol_v3 import TemplateStateV3

def template_state_digest_v3(state: TemplateStateV3) -> str:
    """按递归排序 JSON 生成冻结 State digest。"""
    raw = json.dumps(state.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()

def package_digest_v3(path: str | Path) -> str:
    """计算已持久化 Update ZIP 的摘要。"""
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
