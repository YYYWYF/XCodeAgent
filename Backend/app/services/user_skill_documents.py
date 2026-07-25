from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field

from app.services.user_skills import (
    ApiModel,
    SkillFrontmatterError,
    list_user_skills,
    parse_skill_frontmatter,
    resolve_user_skills_root,
    user_skills_root_label,
)
from app.services.user_skill_settings import clear_user_skill_setting


MAX_SKILL_CONTENT_BYTES = 512 * 1024
CREATE_SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class UserSkillDocument(ApiModel):
    name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    content: str
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class DeletedUserSkill(ApiModel):
    name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)


class GetUserSkillRequest(ApiModel):
    action: Literal["get"]
    relative_path: str = Field(min_length=1)


class CreateUserSkillRequest(ApiModel):
    action: Literal["create"]
    content: str


class DeleteUserSkillRequest(ApiModel):
    action: Literal["delete"]
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


class SkillAlreadyExistsError(RuntimeError):
    pass


class SkillNameError(ValueError):
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
        content = _normalize_newlines(raw_content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SkillFrontmatterError("SKILL.md 必须使用 UTF-8 编码。") from exc
    metadata = parse_skill_frontmatter(content)
    return UserSkillDocument(
        name=metadata["name"],
        relative_path=relative_path,
        content=content,
        revision=_content_revision(raw_content),
    )


def create_user_skill_document(
    content: str,
    *,
    root: Path | None = None,
) -> UserSkillDocument:
    """创建直属用户技能，并清理同路径遗留的关闭状态。"""

    normalized_content = _normalize_newlines(content)
    encoded_content = _encode_skill_content(normalized_content)
    metadata = _parse_create_skill_frontmatter(normalized_content)
    name = metadata["name"]
    skills_root = _ensure_user_skills_root(root)

    try:
        existing_skills = list_user_skills(skills_root).skills
    except (OSError, RuntimeError) as exc:
        raise SkillPathError("无法检查已有用户技能。") from exc
    if any(skill.name == name for skill in existing_skills):
        raise SkillAlreadyExistsError(f"技能 {name} 已存在。")

    skill_directory = skills_root / name
    try:
        skill_directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise SkillAlreadyExistsError(f"技能目录 {name} 已存在。") from exc
    except OSError as exc:
        raise SkillPathError("无法创建技能目录。") from exc

    try:
        _create_skill_file(skill_directory / "SKILL.md", encoded_content)
    except Exception:
        try:
            skill_directory.rmdir()
        except OSError:
            pass
        raise

    relative_path = f"{name}/SKILL.md"
    clear_user_skill_setting(skills_root, relative_path)
    return UserSkillDocument(
        name=name,
        relative_path=relative_path,
        content=normalized_content,
        revision=_content_revision(encoded_content),
    )


def save_user_skill_document(
    relative_path: str,
    content: str,
    expected_revision: str,
    *,
    root: Path | None = None,
) -> UserSkillDocument:
    skill_file = _resolve_user_skill_file(relative_path, root=root)
    normalized_content = _normalize_newlines(content)
    encoded_content = _encode_skill_content(normalized_content)

    metadata = parse_skill_frontmatter(normalized_content)
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
        content=normalized_content,
        revision=_content_revision(encoded_content),
    )


def delete_user_skill(
    relative_path: str,
    *,
    root: Path | None = None,
) -> DeletedUserSkill:
    """删除直属用户技能目录，并同步清理启用状态。"""

    skills_root = root or resolve_user_skills_root()
    skill_file = _resolve_user_skill_file(relative_path, root=skills_root)
    raw_content = _read_skill_content_bytes(skill_file)
    try:
        content = _normalize_newlines(raw_content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SkillFrontmatterError("SKILL.md 必须使用 UTF-8 编码。") from exc
    metadata = parse_skill_frontmatter(content)
    skill_directory = skill_file.parent
    try:
        shutil.rmtree(skill_directory)
    except OSError as exc:
        raise SkillPathError("无法删除技能目录。") from exc
    clear_user_skill_setting(skills_root, relative_path)
    return DeletedUserSkill(name=metadata["name"], relative_path=relative_path)


def _encode_skill_content(content: str) -> bytes:
    """把技能正文统一为 LF 后编码，避免系统默认换行污染版本号。"""

    try:
        encoded_content = _normalize_newlines(content).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SkillFrontmatterError("SKILL.md 必须是有效的 UTF-8 文本。") from exc
    if len(encoded_content) > MAX_SKILL_CONTENT_BYTES:
        raise SkillContentTooLargeError("SKILL.md 不能超过 512 KiB。")
    return encoded_content


def _normalize_newlines(content: str) -> str:
    """把 CRLF、CR 统一为用户技能文档的标准 LF。"""

    return content.replace("\r\n", "\n").replace("\r", "\n")


def _parse_create_skill_frontmatter(content: str) -> dict[str, str]:
    metadata = parse_skill_frontmatter(content)
    lines = content.removeprefix("\ufeff").splitlines()
    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() in {"---", "..."}
        ),
        None,
    )
    if closing_index is None or lines[closing_index].strip() != "---":
        raise SkillFrontmatterError("新技能 YAML frontmatter 必须使用 --- 结束。")
    if not CREATE_SKILL_NAME_PATTERN.fullmatch(metadata["name"]):
        raise SkillNameError(
            "技能 name 必须以英文小写字母开头，且仅包含英文小写字母、数字和下划线。"
        )
    return metadata


def _ensure_user_skills_root(root: Path | None) -> Path:
    skills_root = root or resolve_user_skills_root()
    environment_root = skills_root.parent
    if environment_root.is_symlink():
        raise SkillPathError("用户技能环境目录不允许使用符号链接。")
    try:
        skills_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillPathError("无法创建用户技能目录。") from exc
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise SkillPathError(f"{user_skills_root_label()} 不可用。")
    return skills_root


def _create_skill_file(skill_file: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(skill_file, flags, 0o644)
        created = True
        file = os.fdopen(descriptor, "wb")
        descriptor = None
        with file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
    except (OSError, ValueError) as exc:
        if created:
            try:
                skill_file.unlink()
            except OSError:
                pass
        raise SkillPathError("无法创建 SKILL.md。") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


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
        raise SkillPathError("只允许访问直属 skill 目录中的 SKILL.md。")

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
    """按规范化换行计算版本号，使同一技能在两个系统上版本一致。"""

    canonical_content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical_content).hexdigest()


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
