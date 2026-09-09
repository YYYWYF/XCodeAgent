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
from app.services.frontend_scaffold import ensure_frontend_menu_entries


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
            (root / ".xcodeagent/plans/product-plan.json").write_text('{"confirmation_status":"confirmed","schema_version":"product-plan.v5","pages":[{"pageId":"dashboard","name":"首页","path":"/dashboard"}]}', encoding="utf-8")
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


class FrontendMenuSyncTests(unittest.TestCase):
    """验证 BIZ_MENUS 顶层菜单项随 ProductPlan 页面集合同步增删。"""

    _TEMPLATE_MENUS = (
        "import { Route } from '@/typings/workbench';\n\n"
        "export const BIZ_MENUS: Route[] = [\n"
        "  {\n"
        "    path: 'firstLevel',\n"
        "    name: '一级目录',\n"
        "    icon: 'https://x.png',\n"
        "    children: [\n"
        "      { path: 'default', name: '默认页面', key: 'DefaultPage' }\n"
        "    ]\n"
        "  },\n"
        "  { path: 'https://www.baidu.com', name: '外部链接', target: '_blank' },\n"
        "  { path: '/page/projects', name: '项目列表', key: 'ProjectList' },\n"
        "  { path: '/page/projects/:id', name: '项目详情', key: 'ProjectDetail', hideInMenu: true },\n"
        "];\n"
    )

    def _workspace_with_menus(self) -> tuple[Path, Path]:
        directory = tempfile.mkdtemp()
        root = Path(directory)
        (root / "frontend/src/constants").mkdir(parents=True)
        menus = root / "frontend/src/constants/menus.ts"
        menus.write_text(self._TEMPLATE_MENUS, encoding="utf-8")
        return root, menus

    def test_removes_orphaned_page_menu_entries(self) -> None:
        """ProductPlan 已移除的页面菜单项应从顶层删除。"""

        root, menus = self._workspace_with_menus()
        pages = [{"key": "ProjectList", "name": "项目列表", "path": "/page/projects"}]
        result = ensure_frontend_menu_entries(root / "frontend", pages)
        content = menus.read_text(encoding="utf-8")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["removedKeys"], ["ProjectDetail"])
        self.assertNotIn("ProjectDetail", content)
        self.assertIn("ProjectList", content)

    def test_appends_missing_page_menu_entries(self) -> None:
        """ProductPlan 新增的页面菜单项应追加到顶层末尾。"""

        root, menus = self._workspace_with_menus()
        pages = [
            {"key": "ProjectList", "name": "项目列表", "path": "/page/projects"},
            {"key": "Orders", "name": "订单列表", "path": "/page/orders"},
        ]
        result = ensure_frontend_menu_entries(root / "frontend", pages)
        content = menus.read_text(encoding="utf-8")
        self.assertEqual(result["injectedKeys"], ["Orders"])
        self.assertIn('key: "Orders"', content)

    def test_preserves_directory_children_and_external_links(self) -> None:
        """带 children 的目录项、无 key 的外部链接、DefaultPage 不得删除。"""

        root, menus = self._workspace_with_menus()
        pages = [{"key": "ProjectList", "name": "项目列表", "path": "/page/projects"}]
        ensure_frontend_menu_entries(root / "frontend", pages)
        content = menus.read_text(encoding="utf-8")
        self.assertIn("firstLevel", content)
        self.assertIn("DefaultPage", content)
        self.assertIn("www.baidu.com", content)

    def test_idempotent_on_repeated_sync(self) -> None:
        """连续两次同步应产生相同文件，第二次无增删。"""

        root, menus = self._workspace_with_menus()
        pages = [{"key": "ProjectList", "name": "项目列表", "path": "/page/projects"}]
        first = ensure_frontend_menu_entries(root / "frontend", pages)
        content_after_first = menus.read_text(encoding="utf-8")
        second = ensure_frontend_menu_entries(root / "frontend", pages)
        self.assertEqual(second["injectedKeys"], [])
        self.assertEqual(second["removedKeys"], [])
        self.assertEqual(menus.read_text(encoding="utf-8"), content_after_first)

    def test_default_page_not_removed_when_product_plan_empty(self) -> None:
        """ProductPlan 清空所有业务页面时仍保留 DefaultPage。"""

        root, menus = self._workspace_with_menus()
        result = ensure_frontend_menu_entries(root / "frontend", [])
        content = menus.read_text(encoding="utf-8")
        self.assertIn("DefaultPage", content)
        self.assertNotIn("ProjectList", content)
        self.assertNotIn("ProjectDetail", content)
        self.assertEqual(result["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
