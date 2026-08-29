"""工作台正式修订草稿的安全路径与原子文档读写。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.domain.application_revision import RevisionDraftMetadata


_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


def revision_draft_directory(
    workspace: str | Path,
    *,
    change_id: str,
    artifact_key: str,
) -> Path:
    """返回受工作区限制且不允许路径穿越的当前草稿目录。"""

    _validate_key(change_id, label="changeId")
    _validate_key(artifact_key, label="artifactKey")
    root = Path(workspace).expanduser().resolve() / ".xcodeagent" / "drafts" / "revisions"
    directory = (root / change_id / artifact_key).resolve()
    if root != directory and root not in directory.parents:
        raise ValueError("revision 草稿路径越出工作区。")
    return directory


def write_revision_draft(
    workspace: str | Path,
    *,
    metadata: RevisionDraftMetadata,
    markdown: str,
    artifact: dict[str, Any],
) -> Path:
    """覆盖写入当前草稿 Markdown、内部 JSON 和 metadata，不保留历史版本。"""

    directory = revision_draft_directory(
        workspace,
        change_id=metadata.change_id,
        artifact_key=metadata.artifact_key,
    )
    directory.mkdir(parents=True, exist_ok=True)
    _write_text_atomically(directory / "artifact.md", markdown)
    _write_json_atomically(directory / "artifact.json", artifact)
    _write_json_atomically(
        directory / "metadata.json",
        metadata.model_dump(mode="json", by_alias=True),
    )
    return directory


def load_revision_draft(
    workspace: str | Path,
    *,
    change_id: str,
    artifact_key: str,
) -> tuple[RevisionDraftMetadata, str, dict[str, Any]]:
    """严格读取一个当前草稿的 metadata、Markdown 和内部 JSON。"""

    directory = revision_draft_directory(
        workspace,
        change_id=change_id,
        artifact_key=artifact_key,
    )
    try:
        metadata = RevisionDraftMetadata.model_validate_json(
            (directory / "metadata.json").read_text(encoding="utf-8")
        )
        markdown = (directory / "artifact.md").read_text(encoding="utf-8")
        artifact = json.loads((directory / "artifact.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("revision 草稿缺失或未通过当前合同校验。") from exc
    if not isinstance(artifact, dict):
        raise ValueError("revision 草稿内部 JSON 必须是对象。")
    if metadata.change_id != change_id or metadata.artifact_key != artifact_key:
        raise ValueError("revision 草稿 metadata 与目录身份不匹配。")
    return metadata, markdown, artifact


def discard_revision_draft(
    workspace: str | Path,
    *,
    change_id: str,
    artifact_key: str,
) -> None:
    """只删除指定 change 的当前 artifact 草稿目录，并保留全部 canonical。"""

    directory = revision_draft_directory(
        workspace,
        change_id=change_id,
        artifact_key=artifact_key,
    )
    if directory.exists():
        shutil.rmtree(directory)
    change_directory = directory.parent
    if change_directory.exists() and not any(change_directory.iterdir()):
        change_directory.rmdir()


def _validate_key(value: str, *, label: str) -> None:
    """限制目录 key 为单段稳定标识，拒绝斜杠、空白和路径穿越。"""

    if not _SAFE_KEY.fullmatch(str(value or "")):
        raise ValueError(f"{label} 不是安全的稳定标识。")


def _write_json_atomically(path: Path, value: dict[str, Any]) -> None:
    """以稳定 JSON 编码和原子替换写入内部文档。"""

    _write_text_atomically(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _write_text_atomically(path: Path, value: str) -> None:
    """在目标目录创建临时文件、fsync 后原子替换单个草稿文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
