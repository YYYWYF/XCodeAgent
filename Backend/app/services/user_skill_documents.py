from __future__ import annotations

import hashlib
import hmac
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field

from app.services.user_skills import (
    ApiModel,
    SkillFrontmatterError,
    parse_skill_frontmatter,
    resolve_user_skills_root,
    user_skills_root_label,
)


MAX_SKILL_CONTENT_BYTES = 512 * 1024


class UserSkillDocument(ApiModel):
    name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    content: str
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class GetUserSkillRequest(ApiModel):
    action: Literal["get"]
    relative_path: str = Field(min_length=1)


class SaveUserSkillRequest(ApiModel):
    action: Literal["save"]
    relative_path: str = Field(min_length=1)
    content: str
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class SkillPathError(ValueError):
    pass


class SkillContentTooLargeError(ValueError):
    pass


class SkillRevisionConflictError(RuntimeError):
    pass


def read_user_skill_document(
    relative_path: str,
    *,
    root: Path | None = None,
) -> UserSkillDocument:
    skill_file = _resolve_user_skill_file(relative_path, root=root)
    raw_content = _read_skill_content_bytes(skill_file)
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillFrontmatterError("SKILL.md 必须使用 UTF-8 编码。") from exc
    metadata = parse_skill_frontmatter(content)
    return UserSkillDocument(
        name=metadata["name"],
        relative_path=relative_path,
        content=content,
        revision=_content_revision(raw_content),
    )


def save_user_skill_document(
    relative_path: str,
    content: str,
    expected_revision: str,
    *,
    root: Path | None = None,
) -> UserSkillDocument:
    skill_file = _resolve_user_skill_file(relative_path, root=root)
    try:
        encoded_content = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SkillFrontmatterError("SKILL.md 必须是有效的 UTF-8 文本。") from exc
    if len(encoded_content) > MAX_SKILL_CONTENT_BYTES:
        raise SkillContentTooLargeError("SKILL.md 不能超过 512 KiB。")

    metadata = parse_skill_frontmatter(content)
    current_content = _read_skill_content_bytes(skill_file)
    current_revision = _content_revision(current_content)
    if not hmac.compare_digest(current_revision, expected_revision):
        raise SkillRevisionConflictError(
            "SKILL.md 已被其他操作修改，请重新打开后再保存。"
        )

    file_mode = stat.S_IMODE(skill_file.stat().st_mode)
    _atomic_replace(skill_file, encoded_content, file_mode)
    return UserSkillDocument(
        name=metadata["name"],
        relative_path=relative_path,
        content=content,
        revision=_content_revision(encoded_content),
    )


def _resolve_user_skill_file(
    relative_path: str,
    *,
    root: Path | None = None,
) -> Path:
    if not relative_path or "\\" in relative_path:
        raise SkillPathError("技能路径无效。")
    path_parts = PurePosixPath(relative_path).parts
    if (
        len(path_parts) != 2
        or path_parts[0] in {"", ".", ".."}
        or path_parts[1] != "SKILL.md"
    ):
        raise SkillPathError("只允许读取直属 skill 目录中的 SKILL.md。")

    skills_root = root or resolve_user_skills_root()
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise SkillPathError(f"{user_skills_root_label()} 不可用。")
    skill_directory = skills_root / path_parts[0]
    if skill_directory.is_symlink() or not skill_directory.is_dir():
        raise SkillPathError("技能目录不存在或不允许访问。")
    skill_file = skill_directory / "SKILL.md"
    if skill_file.is_symlink() or not skill_file.is_file():
        raise SkillPathError("SKILL.md 不存在或不允许访问。")
    return skill_file


def _read_skill_content_bytes(skill_file: Path) -> bytes:
    if skill_file.stat().st_size > MAX_SKILL_CONTENT_BYTES:
        raise SkillContentTooLargeError("SKILL.md 不能超过 512 KiB。")
    with skill_file.open("rb") as file:
        content = file.read(MAX_SKILL_CONTENT_BYTES + 1)
    if len(content) > MAX_SKILL_CONTENT_BYTES:
        raise SkillContentTooLargeError("SKILL.md 不能超过 512 KiB。")
    return content


def _content_revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_replace(skill_file: Path, content: bytes, file_mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".SKILL.md.",
        suffix=".tmp",
        dir=skill_file.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_path, file_mode)
        if skill_file.is_symlink() or not skill_file.is_file():
            raise SkillPathError("保存前 SKILL.md 已发生路径变化。")
        os.replace(temporary_path, skill_file)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
