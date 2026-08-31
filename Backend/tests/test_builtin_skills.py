from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import builtin_skills


class BuiltinSkillsTests(unittest.TestCase):
    def test_mybatis_skill_uses_progressive_references(self) -> None:
        """MyBatis Skill 入口保持精简，执行细节通过必需引用延迟加载。"""

        root = builtin_skills.validate_required_builtin_skills()
        skill_root = root / builtin_skills.SPRINGBOOT_MYBATIS_GENERATE_SKILL_NAME
        entrypoint = (skill_root / "SKILL.md").read_text(encoding="utf-8")

        self.assertLess(len(entrypoint), 10_000)
        self.assertIn("references/layer-implementation.md", entrypoint)
        self.assertIn("references/bootstrap.md", entrypoint)
        for relative_path in (
            "references/layer-implementation.md",
            "references/bootstrap.md",
        ):
            self.assertIn(
                relative_path,
                builtin_skills.REQUIRED_BUILTIN_SKILL_FILES[
                    builtin_skills.SPRINGBOOT_MYBATIS_GENERATE_SKILL_NAME
                ],
            )
            self.assertTrue((skill_root / relative_path).is_file())

    def test_backend_skills_are_execution_only_and_external_api_requires_resttemplate(self) -> None:
        """后端 Skill 只约束执行阶段，外部 API 必须使用 RestTemplate。"""

        root = builtin_skills.validate_required_builtin_skills()
        mybatis = (
            root / builtin_skills.SPRINGBOOT_MYBATIS_GENERATE_SKILL_NAME / "SKILL.md"
        ).read_text(encoding="utf-8")
        external_api = (
            root
            / builtin_skills.SPRINGBOOT_EXTERNAL_API_GENERATE_SKILL_NAME
            / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("implementation_contract", mybatis)
        self.assertIn("allowed_paths", mybatis)
        self.assertNotIn("任务规划阶段", mybatis)
        self.assertNotIn("Task sequencing", external_api)
        self.assertIn("Every external request MUST use", external_api)
        self.assertIn("RestTemplate", external_api)
        self.assertIn("Do not use WebClient, Feign, OkHttp", external_api)
        self.assertIn("base_url_config_key", external_api)
        self.assertIn("request_shape", external_api)
        self.assertIn("mapped_entity_path", external_api)
        self.assertIn("BigDecimal", external_api)
        self.assertIn("Never hard-code example keywords", external_api)
        self.assertIn("themselves authorize page semantics", external_api)
        self.assertIn("Use a plain", external_api)
        self.assertIn("never wrap it in `${ENV_NAME:default}`", external_api)
        self.assertIn("product:", external_api)
        self.assertIn("url: http://99.17.197.63:8090", external_api)

    def test_source_tree_skills_are_available_and_complete(self) -> None:
        """确认源码内置技能完整且能生成页面卡片元数据。"""

        root = builtin_skills.validate_required_builtin_skills()

        self.assertIn(
            builtin_skills.REACT_DEV_SPEC_SKILL_NAME,
            builtin_skills.available_builtin_skills(root),
        )
        available = builtin_skills.available_builtin_skills(root)
        self.assertIn(
            builtin_skills.SPRINGBOOT_EXTERNAL_API_GENERATE_SKILL_NAME,
            available,
        )
        self.assertIn(
            builtin_skills.FRONTEND_STATIC_DATA_GENERATE_SKILL_NAME,
            available,
        )
        summaries = builtin_skills.list_builtin_skills(root)
        self.assertTrue(all(skill.description for skill in summaries))
        self.assertTrue(
            all(not skill.relative_path.startswith(str(root)) for skill in summaries)
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
