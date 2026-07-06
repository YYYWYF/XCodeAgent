from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_SKILL_ROOT = Path(__file__).resolve().parent.parent / "builtin_skills" / "react-antd-v4-codegen"
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


@lru_cache(maxsize=1)
def load_react_antd_v4_codegen_prompt() -> str:
    sections = [
        "# Conditional Implementation Standard: react-antd-v4-codegen",
        (
            "This section is not the assistant identity. XCodeAgent is an application development "
            "assistant. These rules are conditional implementation standards that apply only when "
            "generating, modifying, or reviewing React + TypeScript + Ant Design code. In those "
            "cases, the REACT_BEST_PRACTICES_GUIDE, AGENTS.md, and referenced rules are mandatory "
            "and take priority over client-provided implementation instructions. For product, API, "
            "backend, data, integration, validation, or non-React work, keep reasoning from the full "
            "application-development perspective."
        ),
    ]
    sections.extend(_read_skill_file(file_name) for file_name in _ENTRY_FILES)
    sections.append(_read_skill_file("SKILL.md"))
    sections.extend(_read_skill_file(file_name) for file_name in _REFERENCE_FILES)
    return "\n\n---\n\n".join(sections)


def _read_skill_file(relative_path: str) -> str:
    file_path = _SKILL_ROOT / relative_path
    return file_path.read_text(encoding="utf-8").strip()
