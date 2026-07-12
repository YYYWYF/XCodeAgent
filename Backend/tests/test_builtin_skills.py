from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import builtin_skills


class BuiltinSkillsTests(unittest.TestCase):
    def test_source_tree_skills_are_available_and_complete(self) -> None:
        root = builtin_skills.validate_required_builtin_skills()

        self.assertIn(
            builtin_skills.REACT_ANTD_V4_SKILL_NAME,
            builtin_skills.available_builtin_skills(root),
        )

    def test_environment_override_controls_skill_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root).resolve()
            self._write_required_skill_files(root)

            with patch.dict(
                os.environ,
                {builtin_skills.BUILTIN_SKILLS_DIR_ENV: str(root)},
            ):
                self.assertEqual(builtin_skills.resolve_builtin_skills_root(), root)
                self.assertEqual(
                    builtin_skills.validate_required_builtin_skills(),
                    root,
                )

    def test_frozen_root_is_used_when_module_relative_data_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            frozen_root = Path(temporary_root).resolve()
            skills_root = frozen_root / "app" / "builtin_skills"
            self._write_required_skill_files(skills_root)
            missing_module = frozen_root / "missing" / "app" / "services" / "builtin_skills.py"

            with (
                patch.dict(os.environ, {}, clear=False),
                patch.object(builtin_skills, "__file__", str(missing_module)),
                patch.object(builtin_skills.sys, "_MEIPASS", str(frozen_root), create=True),
            ):
                os.environ.pop(builtin_skills.BUILTIN_SKILLS_DIR_ENV, None)
                self.assertEqual(
                    builtin_skills.resolve_builtin_skills_root(),
                    skills_root,
                )

    def test_validation_reports_missing_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            with self.assertRaisesRegex(RuntimeError, "SKILL.md"):
                builtin_skills.validate_required_builtin_skills(Path(temporary_root))

    @staticmethod
    def _write_required_skill_files(root: Path) -> None:
        for path in builtin_skills.required_builtin_skill_paths(root):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
