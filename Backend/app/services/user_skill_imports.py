from __future__ import annotations

import base64
import binascii
import os
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field

from app.services.user_skill_documents import MAX_SKILL_CONTENT_BYTES
from app.services.user_skills import (
    ApiModel,
    SkillFrontmatterError,
    UserSkillSummary,
    list_user_skills,
    parse_skill_frontmatter,
    resolve_user_skills_root,
    user_skills_root_label,
)
from app.services.user_skill_settings import clear_user_skill_setting


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_FILES = 256
MAX_ARCHIVE_ENTRIES = 512
MAX_EXTRACTED_BYTES = 32 * 1024 * 1024
MAX_RESOURCE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BASE64_CHARACTERS = 4 * ((MAX_ARCHIVE_BYTES + 2) // 3)
IMPORT_SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[a-zA-Z]:")
COPY_CHUNK_BYTES = 64 * 1024
_IMPORT_RENAME_LOCK = threading.Lock()


class ImportUserSkillRequest(ApiModel):
    action: Literal["import"]
    file_name: str = Field(min_length=1, max_length=255)
    archive_base64: str = Field(
        min_length=1,
        max_length=MAX_ARCHIVE_BASE64_CHARACTERS,
    )


class ImportedUserSkill(ApiModel):
    imported: UserSkillSummary
    root: str = Field(min_length=1)


class SkillImportError(ValueError):
    code = "import_error"


class SkillArchiveFormatError(SkillImportError):
    code = "invalid_archive"


class SkillArchiveSizeError(SkillImportError):
    code = "archive_limit"


class SkillArchivePathError(SkillImportError):
    code = "unsafe_archive_path"


class SkillImportConflictError(SkillImportError):
    code = "skill_conflict"


class SkillImportFilesystemError(SkillImportError):
    code = "filesystem_error"


def import_user_skill_archive(
    file_name: str,
    archive_base64: str,
    *,
    root: Path | None = None,
) -> ImportedUserSkill:
    """校验并原子导入一个用户技能 ZIP，同时恢复默认开启状态。"""

    if not file_name.strip().lower().endswith(".zip"):
        raise SkillArchiveFormatError("只支持 ZIP 技能包。")
    archive = _decode_archive(archive_base64)
    skills_root = _ensure_skills_root(root)

    try:
        with zipfile.ZipFile(BytesIO(archive)) as package:
            entries, skill_entry, wrapper = _validate_archive(package)
            skill_content = _read_skill_document(package, skill_entry)
            try:
                metadata = parse_skill_frontmatter(skill_content)
            except SkillFrontmatterError as exc:
                raise SkillArchiveFormatError(str(exc)) from exc
            skill_name = metadata["name"]
            if not IMPORT_SKILL_NAME_PATTERN.fullmatch(skill_name):
                raise SkillArchiveFormatError(
                    "技能 name 必须以英文小写字母开头，且仅包含英文小写字母、数字、连字符和下划线。"
                )
            _ensure_available(skills_root, skill_name)
            target = _extract_atomically(
                package,
                entries,
                wrapper=wrapper,
                skill_name=skill_name,
                skills_root=skills_root,
            )
    except zipfile.BadZipFile as exc:
        raise SkillArchiveFormatError("ZIP 文件已损坏或格式无效。") from exc
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, SkillImportError):
            raise
        raise SkillImportFilesystemError("无法导入技能 ZIP。") from exc

    skill_file = target / "SKILL.md"
    relative_path = f"{skill_name}/SKILL.md"
    clear_user_skill_setting(skills_root, relative_path)
    updated_at = datetime.fromtimestamp(
        skill_file.stat().st_mtime, tz=timezone.utc
    ).isoformat()
    return ImportedUserSkill(
        root=user_skills_root_label(),
        imported=UserSkillSummary(
            name=skill_name,
            description=metadata["description"],
            directory_name=skill_name,
            relative_path=relative_path,
            updated_at=updated_at,
            version=metadata.get("version"),
            enabled=True,
        ),
    )


def _decode_archive(archive_base64: str) -> bytes:
    if len(archive_base64) > MAX_ARCHIVE_BASE64_CHARACTERS:
        raise SkillArchiveSizeError("ZIP 文件不能超过 32 MiB。")
    try:
        archive = base64.b64decode(archive_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SkillArchiveFormatError("ZIP Base64 数据无效。") from exc
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise SkillArchiveSizeError("ZIP 文件不能超过 32 MiB。")
    if not archive:
        raise SkillArchiveFormatError("ZIP 文件为空。")
    return archive


def _ensure_skills_root(root: Path | None) -> Path:
    skills_root = root or resolve_user_skills_root()
    environment_root = skills_root.parent
    if environment_root.is_symlink():
        raise SkillImportFilesystemError("用户技能环境目录不允许使用符号链接。")
    try:
        skills_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillImportFilesystemError("无法创建用户技能目录。") from exc
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise SkillImportFilesystemError(f"{user_skills_root_label()} 不可用。")
    return skills_root


def _validate_archive(
    package: zipfile.ZipFile,
) -> tuple[list[zipfile.ZipInfo], zipfile.ZipInfo, str | None]:
    all_entries = package.infolist()
    if len(all_entries) > MAX_ARCHIVE_ENTRIES:
        raise SkillArchiveSizeError("ZIP 条目数量过多。")
    if any(entry.flag_bits & 0x1 for entry in all_entries):
        raise SkillArchiveFormatError("不支持加密 ZIP 文件。")

    entries: list[zipfile.ZipInfo] = []
    paths_seen: set[str] = set()
    folded_paths_seen: set[str] = set()
    file_count = 0
    total_size = 0
    skill_entries: list[zipfile.ZipInfo] = []

    for entry in all_entries:
        path = _validate_entry_path(entry.filename)
        if _is_system_junk(path):
            continue
        _validate_entry_type(entry)
        normalized_path = path.as_posix().rstrip("/")
        folded_path = normalized_path.casefold()
        if normalized_path in paths_seen or folded_path in folded_paths_seen:
            raise SkillArchivePathError("ZIP 包含重复或大小写冲突的路径。")
        paths_seen.add(normalized_path)
        folded_paths_seen.add(folded_path)
        entries.append(entry)

        if not entry.is_dir():
            file_count += 1
            if file_count > MAX_ARCHIVE_FILES:
                raise SkillArchiveSizeError("ZIP 文件数量不能超过 256。")
            resource_limit = (
                MAX_SKILL_CONTENT_BYTES
                if path.name == "SKILL.md"
                else MAX_RESOURCE_BYTES
            )
            if entry.file_size > resource_limit:
                if path.name == "SKILL.md":
                    raise SkillArchiveSizeError("SKILL.md 不能超过 512 KiB。")
                raise SkillArchiveSizeError("ZIP 内单个资源不能超过 10 MiB。")
            total_size += entry.file_size
            if total_size > MAX_EXTRACTED_BYTES:
                raise SkillArchiveSizeError("ZIP 总展开大小不能超过 32 MiB。")
            if path.name == "SKILL.md":
                skill_entries.append(entry)

    if len(skill_entries) != 1:
        raise SkillArchiveFormatError("ZIP 必须且只能包含一个 SKILL.md。")
    if not entries:
        raise SkillArchiveFormatError("ZIP 中未找到技能内容。")

    skill_path = PurePosixPath(skill_entries[0].filename)
    if len(skill_path.parts) == 1:
        wrapper = None
    elif len(skill_path.parts) == 2:
        wrapper = skill_path.parts[0]
    else:
        raise SkillArchiveFormatError(
            "SKILL.md 只能位于 ZIP 根目录或单层包装目录内。"
        )
    if wrapper is not None:
        for entry in entries:
            entry_path = PurePosixPath(entry.filename)
            if entry_path.parts[0] != wrapper:
                raise SkillArchiveFormatError("ZIP 只能包含一个技能目录。")

    return entries, skill_entries[0], wrapper


def _validate_entry_path(raw_path: str) -> PurePosixPath:
    if not raw_path or "\x00" in raw_path or "\\" in raw_path:
        raise SkillArchivePathError("ZIP 包含无效路径。")
    if raw_path.startswith("/") or WINDOWS_DRIVE_PATTERN.match(raw_path):
        raise SkillArchivePathError("ZIP 不允许绝对路径。")
    canonical_source = raw_path[:-1] if raw_path.endswith("/") else raw_path
    raw_parts = canonical_source.split("/")
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise SkillArchivePathError("ZIP 不允许路径穿越。")
    path = PurePosixPath(raw_path)
    return path


def _is_system_junk(path: PurePosixPath) -> bool:
    return "__MACOSX" in path.parts or path.name == ".DS_Store"


def _validate_entry_type(entry: zipfile.ZipInfo) -> None:
    if entry.flag_bits & 0x1:
        raise SkillArchiveFormatError("不支持加密 ZIP 文件。")
    unix_mode = (entry.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type and not (stat.S_ISREG(unix_mode) or stat.S_ISDIR(unix_mode)):
        raise SkillArchivePathError("ZIP 不允许符号链接或其他特殊文件。")
    if entry.is_dir() and file_type and not stat.S_ISDIR(unix_mode):
        raise SkillArchivePathError("ZIP 目录条目类型无效。")


def _read_skill_document(package: zipfile.ZipFile, entry: zipfile.ZipInfo) -> str:
    chunks: list[bytes] = []
    total_read = 0
    try:
        with package.open(entry, "r") as source:
            while chunk := source.read(COPY_CHUNK_BYTES):
                total_read += len(chunk)
                if total_read > MAX_SKILL_CONTENT_BYTES:
                    raise SkillArchiveSizeError("SKILL.md 不能超过 512 KiB。")
                chunks.append(chunk)
    except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
        raise SkillArchiveFormatError("SKILL.md 完整性校验失败。") from exc
    raw_content = b"".join(chunks)
    try:
        return raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillArchiveFormatError("SKILL.md 必须使用 UTF-8 编码。") from exc


def _ensure_available(skills_root: Path, skill_name: str) -> None:
    target = skills_root / skill_name
    if target.exists() or target.is_symlink():
        raise SkillImportConflictError(f"技能目录 {skill_name} 已存在。")
    try:
        existing_skills = list_user_skills(skills_root).skills
    except OSError as exc:
        raise SkillImportFilesystemError("无法检查已有用户技能。") from exc
    if any(skill.name == skill_name for skill in existing_skills):
        raise SkillImportConflictError(f"技能 {skill_name} 已存在。")


def _extract_atomically(
    package: zipfile.ZipFile,
    entries: list[zipfile.ZipInfo],
    *,
    wrapper: str | None,
    skill_name: str,
    skills_root: Path,
) -> Path:
    try:
        staging = Path(tempfile.mkdtemp(prefix=".skill-import-", dir=skills_root))
    except OSError as exc:
        raise SkillImportFilesystemError("无法创建技能导入临时目录。") from exc
    target = skills_root / skill_name
    total_written = 0
    try:
        for entry in entries:
            source_path = PurePosixPath(entry.filename)
            relative_parts = (
                source_path.parts[1:] if wrapper is not None else source_path.parts
            )
            if not relative_parts:
                continue
            destination = staging.joinpath(*relative_parts)
            if entry.is_dir():
                destination.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            written = _write_archive_file(package, entry, destination)
            total_written += written
            if total_written > MAX_EXTRACTED_BYTES:
                raise SkillArchiveSizeError("ZIP 总展开大小不能超过 32 MiB。")
        if not (staging / "SKILL.md").is_file():
            raise SkillArchiveFormatError("ZIP 中未找到有效的 SKILL.md。")
        with _IMPORT_RENAME_LOCK:
            if target.exists() or target.is_symlink():
                raise SkillImportConflictError(f"技能目录 {skill_name} 已存在。")
            try:
                os.rename(staging, target)
            except FileExistsError as exc:
                raise SkillImportConflictError(
                    f"技能目录 {skill_name} 已存在。"
                ) from exc
            except OSError as exc:
                raise SkillImportFilesystemError("无法完成技能目录原子写入。") from exc
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _write_archive_file(
    package: zipfile.ZipFile,
    entry: zipfile.ZipInfo,
    destination: Path,
) -> int:
    written = 0
    limit = (
        MAX_SKILL_CONTENT_BYTES
        if destination.name == "SKILL.md"
        else MAX_RESOURCE_BYTES
    )
    try:
        with package.open(entry, "r") as source, destination.open("xb") as output:
            while chunk := source.read(COPY_CHUNK_BYTES):
                written += len(chunk)
                if written > limit:
                    raise SkillArchiveSizeError("ZIP 内文件的实际展开大小超过限制。")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except SkillImportError:
        raise
    except zipfile.BadZipFile as exc:
        raise SkillArchiveFormatError("ZIP 文件完整性校验失败。") from exc
    except (OSError, RuntimeError) as exc:
        raise SkillImportFilesystemError("无法写入技能文件。") from exc
    return written
