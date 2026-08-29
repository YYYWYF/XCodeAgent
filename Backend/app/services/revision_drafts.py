"""工作台正式修订草稿的创建、保存、确认和放弃协调服务。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.domain.application_revision import (
    RevisionArtifactReference,
    RevisionDraftMetadata,
)
from app.services.artifact_invalidation import canonical_sha256
from app.workspace.revision_draft_documents import (
    discard_revision_draft,
    load_revision_draft,
    write_revision_draft,
)


MarkdownSynchronizer = Callable[[str, dict[str, Any]], dict[str, Any]]
ArtifactValidator = Callable[[dict[str, Any]], None]
EMPTY_CANONICAL_SHA256 = hashlib.sha256(b"").hexdigest()


def create_revision_draft(
    workspace: str | Path,
    *,
    change_id: str,
    artifact_key: str,
    kind: str,
    target_id: str,
    canonical_json_path: str | Path,
    markdown: str,
    artifact: dict[str, Any],
    based_on_paths: dict[str, str | Path],
) -> RevisionDraftMetadata:
    """基于当前 canonical 与直接上游哈希创建唯一当前草稿。"""

    metadata = RevisionDraftMetadata(
        changeId=change_id,
        artifactKey=artifact_key,
        kind=kind,
        targetId=target_id,
        baseCanonicalSha256=_canonical_or_empty_sha256(canonical_json_path),
        basedOnCanonical=[
            RevisionArtifactReference(
                artifactKey=upstream_key,
                sha256=canonical_sha256(upstream_path),
            )
            for upstream_key, upstream_path in sorted(based_on_paths.items())
        ],
        generatedAt=datetime.now(UTC),
    )
    draft_artifact = {
        **artifact,
        "confirmation_status": "pending_user_confirmation",
        "basedOn": [
            reference.model_dump(mode="json", by_alias=True)
            for reference in metadata.based_on_canonical
        ],
    }
    write_revision_draft(
        workspace,
        metadata=metadata,
        markdown=markdown,
        artifact=draft_artifact,
    )
    return metadata


def save_revision_draft_markdown(
    workspace: str | Path,
    *,
    change_id: str,
    artifact_key: str,
    markdown: str,
) -> str:
    """只覆盖当前草稿 Markdown 并返回正文哈希，保存不等于确认。"""

    metadata, _old_markdown, artifact = load_revision_draft(
        workspace,
        change_id=change_id,
        artifact_key=artifact_key,
    )
    write_revision_draft(
        workspace,
        metadata=metadata,
        markdown=markdown,
        artifact=artifact,
    )
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def confirm_revision_draft(
    workspace: str | Path,
    *,
    change_id: str,
    artifact_key: str,
    canonical_markdown_path: str | Path,
    canonical_json_path: str | Path,
    based_on_paths: dict[str, str | Path],
    synchronize_markdown: MarkdownSynchronizer,
    validate_artifact: ArtifactValidator,
) -> dict[str, Any]:
    """校验基线、同步 Markdown、验证领域结构并原子提交当前 canonical。"""

    metadata, markdown, artifact = load_revision_draft(
        workspace,
        change_id=change_id,
        artifact_key=artifact_key,
    )
    if _canonical_or_empty_sha256(canonical_json_path) != metadata.base_canonical_sha256:
        raise ValueError("当前 canonical 已变化，请重新生成 revision 草稿。")
    expected_upstream = {
        reference.artifact_key: reference.sha256
        for reference in metadata.based_on_canonical
    }
    actual_upstream = {
        upstream_key: canonical_sha256(upstream_path)
        for upstream_key, upstream_path in based_on_paths.items()
    }
    if expected_upstream != actual_upstream:
        raise ValueError("草稿直接上游已变化，请重新生成 revision 草稿。")
    synchronized = synchronize_markdown(markdown, dict(artifact))
    if not isinstance(synchronized, dict):
        raise ValueError("Markdown 同步器必须返回完整内部 JSON。")
    confirmed = {
        **synchronized,
        "confirmation_status": "confirmed",
        "basedOn": [
            {"artifactKey": key, "sha256": value}
            for key, value in sorted(actual_upstream.items())
        ],
    }
    validate_artifact(confirmed)
    _commit_canonical_pair(
        markdown_path=Path(canonical_markdown_path),
        json_path=Path(canonical_json_path),
        markdown=markdown,
        artifact=confirmed,
    )
    discard_revision_draft(
        workspace,
        change_id=change_id,
        artifact_key=artifact_key,
    )
    return {
        "artifactKey": artifact_key,
        "confirmationStatus": "confirmed",
        "canonicalSha256": canonical_sha256(canonical_json_path),
        "artifact": confirmed,
    }


def discard_current_revision_draft(
    workspace: str | Path,
    *,
    change_id: str,
    artifact_key: str,
) -> None:
    """放弃当前未确认草稿，不读取或改写 canonical。"""

    discard_revision_draft(
        workspace,
        change_id=change_id,
        artifact_key=artifact_key,
    )


def _commit_canonical_pair(
    *,
    markdown_path: Path,
    json_path: Path,
    markdown: str,
    artifact: dict[str, Any],
) -> None:
    """先落临时文件，再按 Markdown 后 JSON 顺序提交一个正式产物。"""

    if markdown_path.parent != json_path.parent:
        raise ValueError("canonical Markdown 与 JSON 必须位于同一产物目录。")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_temp = _temporary_text(markdown_path, markdown)
    json_temp = _temporary_text(
        json_path,
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    try:
        os.replace(markdown_temp, markdown_path)
        os.replace(json_temp, json_path)
    except Exception:
        for temporary_name in (markdown_temp, json_temp):
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        raise


def _temporary_text(path: Path, value: str) -> str:
    """在目标目录创建已 fsync 的临时正文文件并返回路径。"""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary_name


def _canonical_or_empty_sha256(path: str | Path) -> str:
    """以空正文哈希表示尚不存在的 canonical，支持新增正式 endpoint 的首次确认。"""

    candidate = Path(path)
    return canonical_sha256(candidate) if candidate.is_file() else EMPTY_CANONICAL_SHA256
