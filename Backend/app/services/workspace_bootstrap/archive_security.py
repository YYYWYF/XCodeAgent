"""不可信 Template Package 的 ZIP 路径、类型和配额安全检查。"""

from __future__ import annotations

import re
import stat
import zipfile
from pathlib import PurePosixPath

from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def validate_archive_entries(package: zipfile.ZipFile, limits: ArchiveLimits) -> list[zipfile.ZipInfo]:
    """校验 ZIP 全部条目，并返回已验证的原始条目列表。"""

    entries = package.infolist()
    if len(entries) > limits.max_files:
        raise TemplatePackageError("模板 ZIP 条目数量超过限制。")
    paths: set[str] = set()
    folded_paths: set[str] = set()
    total_size = 0
    for entry in entries:
        path = validate_zip_entry_path(entry.filename)
        validate_zip_entry_type(entry)
        normalized = path.as_posix().rstrip("/")
        folded = normalized.casefold()
        if normalized in paths or folded in folded_paths:
            raise TemplatePackageError("模板 ZIP 包含重复或大小写冲突路径。")
        paths.add(normalized)
        folded_paths.add(folded)
        if not entry.is_dir():
            if entry.file_size > limits.max_extracted_bytes:
                raise TemplatePackageError("模板 ZIP 单文件超过展开大小限制。")
            total_size += entry.file_size
            if total_size > limits.max_extracted_bytes:
                raise TemplatePackageError("模板 ZIP 总展开大小超过限制。")
    return entries


def validate_zip_entry_path(raw_path: str) -> PurePosixPath:
    """拒绝 ZIP Slip、平台绝对路径和非 POSIX 条目路径。"""

    if not raw_path or "\x00" in raw_path or "\\" in raw_path:
        raise TemplatePackageError("模板 ZIP 包含无效路径。")
    if raw_path.startswith("/") or _WINDOWS_DRIVE.match(raw_path):
        raise TemplatePackageError("模板 ZIP 不允许绝对路径。")
    source = raw_path[:-1] if raw_path.endswith("/") else raw_path
    if not source or any(part in {"", ".", ".."} for part in source.split("/")):
        raise TemplatePackageError("模板 ZIP 不允许路径穿越。")
    return PurePosixPath(raw_path)


def validate_zip_entry_type(entry: zipfile.ZipInfo) -> None:
    """拒绝加密、符号链接、设备和 FIFO 等非普通条目。"""

    if entry.flag_bits & 0x1:
        raise TemplatePackageError("不支持加密模板 ZIP。")
    mode = (entry.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise TemplatePackageError("模板 ZIP 不允许符号链接或特殊文件。")
    if entry.is_dir() and file_type and not stat.S_ISDIR(mode):
        raise TemplatePackageError("模板 ZIP 目录条目类型无效。")
