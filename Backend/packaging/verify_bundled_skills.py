from __future__ import annotations

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builtin_skills import validate_required_builtin_skills  # noqa: E402


def resolve_bundled_skills_root(bundle_root: Path) -> Path:
    candidates = [
        bundle_root / "_internal" / "app" / "builtin_skills",
        bundle_root / "app" / "builtin_skills",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_bundled_skills.py <pyinstaller-bundle-root>")

    bundle_root = Path(sys.argv[1]).expanduser().resolve()
    skills_root = resolve_bundled_skills_root(bundle_root)
    try:
        validate_required_builtin_skills(skills_root)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Bundled built-in skills verified at {skills_root}")


if __name__ == "__main__":
    main()
