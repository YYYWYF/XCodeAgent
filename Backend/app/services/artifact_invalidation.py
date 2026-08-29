"""基于直接上游 canonical 哈希的正式产物失效传播。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ArtifactInvalidationError(ValueError):
    """表示正式产物引用缺失、损坏或无法安全标记 stale。"""


def canonical_sha256(path: str | Path) -> str:
    """计算一个当前 canonical 文件的完整 SHA-256。"""

    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ArtifactInvalidationError(f"无法读取 canonical：{path}") from exc


def stale_artifact_keys(
    artifacts: dict[str, dict[str, Any]],
    *,
    canonical_hashes: dict[str, str],
) -> list[str]:
    """按 basedOn 直接引用计算不匹配闭包，并返回稳定排序的 stale key。"""

    stale: set[str] = set()
    changed = True
    while changed:
        changed = False
        for artifact_key, artifact in artifacts.items():
            if artifact_key in stale:
                continue
            based_on = artifact.get("basedOn")
            if not isinstance(based_on, list):
                based_on = []
            for reference in based_on:
                if not isinstance(reference, dict):
                    raise ArtifactInvalidationError(
                        f"{artifact_key} 的 basedOn 必须只包含对象。"
                    )
                upstream = str(reference.get("artifactKey") or "").strip()
                expected = str(reference.get("sha256") or "").strip()
                actual = canonical_hashes.get(upstream)
                if not upstream or len(expected) != 64 or actual is None:
                    raise ArtifactInvalidationError(
                        f"{artifact_key} 缺少可验证的直接上游哈希。"
                    )
                if expected != actual or upstream in stale:
                    stale.add(artifact_key)
                    changed = True
                    break
    return sorted(stale)


def mark_artifact_documents_stale(
    artifact_paths: dict[str, str | Path],
    *,
    canonical_hashes: dict[str, str],
) -> list[str]:
    """读取当前 JSON 正式产物、计算失效闭包并原子写回 stale 状态。"""

    artifacts: dict[str, dict[str, Any]] = {}
    for artifact_key, path in artifact_paths.items():
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactInvalidationError(f"正式产物无法读取：{artifact_key}") from exc
        if not isinstance(value, dict):
            raise ArtifactInvalidationError(f"正式产物必须是对象：{artifact_key}")
        artifacts[artifact_key] = value
    stale = stale_artifact_keys(artifacts, canonical_hashes=canonical_hashes)
    for artifact_key in stale:
        artifact = {**artifacts[artifact_key], "confirmation_status": "stale"}
        _write_json_atomically(Path(artifact_paths[artifact_key]), artifact)
    return stale


def mark_artifact_document_stale(path: str | Path) -> None:
    """把一个已确定受影响的直接下游正式产物原子标记为 stale。"""

    document_path = Path(path)
    try:
        value = json.loads(document_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactInvalidationError(f"正式产物无法读取：{document_path}") from exc
    if not isinstance(value, dict):
        raise ArtifactInvalidationError(f"正式产物必须是对象：{document_path}")
    _write_json_atomically(
        document_path,
        {**value, "confirmation_status": "stale", "status": "stale"},
    )


def assert_confirmed_artifact_closure(
    artifacts: dict[str, dict[str, Any]],
    *,
    canonical_hashes: dict[str, str],
) -> None:
    """在 Build 前拒绝未确认、stale 或 basedOn 不匹配的正式产物。"""

    invalid_status = sorted(
        key
        for key, artifact in artifacts.items()
        if artifact.get("confirmation_status") != "confirmed"
    )
    stale = stale_artifact_keys(artifacts, canonical_hashes=canonical_hashes)
    blocked = sorted(set(invalid_status) | set(stale))
    if blocked:
        raise ArtifactInvalidationError(
            "Build 需要所有受影响正式产物 confirmed 且 basedOn 匹配："
            + "、".join(blocked)
        )


def _write_json_atomically(path: Path, value: dict[str, Any]) -> None:
    """以同目录临时文件原子替换一个正式 JSON 文档。"""

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
