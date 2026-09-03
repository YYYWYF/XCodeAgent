"""确定性后端骨架注入生成器的单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.template_scaffold_injection import (
    inject_deterministic_backend_skeleton,
    prebuilt_files_for_plan,
    _to_pascal_case,
    _to_camel_case,
    _to_snake_case,
    _module_name,
    _java_type,
)


def _sample_technical_plan() -> dict:
    """构造一个包含两个实体的最小 TechnicalPlan。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "entities": [
            {
                "id": "Project",
                "name": "Project",
                "fields": [
                    {"name": "project_id", "type": "text", "required": True},
                    {"name": "project_name", "type": "text", "required": True},
                    {"name": "budget", "type": "decimal", "required": False},
                    {"name": "status", "type": "enum", "required": True, "enum_values": ["active", "done"]},
                    {"name": "created_at", "type": "datetime", "required": True},
                ],
            },
            {
                "id": "ProjectMember",
                "name": "ProjectMember",
                "fields": [
                    {"name": "member_id", "type": "text", "required": True},
                    {"name": "member_name", "type": "text", "required": True},
                ],
            },
        ],
        "api_contracts": [
            {
                "id": "project_api",
                "entity_ids": ["Project", "ProjectMember"],
                "endpoints": [
                    {"id": "project_api.list", "method": "GET", "path": "/api/projects"},
                    {"id": "project_api.get", "method": "GET", "path": "/api/projects/{projectId}"},
                    {"id": "project_api.create", "method": "POST", "path": "/api/projects"},
                    {"id": "project_api.update", "method": "PUT", "path": "/api/projects/{projectId}"},
                    {"id": "project_api.delete", "method": "DELETE", "path": "/api/projects/{projectId}"},
                ],
            },
        ],
    }


class NamingConversionTests(unittest.TestCase):
    """命名转换工具函数。"""

    def test_pascal_case(self) -> None:
        self.assertEqual(_to_pascal_case("project_member"), "ProjectMember")
        self.assertEqual(_to_pascal_case("project"), "Project")
        self.assertEqual(_to_pascal_case("order-item"), "OrderItem")

    def test_camel_case(self) -> None:
        self.assertEqual(_to_camel_case("project_name"), "projectName")
        self.assertEqual(_to_camel_case("project"), "project")

    def test_snake_case(self) -> None:
        self.assertEqual(_to_snake_case("projectName"), "project_name")
        self.assertEqual(_to_snake_case("ProjectMember"), "project_member")

    def test_module_name(self) -> None:
        self.assertEqual(_module_name("Project"), "project")
        self.assertEqual(_module_name("ProjectMember"), "project")
        self.assertEqual(_module_name("OrderItem"), "order")

    def test_java_type(self) -> None:
        self.assertEqual(_java_type("text"), "String")
        self.assertEqual(_java_type("integer"), "Integer")
        self.assertEqual(_java_type("decimal"), "BigDecimal")
        self.assertEqual(_java_type("boolean"), "Boolean")
        self.assertEqual(_java_type("datetime"), "LocalDateTime")
        self.assertEqual(_java_type("unknown"), "String")


class PrebuiltFilesTests(unittest.TestCase):
    """prebuilt_files_for_plan 清单生成。"""

    def test_returns_all_files_for_entity(self) -> None:
        plan = _sample_technical_plan()
        files = prebuilt_files_for_plan(plan)
        # 2 个实体 × 10 文件 = 20
        self.assertEqual(len(files), 20)
        # Project 的文件
        self.assertIn(
            "backend/src/main/java/com/cmbchina/backend/project/domain/entity/Project.java",
            files,
        )
        self.assertIn(
            "backend/src/main/java/com/cmbchina/backend/project/adapter/web/ProjectController.java",
            files,
        )
        # ProjectMember 归到 project 模块
        self.assertIn(
            "backend/src/main/java/com/cmbchina/backend/project/domain/entity/ProjectMember.java",
            files,
        )

    def test_empty_plan_returns_empty(self) -> None:
        self.assertEqual(prebuilt_files_for_plan({}), [])
        self.assertEqual(prebuilt_files_for_plan({"entities": []}), [])


class InjectSkeletonTests(unittest.TestCase):
    """inject_deterministic_backend_skeleton 端到端写入。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        # 模拟 backend 目录
        (Path(self.tmpdir) / "backend" / "src" / "main" / "java").mkdir(parents=True)
        (Path(self.tmpdir) / "backend" / "src" / "main" / "resources").mkdir(parents=True)

    def test_injects_all_files(self) -> None:
        plan = _sample_technical_plan()
        result = inject_deterministic_backend_skeleton(self.tmpdir, plan)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["fileCount"], 20)
        self.assertEqual(result["moduleCount"], 1)  # Project + ProjectMember 都归 project

    def test_idempotent_second_injection(self) -> None:
        """第二次注入应全部 skip，不重复写入。"""

        plan = _sample_technical_plan()
        first = inject_deterministic_backend_skeleton(self.tmpdir, plan)
        self.assertEqual(len(first["files"]), 20)
        self.assertEqual(len(first["skipped"]), 0)
        second = inject_deterministic_backend_skeleton(self.tmpdir, plan)
        self.assertEqual(len(second["files"]), 0)
        self.assertEqual(len(second["skipped"]), 20)

    def test_skips_when_no_backend_dir(self) -> None:
        """backend 目录不存在时 skip。"""

        import shutil
        shutil.rmtree(Path(self.tmpdir) / "backend")
        result = inject_deterministic_backend_skeleton(self.tmpdir, _sample_technical_plan())
        self.assertEqual(result["status"], "skipped")

    def test_entity_has_no_duplicate_audit_fields(self) -> None:
        """PO 不应重复审计字段（实体已含 created_at 时）。"""

        plan = _sample_technical_plan()
        inject_deterministic_backend_skeleton(self.tmpdir, plan)
        po_path = (
            Path(self.tmpdir)
            / "backend"
            / "src"
            / "main"
            / "java"
            / "com"
            / "cmbchina"
            / "backend"
            / "project"
            / "infrastructure"
            / "po"
            / "ProjectPO.java"
        )
        content = po_path.read_text(encoding="utf-8")
        # createdAt 只出现一次（字段声明）
        self.assertEqual(content.count("private LocalDateTime createdAt;"), 1)
        self.assertEqual(content.count("private String createdBy;"), 1)

    def test_controller_has_correct_endpoints(self) -> None:
        """Controller 从 API Contract 推导端点。"""

        plan = _sample_technical_plan()
        inject_deterministic_backend_skeleton(self.tmpdir, plan)
        controller_path = (
            Path(self.tmpdir)
            / "backend"
            / "src"
            / "main"
            / "java"
            / "com"
            / "cmbchina"
            / "backend"
            / "project"
            / "adapter"
            / "web"
            / "ProjectController.java"
        )
        content = controller_path.read_text(encoding="utf-8")
        self.assertIn("@GetMapping", content)
        self.assertIn("@PostMapping", content)
        self.assertIn("@PutMapping", content)
        self.assertIn("@DeleteMapping", content)
        self.assertIn("/api/projects", content)

    def test_po_has_mybatis_annotations(self) -> None:
        """PO 有 MyBatis-Plus 注解和审计字段。"""

        plan = _sample_technical_plan()
        inject_deterministic_backend_skeleton(self.tmpdir, plan)
        po_path = (
            Path(self.tmpdir)
            / "backend"
            / "src"
            / "main"
            / "java"
            / "com"
            / "cmbchina"
            / "backend"
            / "project"
            / "infrastructure"
            / "po"
            / "ProjectPO.java"
        )
        content = po_path.read_text(encoding="utf-8")
        self.assertIn("@TableName(\"project\")", content)
        self.assertIn("@TableId(value = \"id\", type = IdType.AUTO)", content)
        self.assertIn("@TableLogic(value = \"0\", delval = \"1\")", content)
        self.assertIn("private Boolean isDeleted;", content)

    def test_mapper_xml_has_namespace(self) -> None:
        """Mapper XML 有正确的 namespace。"""

        plan = _sample_technical_plan()
        inject_deterministic_backend_skeleton(self.tmpdir, plan)
        xml_path = (
            Path(self.tmpdir)
            / "backend"
            / "src"
            / "main"
            / "resources"
            / "mapper"
            / "project"
            / "ProjectMapper.xml"
        )
        content = xml_path.read_text(encoding="utf-8")
        self.assertIn(
            "namespace=\"com.cmbchina.backend.project.infrastructure.mapper.ProjectMapper\"",
            content,
        )

    def test_converter_uses_mapstruct(self) -> None:
        """Converter 用 MapStruct 注解。"""

        plan = _sample_technical_plan()
        inject_deterministic_backend_skeleton(self.tmpdir, plan)
        converter_path = (
            Path(self.tmpdir)
            / "backend"
            / "src"
            / "main"
            / "java"
            / "com"
            / "cmbchina"
            / "backend"
            / "project"
            / "infrastructure"
            / "repository"
            / "converter"
            / "ProjectConverter.java"
        )
        content = converter_path.read_text(encoding="utf-8")
        self.assertIn("@Mapper(componentModel = \"spring\")", content)
        self.assertIn("Project toEntity(ProjectPO source);", content)
        self.assertIn("ProjectPO toPO(Project source);", content)

    def test_repository_impl_has_crud(self) -> None:
        """RepositoryImpl 有 CRUD 方法实现。"""

        plan = _sample_technical_plan()
        inject_deterministic_backend_skeleton(self.tmpdir, plan)
        impl_path = (
            Path(self.tmpdir)
            / "backend"
            / "src"
            / "main"
            / "java"
            / "com"
            / "cmbchina"
            / "backend"
            / "project"
            / "infrastructure"
            / "repository"
            / "impl"
            / "ProjectRepositoryImpl.java"
        )
        content = impl_path.read_text(encoding="utf-8")
        self.assertIn("selectPage", content)
        self.assertIn("selectOne", content)
        self.assertIn("insert", content)
        self.assertIn("softDelete", content)
        self.assertIn("LambdaUpdateWrapper", content)


if __name__ == "__main__":
    unittest.main()
