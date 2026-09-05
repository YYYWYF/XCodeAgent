"""Template Reconcile 使用的原始文件摘要工具。"""

from __future__ import annotations

import hashlib
from pathlib import Path


class TemplateReconcileDigestError(ValueError):
    """表示 Reconcile 无法读取需要绑定的原始文件。"""


def artifact_file_sha256(path: str | Path) -> str:
    """计算文件原始 bytes 的 SHA-256，不将其误作 JSON 语义摘要。"""

    artifact_path = Path(path)
    try:
        return hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise TemplateReconcileDigestError(
            f"无法读取用于 Reconcile 绑定的文件：{artifact_path}"
        ) from exc
