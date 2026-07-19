from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services import user_skill_settings, user_skills


class UserSkillSettingsTests(unittest.TestCase):
    """验证用户技能启用状态的持久化和目录投影。"""

    def test_missing_settings_defaults_existing_skills_to_enabled(self) -> None:
        """确认无设置文件时兼容现有的默认开启行为。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            skills_root = Path(temporary_root) / "environment" / "skills"
            self._write_skill(skills_root / "sample", "sample")

            catalog = user_skills.list_user_skills(skills_root)

            self.assertTrue(catalog.skills[0].enabled)
            self.assertFalse(
                user_skill_settings.resolve_skill_settings_path(skills_root).exists()
            )

    def test_disabled_state_is_atomic_and_persists_in_environment_root(self) -> None:
        """确认关闭状态写入环境目录并能被后续目录扫描恢复。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            skills_root = Path(temporary_root) / "environment" / "skills"
            self._write_skill(skills_root / "sample", "sample")

            user_skills.update_user_skill_enabled(
                "sample/SKILL.md",
                False,
                root=skills_root,
            )
            settings_path = user_skill_settings.resolve_skill_settings_path(skills_root)
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            catalog = user_skills.list_user_skills(skills_root)

            self.assertEqual(settings_path.parent, skills_root.parent)
            self.assertEqual(payload["schemaVersion"], 1)
            self.assertEqual(payload["disabledSkills"], ["sample/SKILL.md"])
            self.assertFalse(catalog.skills[0].enabled)

            user_skills.update_user_skill_enabled(
                "sample/SKILL.md",
                True,
                root=skills_root,
            )
            self.assertTrue(user_skills.list_user_skills(skills_root).skills[0].enabled)

    def test_environment_roots_keep_enablement_isolated(self) -> None:
        """确认不同运行环境不会共享用户技能开关。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            base = Path(temporary_root)
            first_root = base / "dev" / "skills"
            second_root = base / "uat" / "skills"
            self._write_skill(first_root / "sample", "sample")
            self._write_skill(second_root / "sample", "sample")

            user_skills.update_user_skill_enabled(
                "sample/SKILL.md",
                False,
                root=first_root,
            )

            self.assertFalse(user_skills.list_user_skills(first_root).skills[0].enabled)
            self.assertTrue(user_skills.list_user_skills(second_root).skills[0].enabled)

    def test_invalid_settings_fail_closed(self) -> None:
        """确认损坏设置不会静默恢复为全部开启。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            skills_root = Path(temporary_root) / "environment" / "skills"
            self._write_skill(skills_root / "sample", "sample")
            settings_path = user_skill_settings.resolve_skill_settings_path(skills_root)
            settings_path.write_text("{invalid", encoding="utf-8")

            with self.assertRaises(user_skill_settings.SkillSettingsError):
                user_skills.list_user_skills(skills_root)

    def test_absolute_disabled_path_fails_closed(self) -> None:
        """确认状态文件不能借助绝对路径越过用户技能目录。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            skills_root = Path(temporary_root) / "environment" / "skills"
            self._write_skill(skills_root / "sample", "sample")
            settings_path = user_skill_settings.resolve_skill_settings_path(skills_root)
            settings_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "disabledSkills": ["/SKILL.md"],
                        "updatedAt": "2026-07-19T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(user_skill_settings.SkillSettingsError):
                user_skills.list_user_skills(skills_root)

    @staticmethod
    def _write_skill(directory: Path, name: str) -> None:
        """写入满足目录扫描要求的最小用户技能。"""

        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Sample skill\n"
            "---\n\n"
            "# Sample\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
