from __future__ import annotations

import os
import sys
from pathlib import Path


BUILTIN_SKILLS_DIR_ENV = "XCODEAGENT_BUILTIN_SKILLS_DIR"
BUILTIN_SKILLS_VIRTUAL_ROOT = "/.xcodeagent/builtin-skills/"
REACT_ANTD_V4_SKILL_NAME = "react-antd-v4-codegen"
_ENTRY_FILES = [
    "REACT_BEST_PRACTICES_GUIDE.md",
    "AGENTS.md",
]
_REFERENCE_FILES = [
    "references/dependencies-and-antd.md",
    "references/structure-and-ownership.md",
    "references/react-rules.md",
    "references/review-checklist.md",
]
REQUIRED_BUILTIN_SKILL_FILES = {
    REACT_ANTD_V4_SKILL_NAME: ["SKILL.md", *_ENTRY_FILES, *_REFERENCE_FILES],
}


def resolve_builtin_skills_root() -> Path:
    """Resolve bundled skills without depending on the process working directory."""

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
    skills_root = root or resolve_builtin_skills_root()
    return [
        skills_root / skill_name / relative_path
        for skill_name, relative_paths in REQUIRED_BUILTIN_SKILL_FILES.items()
        for relative_path in relative_paths
    ]


def validate_required_builtin_skills(root: Path | None = None) -> Path:
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
    skills_root = root or resolve_builtin_skills_root()
    if not skills_root.is_dir():
        return []
    return sorted(
        child.name
        for child in skills_root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    )


def is_builtin_skill_virtual_path(file_path: str) -> bool:
    root = BUILTIN_SKILLS_VIRTUAL_ROOT.rstrip("/")
    return file_path == root or file_path.startswith(f"{root}/")
