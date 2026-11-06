"""验证设计转入计划只读、保留目标定制且不消费过期正式产物。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.services.agent_ui_dependency_plan import target_snapshot
from app.services.agent_ui_design_generator import _host_files
from app.services.agent_ui_design_transfer_plan import prepare_transfer_plan
from app.services.agent_ui_transfer_files import rewrite_files, scan_imports, source_files
from tests.agent_ui_transfer_fixtures import transfer_fixture, verify_target
from tests.test_agent_ui_design_generator import formal_facts
from app.services.ui_design_agent_surfaces import project_agent_design_pages


class TransferPlanTests(unittest.TestCase):
    """使用真实目录与当前凭据覆盖源码交接边界。"""

    def setUp(self) -> None:
        """每个用例获得隔离的已确认设计和已验证目标。"""
        self.temp = TemporaryDirectory(prefix="T22 中文 ")
        self.workspace = Path(self.temp.name).resolve()
        self.source, self.target, self.installation = transfer_fixture(str(self.workspace))

    def tearDown(self) -> None:
        """释放本用例创建的临时目录。"""
        self.temp.cleanup()

    def plan(self, **options: str) -> dict:
        """只请求当前目标的只读计划。"""
        return prepare_transfer_plan(self.workspace, installation_plan_id=self.installation,
                                     target_source_root="src/助手", **options)

    def test_new_target_plan_is_read_only_and_rewrites_imports(self) -> None:
        """路径不同仍保留应用层语义，只列初始化前置且不复制内部状态。"""
        before = target_snapshot(self.workspace)
        plan = self.plan()
        self.assertEqual(before, target_snapshot(self.workspace))
        self.assertTrue(plan["initialization"]["required"])
        self.assertTrue(plan["initialization"]["requiresReplan"])
        by_source = {item["sourcePath"]: item for item in plan["changes"]}
        self.assertIn("src/agent-ui/app-ui.json", by_source)
        projects = by_source["src/design/Agent.tsx"]
        self.assertEqual(projects["targetPath"], "src/助手/design/Agent.tsx")
        self.assertIn("../application/project-config", projects["after"])
        self.assertTrue(projects["importAdjustments"])
        self.assertFalse(any("plugins/" in path or "services/" in path or ".agent-ui" in path for path in by_source))
        self.assertTrue(plan["publicApi"]["catalogSha256"])
        self.assertTrue(plan["executionAvailable"])
        self.assertEqual(plan["status"], "reviewable", plan["blockers"])
        self.assertEqual(plan["planId"], self.plan()["planId"])

    def test_generated_result_observer_modules_are_in_transfer_scope(self) -> None:
        """生成器的只读结果模块完整进入转入清单，真实模块导入可在目标重定位。"""
        files = _host_files(project_agent_design_pages(*formal_facts()))
        for name in ('runtimeResults.ts', 'useRuntimeResult.ts'):
            (self.source / 'src/design' / name).write_text(files['src/design/' + name])
        mapping, _ = source_files(self.source, 'src/agent-ui', 'src/助手')
        for name in ('runtimeResults.ts', 'useRuntimeResult.ts'):
            self.assertEqual(mapping['src/design/' + name], 'src/助手/design/' + name)
        rewritten, blockers, _ = rewrite_files(self.source, self.target, mapping,
            [('src/agent-ui', 'src/助手')], self.workspace)
        self.assertEqual(blockers, [])
        self.assertIn("from './runtimeResults'", rewritten['src/design/useRuntimeResult.ts']['after'])

    def test_target_edit_invalidates_installation_without_overwriting(self) -> None:
        """已验证后发生人工修改，必须重新验证，不能沿用旧安装批准。"""
        path = self.target / "src/main.tsx"
        path.write_text("// user customization\n")
        with self.assertRaisesRegex(ValueError, "安装|过期"):
            self.plan()
        self.assertEqual(path.read_text(), "// user customization\n")

    def test_unconfirmed_design_is_rejected(self) -> None:
        """正式状态变化不能因已有生成回执绕过确认。"""
        path = self.workspace / ".devagentstudio/specs/ui-designs.json"
        path.write_text(path.read_text().replace('"confirmed"', '"pending_user_confirmation"'))
        with self.assertRaisesRegex(ValueError, "确认|过期|安装"):
            self.plan()

    def test_existing_customization_is_a_conflict_and_base_is_protected(self) -> None:
        """已初始化工程不重置；相同文件无操作，不同文件留给用户审阅。"""
        config = self.target / ".agent-ui"
        config.mkdir()
        (config / "project.json").write_text('{"sourceRoot":"src/助手"}')
        (config / "source-lock.json").write_text('{"sourceRoot":"src/助手","items":{"custom":{"files":{}}}}')
        path = self.target / "src/助手/app-ui.json"
        path.parent.mkdir()
        path.write_text("user customized model")
        style = path.with_name("styles.css")
        style.write_bytes((self.source / "src/agent-ui/styles.css").read_bytes())
        self.installation = verify_target(self.workspace)
        before = target_snapshot(self.target)
        result = self.plan()
        self.assertFalse(result["initialization"]["required"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("src/助手/app-ui.json", result["conflicts"])
        self.assertEqual(next(item for item in result["changes"] if item["targetPath"] == "src/助手/styles.css")["operation"], "unchanged")
        self.assertEqual(result["protected"][".agent-ui/source-lock.json"], before[".agent-ui/source-lock.json"])
        self.assertEqual(target_snapshot(self.target), before)

    def test_source_root_mismatch_cannot_reset_existing_project(self) -> None:
        """用户已有工具记录不能被另一 sourceRoot 的计划覆盖。"""
        path = self.target / ".agent-ui/project.json"
        path.parent.mkdir()
        path.write_text('{"sourceRoot":"src/custom"}')
        self.installation = verify_target(self.workspace)
        with self.assertRaisesRegex(ValueError, "sourceRoot"):
            self.plan()

    def test_invalid_paths_and_case_collision_are_rejected(self) -> None:
        """Windows/Unicode/大小写安全不依赖执行机平台。"""
        for name in ("../outside", "C:/temp/ui", "src/CON", "src/助手:stream", "src/a/../b", "src/a\\b", "src/.agent-ui", "src/BASE"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                prepare_transfer_plan(self.workspace, installation_plan_id=self.installation, target_source_root=name)
        (self.target / "src/Ui").mkdir()
        self.installation = verify_target(self.workspace)
        with self.assertRaisesRegex(ValueError, "大小写"):
            prepare_transfer_plan(self.workspace, installation_plan_id=self.installation, target_source_root="src/ui")

    def test_symbolic_link_is_rejected(self) -> None:
        """未初始化路径也不能沿链接写入工程外。"""
        (self.target / "src/助手").symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "链接"):
            self.plan()

    def test_markdown_edits_are_not_confirmed_by_planning(self) -> None:
        """Markdown 修改保留，既有确认状态不能授权未同步的新内容。"""
        path = self.workspace / ".devagentstudio/plans/technical-plan.md"
        path.write_text(path.read_text() + "\n用户尚未确认的修改\n")
        with self.assertRaisesRegex(ValueError, "Markdown"):
            self.plan()

    def test_concurrent_target_edit_rejects_mixed_snapshot(self) -> None:
        """读取期间人工写入使整份计划失效，保留该写入。"""
        def scanning(*args: object) -> dict:
            """在真实 AST 扫描完成后模拟另一个编辑者写入。"""
            result = scan_imports(*args)
            (self.target / "src/main.tsx").write_text("// concurrent edit\n")
            return result
        with patch("app.services.agent_ui_transfer_files.scan_imports", side_effect=scanning):
            with self.assertRaisesRegex(ValueError, "变化"):
                self.plan()
        self.assertEqual((self.target / "src/main.tsx").read_text(), "// concurrent edit\n")

    def test_changed_installed_resources_cannot_reuse_metadata_identity(self) -> None:
        """相同版本/锁/metadata 不足以证明实际包资源与确认设计相同。"""
        path = self.target / "node_modules/@za38.03/agent-ui-plugins/dist/catalog.json"
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "资源不同"):
            self.plan()

    def test_import_analysis_preserves_strings_and_reports_unconsumable_modules(self) -> None:
        """AST 不改展示文字，私有模块、缺依赖和动态路径不能静默遗漏。"""
        path = self.source / "src/design/Agent.tsx"
        code = ("const label = '中文😀 ../agent-ui/application/project-config'\n"
                "// import fake from '../ignored'\n"
                "export { agentUIProject } from '../agent-ui/application/project-config'\n"
                "import secret from '@za38.03/agent-ui-plugins/private'\n"
                "import missingApi from '@za38.03/agent-ui-runtime/ui/missing-api'\n"
                "import other from 'missing-package'\n"
                "const module = import(label)\n")
        path.write_text(code)
        mapping, _ = source_files(self.source, "src/agent-ui", "src/助手")
        rewritten, blockers, _ = rewrite_files(self.source, self.target, mapping,
            [("src/agent-ui", "src/助手")], self.workspace)
        after = rewritten["src/design/Agent.tsx"]["after"]
        self.assertIn("'中文😀 ../agent-ui/application/project-config'", after)
        self.assertIn('"../application/project-config"', after)
        self.assertTrue(any("未公开" in message for message in blockers))
        self.assertTrue(any("missing-api" in message for message in blockers))
        self.assertTrue(any("missing-package" in message for message in blockers))
        self.assertTrue(any("computed" in message for message in blockers))

    def test_generated_references_are_deferred_and_raw_requires_target_adapter(self) -> None:
        """注册/Runtime 配置由目标工具生成，构建器差异必须列为阻断。"""
        path = self.source / "src/agent-ui/application/project-config.tsx"
        path.write_text("import raw from '../app-ui.json?raw'\nimport { ProjectShell } from './runtime-config.generated'\nexport const agentUIProject = { raw, ProjectShell }\n")
        mapping, _ = source_files(self.source, "src/agent-ui", "src/助手")
        _, blockers, generated = rewrite_files(self.source, self.target, mapping,
            [("src/agent-ui", "src/助手")], self.workspace)
        self.assertTrue(any("显式适配" in message for message in blockers))
        self.assertTrue(any(item["reference"] == "./runtime-config.generated" for item in generated))

    def test_page_variants_keep_model_identity_and_local_styles(self) -> None:
        """逐页定制与公开组件 props 保持原字节；CSS 本地依赖随显式映射转入。"""
        root = self.source / "src/design/agent-pages/page-1"
        (root / "application").mkdir(parents=True)
        (root / "app-ui.json").write_text('{"id":"keep-plugin-instance"}')
        (root / "application/custom.css").write_text('.custom { color: inherit; }')
        (root / "styles.css").write_text('@import "./application/custom.css";')
        (root / "application/secrets.json").write_text('{"private":"fixture-only"}')
        with self.assertRaisesRegex(ValueError, '独立.*重新生成'):
            source_files(self.source, "src/agent-ui", "src/助手")
        self.assertEqual((root / 'app-ui.json').read_text(), '{"id":"keep-plugin-instance"}')

    def test_independent_variant_blocks_execution_before_initialization(self) -> None:
        """未接入独立组合发布的变体仍可审阅，但不允许部分写入后才失败。"""
        (self.source / "src/design/agent-pages/page-1").mkdir(parents=True)
        before = target_snapshot(self.target)
        with self.assertRaisesRegex(ValueError, '独立.*重新生成'):
            self.plan()
        self.assertEqual(target_snapshot(self.target), before)

    def test_official_plugin_wrapper_preserves_project_props(self) -> None:
        """明确的公开包薄包装层纳入审阅，包定义与实现仍不复制。"""
        root = self.source / "src/agent-ui/plugins/composer"
        (root / "application.json").write_text('{"schemaVersion":1,"pluginId":"composer"}')
        (root / "definition.ts").write_text('export { definition } from "@za38.03/agent-ui-plugins/composer"')
        (root / "index.tsx").write_text('export const props = { showSearch: false }')
        mapping, _ = source_files(self.source, "src/agent-ui", "src/助手")
        self.assertIn("src/agent-ui/plugins/composer/index.tsx", mapping)
        self.assertNotIn("src/agent-ui/plugins/composer/definition.ts", mapping)
