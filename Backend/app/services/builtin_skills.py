from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import Field

from app.services.user_skills import ApiModel, parse_skill_frontmatter


BUILTIN_SKILLS_DIR_ENV = "XCODEAGENT_BUILTIN_SKILLS_DIR"
BUILTIN_SKILLS_VIRTUAL_ROOT = "/.xcodeagent/builtin-skills/"
REACT_ANTD_V4_SKILL_NAME = "react-antd-v4-codegen"
REACT_DEV_SPEC_SKILL_NAME = "react-develop-specification"
CODE_BLOCK_TEMPLATE_SKILL_NAME = "code-block-template"

_ANTD_V4_ENTRY_FILES = [
    "REACT_BEST_PRACTICES_GUIDE.md",
    "AGENTS.md",
]
_ANTD_V4_REFERENCE_FILES = [
    "references/dependencies-and-antd.md",
    "references/structure-and-ownership.md",
    "references/react-rules.md",
    "references/review-checklist.md",
]

_REACT_DEV_SPEC_REFERENCE_FILES = [
    "references/00-framework-intro.md",
    "references/01-naming-standards.md",
    "references/02-coding-standards.md",
    "references/03-security-standards.md",
    "references/04-engineering-standards.md",
    "references/05-project-example.md",
    "references/06-appendix.md",
]

_CODE_BLOCK_TEMPLATE_REFERENCE_FILES = [
    "references/blocks.md",
    "references/codegen-strategy.md",
    "references/mock-data.md",
    "references/page-templates.md",
]

REQUIRED_BUILTIN_SKILL_FILES = {
    REACT_ANTD_V4_SKILL_NAME: ["SKILL.md", *_ANTD_V4_ENTRY_FILES, *_ANTD_V4_REFERENCE_FILES],
    REACT_DEV_SPEC_SKILL_NAME: ["SKILL.md", *_REACT_DEV_SPEC_REFERENCE_FILES],
    CODE_BLOCK_TEMPLATE_SKILL_NAME: ["SKILL.md", *_CODE_BLOCK_TEMPLATE_REFERENCE_FILES],
}


class BuiltinSkillSummary(ApiModel):
    """描述技能页面展示所需的只读内置技能元数据。"""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    directory_name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    version: str | None = None


def resolve_builtin_skills_root() -> Path:
    """在源码和冻结布局中解析内置技能目录。"""

    configured_path = os.getenv(BUILTIN_SKILLS_DIR_ENV)
    if configured_path:
        return Path(configured_path).expanduser().resolve()

    module_relative_root = Path(__file__).resolve().parent.parent / "builtin_skills"
    candidates = [module_relative_root]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root).resolve() / "app" / "builtin_skills")

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return module_relative_root


def required_builtin_skill_paths(root: Path | None = None) -> list[Path]:
    """返回构建和启动阶段必须存在的全部内置技能文件。"""

    skills_root = root or resolve_builtin_skills_root()
    return [
        skills_root / skill_name / relative_path
        for skill_name, relative_paths in REQUIRED_BUILTIN_SKILL_FILES.items()
        for relative_path in relative_paths
    ]


def validate_required_builtin_skills(root: Path | None = None) -> Path:
    """验证内置技能资源完整并返回解析后的目录。"""

    skills_root = (root or resolve_builtin_skills_root()).resolve()
    missing = [
        path.relative_to(skills_root).as_posix()
        for path in required_builtin_skill_paths(skills_root)
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError(
            "Required built-in skill files are missing under "
            f"{skills_root}: {', '.join(missing)}"
        )
    return skills_root


def available_builtin_skills(root: Path | None = None) -> list[str]:
    """返回健康检查使用的内置技能目录名列表。"""

    skills_root = root or resolve_builtin_skills_root()
    if not skills_root.is_dir():
        return []
    return sorted(
        child.name
        for child in skills_root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    )


def builtin_skills_root_label() -> str:
    """返回不会暴露宿主路径的内置技能虚拟目录。"""

    return BUILTIN_SKILLS_VIRTUAL_ROOT.rstrip("/")


def list_builtin_skills(root: Path | None = None) -> list[BuiltinSkillSummary]:
    """从内置 SKILL.md 读取技能页面所需的只读卡片摘要。"""

    skills_root = root or resolve_builtin_skills_root()
    if not skills_root.is_dir():
        return []

    summaries: list[BuiltinSkillSummary] = []
    for directory_name in available_builtin_skills(skills_root):
        skill_file = skills_root / directory_name / "SKILL.md"
        try:
            metadata = parse_skill_frontmatter(skill_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError(f"内置技能 {directory_name} 的元数据无效。") from exc
        summaries.append(
            BuiltinSkillSummary(
                name=metadata["name"],
                description=metadata["description"],
                directory_name=directory_name,
                relative_path=f"{directory_name}/SKILL.md",
                version=metadata.get("version"),
            )
        )
    return sorted(
        summaries,
        key=lambda skill: (skill.name.casefold(), skill.relative_path.casefold()),
    )


def is_builtin_skill_virtual_path(file_path: str) -> bool:
    """判断虚拟路径是否属于只读内置技能命名空间。"""

    root = BUILTIN_SKILLS_VIRTUAL_ROOT.rstrip("/")
    return file_path == root or file_path.startswith(f"{root}/")
