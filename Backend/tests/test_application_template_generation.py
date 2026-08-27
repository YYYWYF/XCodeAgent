import tempfile
import unittest
from pathlib import Path

from app.services.application_template_generation import (
    ApplicationTemplateGenerationError,
    begin_application_template_deletion,
    inspect_template_generation_readiness,
    prepare_application_template_generation,
    validate_application_template_generation,
)


class ApplicationTemplateGenerationTests(unittest.TestCase):
    """验证 main 与 auth 模板初始化严格按下载分支隔离。"""

    def _workspace(self, root: Path) -> None:
        """创建满足 auth 前端模板契约的最小工作区。"""
        (root / "frontend/src/constants").mkdir(parents=True)
        (root / "frontend/package.json").write_text("{}", encoding="utf-8")
        (root / "frontend/src/constants/resources.ts").write_text("export const RESOURCES = { SYSTEM: {} } as const;\n", encoding="utf-8")
        (root / "frontend/src/constants/routes.tsx").write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
        (root / "backend").mkdir()
        (root / "backend/pom.xml").write_text("<project/>", encoding="utf-8")

    def _download(self, branch: str = "auth") -> dict:
        """返回成功下载的最小结果。"""
        return {"targets": {"frontend": {"status": "succeeded", "attempt": 1, "branch": branch}, "backend": {"status": "succeeded", "attempt": 1, "branch": branch}}}

    def test_contract_gate_does_not_create_business_pages(self) -> None:
        """业务页面必须留给后续前端 Build 任务创建。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            manifest = prepare_application_template_generation(root, self._download())
            validate_application_template_generation(root)
            self.assertEqual(manifest["steps"]["templateContract"]["status"], "succeeded")
            self.assertTrue(inspect_template_generation_readiness(root)["ready"])
            self.assertFalse((root / "frontend/src/pages").exists())

    def test_contract_gate_rejects_missing_markers(self) -> None:
        """模板缺少托管区时不得进入 Build。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            (root / "frontend/src/constants/routes.tsx").write_text("export {};", encoding="utf-8")
            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "托管标记"):
                prepare_application_template_generation(root, self._download())

    def test_rejects_mixed_template_branches(self) -> None:
        """前后端分支不匹配时不能静默选择任一生成流程。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            download = self._download("auth")
            download["targets"]["backend"]["branch"] = "main"
            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "分支不一致"):
                prepare_application_template_generation(root, download)

    def test_main_keeps_page_and_menu_initialization(self) -> None:
        """main 模板不依赖 auth 插槽，仍生成占位页和 BIZ_MENUS。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "frontend/src/constants").mkdir(parents=True)
            (root / "frontend/package.json").write_text("{}", encoding="utf-8")
            (root / "frontend/src/constants/menus.ts").write_text("export const BIZ_MENUS = [];\n", encoding="utf-8")
            (root / "backend").mkdir()
            (root / "backend/pom.xml").write_text("<project/>", encoding="utf-8")
            (root / ".xcodeagent/plans").mkdir(parents=True)
            (root / ".xcodeagent/specs").mkdir(parents=True)
            (root / ".xcodeagent/plans/product-plan.json").write_text('{"confirmation_status":"confirmed","schema_version":"product-plan.v6","pages":[{"pageId":"dashboard","name":"首页","path":"/dashboard"}]}', encoding="utf-8")
            (root / ".xcodeagent/specs/ui-designs.json").write_text('{"schema_version":"ui-manifest.v3","confirmation_status":"skipped"}', encoding="utf-8")
            manifest = prepare_application_template_generation(root, self._download("main"))
            validate_application_template_generation(root)
            self.assertEqual(manifest["templateVariant"], "main")
            self.assertTrue((root / "frontend/src/pages/Dashboard/index.tsx").is_file())
            self.assertIn('key: "Dashboard"', (root / "frontend/src/constants/menus.ts").read_text(encoding="utf-8"))

    def test_deletion_fence_rejects_new_template_writes(self) -> None:
        """应用开始删除后不得再次执行模板初始化或完成门禁写入。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            begin_application_template_deletion(root)

            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "正在删除"):
                prepare_application_template_generation(root, self._download())


if __name__ == "__main__":
    unittest.main()
