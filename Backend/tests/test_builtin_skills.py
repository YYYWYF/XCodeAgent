"""验证旧资源清理后的技能目录与必需资源保护。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.builtin_skills import (
    REQUIRED_BUILTIN_SKILL_FILES,
    available_builtin_skills,
    list_builtin_skills,
    required_builtin_skill_paths,
    validate_required_builtin_skills,
)


class BuiltinSkillResourceTests(unittest.TestCase):
    """确认清理仅移除旧技能，剩余资源仍严格校验。"""

    def test_current_catalog_excludes_obsolete_skills(self) -> None:
        """真实目录和公开列表不再包含旧技能，专用技能仍保留。"""
        root = validate_required_builtin_skills()
        names = set(available_builtin_skills(root))
        public_names = {skill.directory_name for skill in list_builtin_skills(root)}
        for name in ("react-develop-specification", "code-block-template",
                     "frontend-template-modification-boundary"):
            self.assertFalse((root / name).exists())
            self.assertNotIn(name, names)
            self.assertNotIn(name, public_names)
            self.assertNotIn(name, REQUIRED_BUILTIN_SKILL_FILES)
        for name in ("frontend-engineering-standards", "frontend-static-data-generate",
                     "frontend-pnpm-install-repair", "frontend-code-scan", "antd-ui-design"):
            self.assertIn(name, names)
            self.assertIn(name, public_names)

    def test_every_remaining_required_resource_is_enforced(self) -> None:
        """逐一移除临时资源，确认缺少任一剩余必需文件仍会报错。"""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = required_builtin_skill_paths(root)
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("resource", encoding="utf-8")
            self.assertEqual(validate_required_builtin_skills(root), root.resolve())
            for path in paths:
                with self.subTest(path=path.relative_to(root).as_posix()):
                    path.unlink()
                    with self.assertRaises(RuntimeError) as raised:
                        validate_required_builtin_skills(root)
                    self.assertIn(path.relative_to(root).as_posix(), str(raised.exception))
                    path.write_text("resource", encoding="utf-8")
