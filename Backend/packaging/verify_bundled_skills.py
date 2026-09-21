from __future__ import annotations

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builtin_skills import validate_required_builtin_skills  # noqa: E402
from app.services.page_templates import (  # noqa: E402
    SOURCE_PAGE_TEMPLATES_DIR,
    validate_page_templates,
)


def resolve_bundled_skills_root(bundle_root: Path) -> Path:
    """定位 PyInstaller 包中随附的内置技能目录。"""

    candidates = [
        bundle_root / "_internal" / "app" / "builtin_skills",
        bundle_root / "app" / "builtin_skills",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def template_files(root: Path) -> dict[Path, bytes]:
    """读取模板清单和 TSX 源码，以便比对源码与冻结资源。"""

    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name in {"manifest.json", "index.tsx"}
    }


def main() -> None:
    """在暂存后端前校验内置技能和页面模板资源完整一致。"""

    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_bundled_skills.py <pyinstaller-bundle-root>")

    bundle_root = Path(sys.argv[1]).expanduser().resolve()
    skills_root = resolve_bundled_skills_root(bundle_root)
    templates_root = bundle_root / "_internal" / "app" / "page_templates"
    try:
        validate_required_builtin_skills(skills_root)
        validate_page_templates(SOURCE_PAGE_TEMPLATES_DIR)
        validate_page_templates(templates_root)
        if template_files(SOURCE_PAGE_TEMPLATES_DIR) != template_files(templates_root):
            raise RuntimeError("打包页面模板与前端源码不一致。")
    except (OSError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Bundled built-in skills verified at {skills_root}")
    print(f"Bundled page templates verified at {templates_root}")


if __name__ == "__main__":
    main()
