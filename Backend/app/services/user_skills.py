from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
import yaml


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
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class UserSkillSummary(ApiModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    directory_name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    version: str | None = None


class UserSkillIssue(ApiModel):
    relative_path: str = Field(min_length=1)
    code: Literal["invalid_frontmatter", "read_error", "symlink_ignored"]
    message: str = Field(min_length=1)


class UserSkillCatalog(ApiModel):
    root: str = Field(min_length=1)
    skills: list[UserSkillSummary] = Field(default_factory=list)
    skipped_count: int = Field(default=0, ge=0)
    issues: list[UserSkillIssue] = Field(default_factory=list)


class SkillFrontmatterError(ValueError):
    pass


def resolve_user_skills_root() -> Path:
    """Return the environment-specific user-owned skill catalog directory."""

    return Path.home() / user_skills_working_dir() / "skills"


def user_skills_working_dir() -> str:
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
    return f"~/{user_skills_working_dir()}/skills"


def list_user_skills(root: Path | None = None) -> UserSkillCatalog:
    """List valid direct-child skills without exposing host absolute paths."""

    skills_root = root or resolve_user_skills_root()
    if not skills_root.exists():
        return UserSkillCatalog(root=user_skills_root_label())
    if not skills_root.is_dir():
        raise NotADirectoryError(f"{user_skills_root_label()} 不是目录。")

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
            )
        )

    skills.sort(key=lambda skill: (skill.name.casefold(), skill.relative_path.casefold()))
    return UserSkillCatalog(
        root=user_skills_root_label(),
        skills=skills,
        skipped_count=len(issues),
        issues=issues,
    )


def _read_skill_frontmatter(skill_file: Path) -> dict[str, str]:
    with skill_file.open("r", encoding="utf-8") as file:
        text = file.read(MAX_SKILL_FRONTMATTER_BYTES)
    return parse_skill_frontmatter(text)


def parse_skill_frontmatter(content: str) -> dict[str, str]:
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
