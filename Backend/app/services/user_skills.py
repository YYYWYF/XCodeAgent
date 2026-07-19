from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.services.user_skill_settings import (
    read_skill_settings,
    set_user_skill_enabled,
)


USER_SKILLS_WORKING_DIR_ENV = "XCODEAGENT_WORKING_DIR"
DEFAULT_USER_SKILLS_WORKING_DIR = ".xcodeagent_dev"
SUPPORTED_USER_SKILLS_WORKING_DIRS = {
    ".xcodeagent_dev",
    ".xcodeagent_st",
    ".xcodeagent_uat",
    ".xcodeagent",
}
MAX_SKILL_FRONTMATTER_BYTES = 64 * 1024


def _to_camel(value: str) -> str:
    """把内部蛇形字段转换成 AG-UI 使用的驼峰字段。"""

    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    """为技能接口模型统一提供驼峰别名。"""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class UserSkillSummary(ApiModel):
    """描述技能页面和运行时共同使用的用户技能摘要。"""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    directory_name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    version: str | None = None
    enabled: bool = True


class UserSkillIssue(ApiModel):
    """描述扫描用户技能目录时可独立跳过的问题。"""

    relative_path: str = Field(min_length=1)
    code: Literal["invalid_frontmatter", "read_error", "symlink_ignored"]
    message: str = Field(min_length=1)


class UserSkillCatalog(ApiModel):
    """描述当前环境的用户技能目录结果。"""

    root: str = Field(min_length=1)
    skills: list[UserSkillSummary] = Field(default_factory=list)
    skipped_count: int = Field(default=0, ge=0)
    issues: list[UserSkillIssue] = Field(default_factory=list)


class SkillFrontmatterError(ValueError):
    """表示 SKILL.md 的 YAML 元数据无效。"""

    pass


def resolve_user_skills_root() -> Path:
    """返回当前环境隔离的用户技能目录。"""

    return Path.home() / user_skills_working_dir() / "skills"


def user_skills_working_dir() -> str:
    """读取并校验当前 XCodeAgent 用户环境目录名。"""

    working_dir = os.getenv(
        USER_SKILLS_WORKING_DIR_ENV,
        DEFAULT_USER_SKILLS_WORKING_DIR,
    ).strip()
    if working_dir not in SUPPORTED_USER_SKILLS_WORKING_DIRS:
        raise RuntimeError(
            f"{USER_SKILLS_WORKING_DIR_ENV} 必须是受支持的 XCodeAgent 用户目录。"
        )
    return working_dir


def user_skills_root_label() -> str:
    """返回不暴露主机绝对路径的用户技能目录标签。"""

    return f"~/{user_skills_working_dir()}/skills"


def user_skill_settings_label() -> str:
    """返回不暴露主机绝对路径的技能启停状态文件标签。"""

    return f"~/{user_skills_working_dir()}/skill-settings.json"


def list_user_skills(root: Path | None = None) -> UserSkillCatalog:
    """列出直属有效用户技能并合并持久化启用状态。"""

    skills_root = root or resolve_user_skills_root()
    if not skills_root.exists():
        return UserSkillCatalog(root=user_skills_root_label())
    if not skills_root.is_dir():
        raise NotADirectoryError(f"{user_skills_root_label()} 不是目录。")

    disabled_skills = set(read_skill_settings(skills_root).disabled_skills)

    skills: list[UserSkillSummary] = []
    issues: list[UserSkillIssue] = []
    entries = sorted(skills_root.iterdir(), key=lambda item: item.name.casefold())
    for entry in entries:
        relative_path = f"{entry.name}/SKILL.md"
        if entry.is_symlink():
            issues.append(
                UserSkillIssue(
                    relative_path=relative_path,
                    code="symlink_ignored",
                    message="已忽略符号链接技能目录。",
                )
            )
            continue
        if not entry.is_dir():
            continue

        skill_file = entry / "SKILL.md"
        if not skill_file.exists():
            continue
        if skill_file.is_symlink():
            issues.append(
                UserSkillIssue(
                    relative_path=relative_path,
                    code="symlink_ignored",
                    message="已忽略符号链接 SKILL.md。",
                )
            )
            continue
        if not skill_file.is_file():
            issues.append(
                UserSkillIssue(
                    relative_path=relative_path,
                    code="read_error",
                    message="SKILL.md 不是常规文件。",
                )
            )
            continue

        try:
            metadata = _read_skill_frontmatter(skill_file)
            stat = skill_file.stat()
        except SkillFrontmatterError as exc:
            issues.append(
                UserSkillIssue(
                    relative_path=relative_path,
                    code="invalid_frontmatter",
                    message=str(exc),
                )
            )
            continue
        except (OSError, UnicodeError) as exc:
            issues.append(
                UserSkillIssue(
                    relative_path=relative_path,
                    code="read_error",
                    message=f"无法读取技能元数据：{type(exc).__name__}。",
                )
            )
            continue

        skills.append(
            UserSkillSummary(
                name=metadata["name"],
                description=metadata["description"],
                directory_name=entry.name,
                relative_path=relative_path,
                updated_at=datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                version=metadata.get("version"),
                enabled=relative_path not in disabled_skills,
            )
        )

    skills.sort(key=lambda skill: (skill.name.casefold(), skill.relative_path.casefold()))
    return UserSkillCatalog(
        root=user_skills_root_label(),
        skills=skills,
        skipped_count=len(issues),
        issues=issues,
    )


def update_user_skill_enabled(
    relative_path: str,
    enabled: bool,
    *,
    root: Path | None = None,
) -> UserSkillSummary:
    """验证目标技能存在后更新启用状态并返回最新摘要。"""

    skills_root = root or resolve_user_skills_root()
    catalog = list_user_skills(skills_root)
    target = next(
        (skill for skill in catalog.skills if skill.relative_path == relative_path),
        None,
    )
    if target is None:
        raise ValueError("要切换的用户技能不存在或不可用。")
    set_user_skill_enabled(skills_root, relative_path, enabled)
    return target.model_copy(update={"enabled": enabled})


def _read_skill_frontmatter(skill_file: Path) -> dict[str, str]:
    """只读取限定范围内的 SKILL.md frontmatter。"""

    with skill_file.open("r", encoding="utf-8") as file:
        text = file.read(MAX_SKILL_FRONTMATTER_BYTES)
    return parse_skill_frontmatter(text)


def parse_skill_frontmatter(content: str) -> dict[str, str]:
    """解析并校验技能名称、描述和可选版本。"""

    text = content.removeprefix("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillFrontmatterError("SKILL.md 缺少 YAML frontmatter。")

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() in {"---", "..."}),
        None,
    )
    if closing_index is None:
        raise SkillFrontmatterError("YAML frontmatter 未在读取范围内结束。")

    frontmatter = "\n".join(lines[1:closing_index])
    try:
        loaded = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise SkillFrontmatterError("YAML frontmatter 格式无效。") from exc
    if not isinstance(loaded, dict):
        raise SkillFrontmatterError("YAML frontmatter 必须是对象。")

    metadata: dict[str, str] = {}
    for key in ("name", "description", "version"):
        value = loaded.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise SkillFrontmatterError(f"{key} 必须是字符串。")
        normalized_value = value.strip()
        if normalized_value:
            metadata[key] = normalized_value

    if not metadata.get("name"):
        raise SkillFrontmatterError("YAML frontmatter 缺少有效的 name。")
    if not metadata.get("description"):
        raise SkillFrontmatterError("YAML frontmatter 缺少有效的 description。")
    return metadata
