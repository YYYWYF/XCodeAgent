from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import builtin_skills


class BuiltinSkillsTests(unittest.TestCase):
    def test_backend_skill_uses_progressive_references(self) -> None:
        """后端数据源 Skill 入口保持精简，执行细节按来源延迟加载。"""

        root = builtin_skills.validate_required_builtin_skills()
        skill_root = root / builtin_skills.SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME
        entrypoint = (skill_root / "SKILL.md").read_text(encoding="utf-8")

        self.assertLess(len(entrypoint), 10_000)
        for relative_path in (
            "references/database/bootstrap.md",
            "references/database/layer-implementation.md",
            "references/external-api/bootstrap.md",
            "references/external-api/layer-implementation.md",
        ):
            self.assertIn(relative_path, entrypoint)
            self.assertIn(
                relative_path,
                builtin_skills.REQUIRED_BUILTIN_SKILL_FILES[
                    builtin_skills.SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME
                ],
            )
            self.assertTrue((skill_root / relative_path).is_file())
            reference = (skill_root / relative_path).read_text(encoding="utf-8")
            self.assertFalse(
                any("\u4e00" <= character <= "\u9fff" for character in reference)
            )
        self.assertFalse(
            any("\u4e00" <= character <= "\u9fff" for character in entrypoint)
        )

    def test_backend_skill_routes_sources_with_shared_document_structure(self) -> None:
        """单一后端 Skill 按数据源路由结构一致的 Bootstrap 和业务引用。"""

        root = builtin_skills.validate_required_builtin_skills()
        skill_root = root / builtin_skills.SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME
        entrypoint = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        entrypoint_headings = (
            "## Execution Boundaries",
            "## Data Source and Mode Routing",
            "### Database",
            "### External API",
        )
        bootstrap_headings = (
            "## Execution Scope",
            "## Dependency Baseline",
            "## Project Configuration",
            "## Completion Criteria",
        )
        layer_headings = (
            "## Execution Scope",
            "## Authoritative Inputs and Scope",
            "## Required Implementation Sequence",
            "## Layer Responsibilities",
            "## Template `common` Infrastructure",
            "## Mapping and Pagination",
            "## Error and Safety Behavior",
            "## Java 8 and Project Constraints",
            "## Completion Criteria",
        )

        actual_entrypoint_headings = tuple(
            line
            for line in entrypoint.splitlines()
            if line.startswith("## ") or line.startswith("### ")
        )
        boundary_section = entrypoint.split("## Execution Boundaries", 1)[1].split(
            "## Data Source and Mode Routing", 1
        )[0]
        normalized_entrypoint = " ".join(entrypoint.split())

        self.assertEqual(entrypoint_headings, actual_entrypoint_headings)
        self.assertEqual(
            6,
            sum(1 for line in boundary_section.splitlines() if line.startswith("- ")),
        )
        self.assertIn("`data_source_type: database`", entrypoint)
        self.assertIn("`data_source_type: external_api`", entrypoint)
        self.assertIn(
            "only authority for implementation semantics",
            entrypoint,
        )
        self.assertIn(
            "authoritative execution constraints",
            entrypoint,
        )
        self.assertNotIn("only business authority", entrypoint)
        self.assertIn(
            "Derive the set of source types from all entity designs",
            entrypoint,
        )
        self.assertIn(
            "Do not collapse a mixed task into a single source type",
            normalized_entrypoint,
        )
        source_classification = normalized_entrypoint.replace("**", "")
        self.assertIn(
            "Classify each referenced entity design from its confirmed "
            "`data_source_type`",
            source_classification,
        )
        self.assertIn(
            "Database means the entity's business data is read from or written to the "
            "application's bound database",
            source_classification,
        )
        self.assertIn(
            "External API means the entity's business data is obtained from or changed "
            "through an outbound call to a confirmed upstream operation",
            source_classification,
        )
        self.assertIn(
            "exposes an HTTP Controller or Endpoint does not make the source an external "
            "API",
            source_classification,
        )
        self.assertIn(
            "Mixed means the referenced entity-design set contains at least one "
            "`database` source and at least one `external_api` source",
            source_classification,
        )
        self.assertIn(
            "Choose the mode by its responsibility, not by the "
            "`implementation_contract.kind` value alone",
            normalized_entrypoint,
        )
        self.assertIn(
            "Bootstrap is a project-level dependency and shared-capability check",
            normalized_entrypoint.replace("**", ""),
        )
        self.assertIn(
            "Endpoint Implementation implements one confirmed endpoint operation",
            normalized_entrypoint.replace("**", ""),
        )
        self.assertIn(
            "must agree with this boundary",
            normalized_entrypoint,
        )
        self.assertIn("stable `database` then `external_api` order", entrypoint)
        for result_name in (
            "already_satisfied",
            "contract_mismatch",
            "change_request",
        ):
            self.assertIn(result_name, entrypoint)
        for source_directory in ("database", "external-api"):
            bootstrap = (
                skill_root / f"references/{source_directory}/bootstrap.md"
            ).read_text(encoding="utf-8")
            layers = (
                skill_root / f"references/{source_directory}/layer-implementation.md"
            ).read_text(encoding="utf-8")
            bootstrap_positions = [
                bootstrap.index(heading) for heading in bootstrap_headings
            ]
            layer_positions = [layers.index(heading) for heading in layer_headings]
            self.assertEqual(sorted(bootstrap_positions), bootstrap_positions)
            self.assertEqual(sorted(layer_positions), layer_positions)

    def test_backend_skills_are_execution_only_and_external_api_prefers_openfeign(self) -> None:
        """后端 Skill 只约束执行阶段，外部 API 新实现优先使用 OpenFeign。"""

        root = builtin_skills.validate_required_builtin_skills()
        backend_root = root / builtin_skills.SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME
        backend_skill = (backend_root / "SKILL.md").read_text(encoding="utf-8")
        external_layers = (
            backend_root / "references/external-api/layer-implementation.md"
        ).read_text(encoding="utf-8")
        external_bootstrap = (
            backend_root / "references/external-api/bootstrap.md"
        ).read_text(encoding="utf-8")

        self.assertIn("implementation_contract", backend_skill)
        self.assertIn("allowed_paths", backend_skill)
        self.assertNotIn("任务规划阶段", backend_skill)
        self.assertNotIn("Task sequencing", backend_skill)
        self.assertLess(len(backend_skill), 10_000)
        self.assertIn("Spring Cloud OpenFeign", backend_skill)
        self.assertIn("RestTemplate", backend_skill)
        self.assertIn("@FeignClient", external_layers)
        self.assertIn("base_url_config_key", external_layers)
        self.assertIn("request_shape", external_layers)
        self.assertIn("mapped_entity_path", external_layers)
        self.assertIn("BigDecimal", external_layers)
        self.assertIn("not authorize pagination by themselves", external_layers)
        self.assertIn("`${ENV_NAME:default}`", external_layers)
        for external_reference in (
            "references/external-api/layer-implementation.md",
            "references/external-api/bootstrap.md",
        ):
            self.assertIn(
                external_reference,
                builtin_skills.REQUIRED_BUILTIN_SKILL_FILES[
                    builtin_skills.SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME
                ],
            )
            self.assertTrue((backend_root / external_reference).is_file())
        self.assertIn("spring-cloud.version` = `2021.0.3", external_bootstrap)
        self.assertIn("spring-cloud-starter-openfeign", external_bootstrap)
        self.assertIn("spring-cloud-dependencies", external_bootstrap)
        self.assertIn("EnableFeignClients", external_bootstrap)

    def test_backend_skills_reuse_injected_common_infrastructure(self) -> None:
        """数据库与外部 API Skill 都必须复用只读模板 common 基础设施。"""

        root = builtin_skills.validate_required_builtin_skills()
        backend_root = root / builtin_skills.SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME
        backend_skill = (backend_root / "SKILL.md").read_text(encoding="utf-8")
        layers = (
            backend_root / "references/database/layer-implementation.md"
        ).read_text(encoding="utf-8")
        bootstrap = (backend_root / "references/database/bootstrap.md").read_text(
            encoding="utf-8"
        )
        external_layers = (
            backend_root / "references/external-api/layer-implementation.md"
        ).read_text(encoding="utf-8")
        normalized_layers = tuple(
            " ".join(content.split()) for content in (layers, external_layers)
        )

        self.assertIn("as read-only dependencies", backend_skill)
        for content in normalized_layers:
            self.assertIn("ResponseEntity", content)
            self.assertIn("response_schema_ref", content)
            self.assertIn("business body type", content)
            self.assertIn("not the HTTP JSON root", content)
            self.assertIn("returnCode", content)
            self.assertIn("separate fixed transport contract", content)
            self.assertIn("PageParam", content)
            self.assertIn("PageResult", content)
            self.assertIn("BizException", content)
            self.assertIn("BaseExceptionHandler", content)
            self.assertIn("org.springframework.http.ResponseEntity", content)
            self.assertIn("@CrossOrigin", content)
            self.assertIn("allowed_paths", content)
        self.assertIn("must not modify, copy, or regenerate", bootstrap)

    def test_frontend_skill_requires_shared_response_entity_unwrap(self) -> None:
        """前端边界 Skill 必须固定真实接口的公共响应解包规则。"""

        root = builtin_skills.validate_required_builtin_skills()
        content = (
            root / "frontend-template-modification-boundary" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("src/apis/responseEntity.ts", content)
        self.assertIn("ResponseEntityBusinessError", content)
        self.assertIn("ResponseEntityProtocolError", content)
        self.assertIn("unwrapResponseEntity<T>()", content)
        self.assertIn("unwrapEmptyResponseEntity()", content)
        self.assertIn("SUC0000", content)
        self.assertIn("response.data", content)
        self.assertIn("不得导入", content)

    def test_source_tree_skills_are_available_and_complete(self) -> None:
        """确认源码内置技能完整且能生成页面卡片元数据。"""

        root = builtin_skills.validate_required_builtin_skills()

        self.assertIn(
            builtin_skills.REACT_DEV_SPEC_SKILL_NAME,
            builtin_skills.available_builtin_skills(root),
        )
        available = builtin_skills.available_builtin_skills(root)
        self.assertIn(
            builtin_skills.SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME,
            available,
        )
        self.assertNotIn("springboot-mybatis-generate", available)
        self.assertNotIn("springboot-external-api-generate", available)
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
