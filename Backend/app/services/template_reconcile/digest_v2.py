"""提供 V2 TemplateState 与 immutable Package 的唯一摘要算法。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.services.template_reconcile.protocol_v2 import TemplateStateV2


def template_state_digest_v2(state: TemplateStateV2) -> str:
    """计算严格排序、紧凑 JSON 的 V2 State SHA-256，供双端 Package binding 与 Recovery 共用。"""

    content = json.dumps(
        state.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def package_digest_v2(path: str | Path) -> str:
    """计算 immutable Package ZIP 原始 bytes 摘要，禁止 Recovery 只信任旧 Attempt 元数据。"""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()
