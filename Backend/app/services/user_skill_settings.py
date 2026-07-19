from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


SKILL_SETTINGS_FILE_NAME = "skill-settings.json"
MAX_SKILL_SETTINGS_BYTES = 256 * 1024
_SETTINGS_WRITE_LOCK = threading.Lock()


def _to_camel(value: str) -> str:
    """把内部蛇形字段转换成持久化 JSON 使用的驼峰字段。"""

    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class SkillSettingsDocument(BaseModel):
    """描述当前环境中用户技能的持久化启用设置。"""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    schema_version: Literal[1] = 1
    disabled_skills: list[str] = Field(default_factory=list)
    updated_at: str = Field(default="")


class SkillSettingsError(RuntimeError):
    """表示用户技能设置文件无法安全读取或写入。"""


def resolve_skill_settings_path(skills_root: Path) -> Path:
    """根据用户技能目录解析同环境下的启用设置文件。"""

    return skills_root.parent / SKILL_SETTINGS_FILE_NAME


def read_skill_settings(skills_root: Path) -> SkillSettingsDocument:
    """读取并严格校验技能设置，文件缺失时返回默认全开启状态。"""

    settings_path = resolve_skill_settings_path(skills_root)
    if not settings_path.exists():
        return SkillSettingsDocument()
    if settings_path.is_symlink() or not settings_path.is_file():
        raise SkillSettingsError("用户技能设置文件不是可用的常规文件。")

    try:
        if settings_path.stat().st_size > MAX_SKILL_SETTINGS_BYTES:
            raise SkillSettingsError("用户技能设置文件超过大小限制。")
        raw_content = settings_path.read_bytes()
        if len(raw_content) > MAX_SKILL_SETTINGS_BYTES:
            raise SkillSettingsError("用户技能设置文件超过大小限制。")
        document = SkillSettingsDocument.model_validate_json(raw_content)
    except SkillSettingsError:
        raise
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise SkillSettingsError("用户技能设置文件无效，已停止加载用户技能。") from exc

    disabled_skills = _normalize_relative_paths(document.disabled_skills)
    return document.model_copy(update={"disabled_skills": disabled_skills})


def set_user_skill_enabled(
    skills_root: Path,
    relative_path: str,
    enabled: bool,
) -> SkillSettingsDocument:
    """原子更新单个用户技能的启用状态并返回最新设置。"""

    normalized_path = _validate_relative_path(relative_path)
    with _SETTINGS_WRITE_LOCK:
        current = read_skill_settings(skills_root)
        disabled = set(current.disabled_skills)
        if enabled:
            disabled.discard(normalized_path)
        else:
            disabled.add(normalized_path)
        updated = SkillSettingsDocument(
            disabled_skills=sorted(disabled, key=lambda value: (value.casefold(), value)),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        _write_skill_settings(skills_root, updated)
        return updated


def clear_user_skill_setting(skills_root: Path, relative_path: str) -> None:
    """清理已删除或重新创建技能遗留的关闭状态。"""

    normalized_path = _validate_relative_path(relative_path)
    settings_path = resolve_skill_settings_path(skills_root)
    if not settings_path.exists():
        return
    with _SETTINGS_WRITE_LOCK:
        current = read_skill_settings(skills_root)
        if normalized_path not in current.disabled_skills:
            return
        disabled = [
            value for value in current.disabled_skills if value != normalized_path
        ]
        updated = SkillSettingsDocument(
            disabled_skills=disabled,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        _write_skill_settings(skills_root, updated)


def _normalize_relative_paths(relative_paths: list[str]) -> list[str]:
    """校验、去重并稳定排序持久化的技能相对路径。"""

    normalized = {_validate_relative_path(value) for value in relative_paths}
    return sorted(normalized, key=lambda value: (value.casefold(), value))


def _validate_relative_path(relative_path: str) -> str:
    """限制设置键为直属用户技能目录中的 SKILL.md。"""

    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
        or "\x00" in relative_path
    ):
        raise SkillSettingsError("用户技能设置包含无效路径。")
    path = PurePosixPath(relative_path)
    parts = path.parts
    if (
        path.is_absolute()
        or len(parts) != 2
        or parts[0] in {"", ".", ".."}
        or parts[1] != "SKILL.md"
    ):
        raise SkillSettingsError("用户技能设置包含无效路径。")
    return f"{parts[0]}/SKILL.md"


def _write_skill_settings(
    skills_root: Path,
    document: SkillSettingsDocument,
) -> None:
    """在环境目录内写入临时文件并原子替换正式设置文件。"""

    environment_root = skills_root.parent
    if environment_root.is_symlink():
        raise SkillSettingsError("用户技能环境目录不允许使用符号链接。")
    try:
        environment_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillSettingsError("无法创建用户技能环境目录。") from exc
    if not environment_root.is_dir():
        raise SkillSettingsError("用户技能环境目录不可用。")

    settings_path = resolve_skill_settings_path(skills_root)
    if settings_path.exists() and (settings_path.is_symlink() or not settings_path.is_file()):
        raise SkillSettingsError("用户技能设置文件不是可用的常规文件。")

    payload = (
        json.dumps(
            document.model_dump(by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".skill-settings.",
            suffix=".tmp",
            dir=environment_root,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as temporary_file:
            descriptor = None
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, settings_path)
    except OSError as exc:
        raise SkillSettingsError("无法保存用户技能启用状态。") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
