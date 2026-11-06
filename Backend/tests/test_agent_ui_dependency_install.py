"""验证目标依赖准备不会覆盖宿主栈或把声明误认为实际安装。"""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from app.services.agent_ui_dependency_plan import REPOSITORY, prepare_dependency_plan, safe_path
from app.services.agent_ui_dependency_install import execute_dependency_plan, process_run_id, read_installation, record_directory
from app.services.agent_ui_dependency_evidence import installed_evidence
from app.services.workspace_process_registry import workspace_process_registry


class AgentUiCommandWarningTests(unittest.TestCase):
    """核对实际包管理器日志中的 peer 提示会进入安装摘要。"""

    def test_pnpm_peer_summary_is_recorded(self) -> None:
        """覆盖 pnpm 11 的汇总文案与既有逐项警告，普通日志不误报。"""
        from subprocess import CompletedProcess
        from app.services.agent_ui_dependency_install import _command

        for log, expected in (
            ('[WARN] Issues with peer dependencies found. Run "pnpm peers check" to list them.', True),
            ('unmet peer react@^18: found 19.0.0', True),
            ('Already up to date\nDone in 5.4s using pnpm v11.9.0', False),
        ):
            with self.subTest(log=log), TemporaryDirectory() as temporary:
                target = Path(temporary)

                def run(argv: list[str], **options: object) -> CompletedProcess:
                    """向执行器真实捕获通道写入已复现的管理器输出。"""
                    options['stdout'].write(log.encode())
                    return CompletedProcess(argv, 0)

                with patch.object(workspace_process_registry, 'run', side_effect=run):
                    result = _command(['pnpm', 'install'], target=target,
                        plan={'workspace': temporary}, run_id='peer-warning-test',
                        directory=target, stage='install')
                self.assertEqual(result['peerWarnings'], expected)
                self.assertEqual((target / 'install.log').read_text(), log)


class AgentUiDependencyPlanTests(unittest.TestCase):
    """用独立 npm/pnpm 目标验证计划、路径和现有定制保护。"""

    def setUp(self) -> None:
        """创建含中文和空格的真实消费者目录。"""
        self.temp = TemporaryDirectory(prefix="Agent UI 目标 ")
        self.workspace = Path(self.temp.name).resolve()
        self.target = self.workspace / "frontend"
        (self.target / "src").mkdir(parents=True)
        self.package = {"name": "consumer", "private": True, "type": "module",
                        "packageManager": "pnpm@9.15.9", "dependencies": {
                            "react": "18.2.0", "react-dom": "18.2.0", "antd": "4.24.16"},
                        "scripts": {"build": "vite build", "typecheck": "tsc --noEmit"}}
        self.write_package()
        (self.target / "src/main.tsx").write_text("import './business.css'\n", encoding="utf-8")
        (self.target / "index.html").write_text('<script type="module" src="/src/main.tsx"></script>')

    def tearDown(self) -> None:
        """释放独立夹具。"""
        self.temp.cleanup()

    def write_package(self) -> None:
        """写入当前测试声明。"""
        (self.target / "package.json").write_text(json.dumps(self.package), encoding="utf-8")

    def plan(self, **options: str) -> dict:
        """生成已授权本地发布物的只读计划。"""
        return prepare_dependency_plan(self.workspace, source="local-vendor", **options)

    def test_plan_is_read_only_and_preserves_host(self) -> None:
        """首次安装只提供可审阅差异，宿主 major 和其他声明保持。"""
        before = (self.target / "package.json").read_bytes()
        plan = self.plan()
        self.assertEqual((self.target / "package.json").read_bytes(), before)
        after = json.loads(next(item["after"] for item in plan["changes"] if item["path"] == "package.json"))
        for name in ("react", "react-dom", "antd"):
            self.assertEqual(after["dependencies"][name], self.package["dependencies"][name])
        self.assertEqual(plan["manager"], "pnpm")
        self.assertEqual(len(plan["packages"]), 4)
        self.assertEqual(plan['sourceDependencies'], [
            {'name': 'lucide-react', 'version': '1.41.0'}, {'name': 'zod', 'version': '4.5.4'}])
        self.assertEqual(after['dependencies']['lucide-react'], '1.41.0')
        self.assertEqual(after['dependencies']['zod'], '4.5.4')
        self.assertFalse((self.workspace / ".devagentstudio").exists())
        self.assertTrue(any(item["path"] == "src/agent-ui-package.css" for item in plan["changes"]))

    def test_plan_changes_when_source_or_lock_changes(self) -> None:
        """源码与锁变化都使旧批准失效。"""
        first = self.plan()["planId"]
        (self.target / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        self.assertNotEqual(first, self.plan()["planId"])
        first = self.plan()["planId"]
        (self.target / "src/main.tsx").write_text("// user edit\n", encoding="utf-8")
        self.assertNotEqual(first, self.plan()["planId"])

    def test_rejects_workspace_and_symlink(self) -> None:
        """不把独立目标安装接口用于 workspace 或链接工程。"""
        (self.workspace / "pnpm-workspace.yaml").write_text("packages: ['frontend']")
        with self.assertRaisesRegex(ValueError, "workspace"):
            self.plan()
        (self.workspace / "pnpm-workspace.yaml").unlink()
        (self.target / "src/link").symlink_to(self.workspace, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "链接"):
            self.plan()

    def test_root_only_pnpm_policy_file_is_preserved(self) -> None:
        """只声明当前包的 pnpm 构建策略不能被误判为多包 workspace，也不能被改写。"""
        path = self.target / 'pnpm-workspace.yaml'
        content = "packages: ['.']\nallowBuilds:\n  esbuild: true\nstrictDepBuilds: true\n"
        path.write_text(content)
        self.assertEqual(self.plan()['manager'], 'pnpm')
        self.assertEqual(path.read_text(), content)

    def test_nested_or_invalid_pnpm_workspace_is_rejected(self) -> None:
        """允许根包策略不放宽子包、通配符、坏配置或链接的拒绝边界。"""
        path = self.target / 'pnpm-workspace.yaml'
        for content in ("packages: ['.', 'packages/*']", "packages: ['*']", "packages: '.'", "[]", "packages: ["):
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    self.plan()
        path.unlink()
        outside = self.workspace / 'policy.yaml'
        outside.write_text("packages: ['.']")
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '链接'):
            self.plan()

    def test_rejects_stack_upgrade_and_style_takeover(self) -> None:
        """Tailwind 3 或已有未知样式配置不能被自动接管。"""
        self.package["devDependencies"] = {"tailwindcss": "^3.4.0"}
        self.write_package()
        with self.assertRaisesRegex(ValueError, "Tailwind"):
            self.plan()
        self.package.pop("devDependencies")
        self.write_package()
        (self.target / "postcss.config.mjs").write_text("export default { plugins: {} }")
        with self.assertRaisesRegex(ValueError, "PostCSS"):
            self.plan()

    def test_existing_template_postcss_and_typechecked_build_are_preserved(self) -> None:
        """模板已有 Tailwind 4/autoprefixer 和构建内类型检查时，不重写配置或添加脚本。"""
        self.package["scripts"] = {"build": "tsc -b && rsbuild build"}
        self.package["devDependencies"] = {"tailwindcss": "^4.3.0", "@tailwindcss/postcss": "^4.2.1",
                                           "autoprefixer": "^10.4.20"}
        self.write_package()
        path = self.target / "postcss.config.js"
        content = "export default {\n plugins: { '@tailwindcss/postcss': {}, autoprefixer: {}, },\n};\n"
        path.write_text(content)
        (self.target / "rsbuild.config.ts").write_text("export default {source: {entry: {index: './src/main.tsx'}}}")
        plan = self.plan()
        self.assertEqual(plan["checks"], ["build"])
        self.assertFalse(any(item["path"].startswith("postcss.config") for item in plan["changes"]))
        package = json.loads(next(item["after"] for item in plan["changes"] if item["path"] == "package.json"))
        self.assertEqual(package["scripts"], self.package["scripts"])
        self.assertEqual(path.read_text(), content)

    def test_postcss_dynamic_duplicate_and_unknown_plugins_still_require_review(self) -> None:
        """只接受已知静态插件空选项，不能执行配置、合并未知插件或接受无效 ESM。"""
        path = self.target / "postcss.config.js"
        for content in (
            "export default { plugins: { '@tailwindcss/postcss': {}, custom: {} } }",
            "export default { plugins: { '@tailwindcss/postcss': {}, '@tailwindcss/postcss': {} } }",
            "export default { plugins: { '@tailwindcss/postcss': {}, autoprefixer: {grid: true} } }",
            "export default { plugins: { '@tailwindcss/postcss': {} } }; run()",
            "export default { plugins: { '@tailwindcss/ postcss': {} } }",
        ):
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaisesRegex(ValueError, "PostCSS"):
                    self.plan()
        path.write_text("export default { plugins: { '@tailwindcss/postcss': {} } }")
        self.package.pop("type")
        self.write_package()
        with self.assertRaisesRegex(ValueError, "PostCSS"):
            self.plan()

    def test_build_without_typecheck_is_not_accepted(self) -> None:
        """没有独立类型检查时只接受当前模板已明确串联 tsc 的构建，不猜测任意脚本。"""
        for build in ("rsbuild build", "tsc -b; rsbuild build", "tsc -b || rsbuild build", "echo tsc -b && rsbuild build"):
            with self.subTest(build=build):
                self.package["scripts"] = {"build": build}
                self.write_package()
                with self.assertRaisesRegex(ValueError, "typecheck"):
                    self.plan()

    def test_npm_lock_selects_npm_without_replacing_lock(self) -> None:
        """有锁 npm 目标保持原管理器。"""
        self.package["packageManager"] = "npm@10.8.2"
        self.write_package()
        (self.target / "package-lock.json").write_text('{"lockfileVersion":3}')
        self.assertEqual(prepare_dependency_plan(self.workspace, source="registry")["lockfile"], "package-lock.json")
        with self.assertRaisesRegex(ValueError, "registry"):
            self.plan()

    def test_rsbuild_layout_uses_declared_entry_and_output(self) -> None:
        """源码中存在其他入口时也必须以当前构建配置为准，规划不能写入文件。"""
        self.package["scripts"] = {"build": "tsc -b && rsbuild build"}
        self.write_package()
        (self.target / "src/index.tsx").write_text("// current entry\n")
        config = self.target / "rsbuild.config.ts"
        config.write_text("import { defineConfig } from '@rsbuild/core';\nexport default defineConfig({"
                          "source: {entry: {index: './src/index.tsx'}}, output: {distPath: {root: 'build'}}});")
        plan = self.plan()
        self.assertEqual(plan["entry"], "src/index.tsx")
        self.assertEqual(plan["outputDir"], "build")
        changes = {item["path"]: item["after"] for item in plan["changes"]}
        self.assertIn("src/index.tsx", changes)
        self.assertNotIn("src/main.tsx", changes)
        self.assertEqual((self.target / "src/index.tsx").read_text(), "// current entry\n")
        first = plan["planId"]
        config.write_text(config.read_text().replace("'build'", "'output'"))
        self.assertNotEqual(first, self.plan()["planId"])

    def test_explicit_entry_cannot_override_build_entry(self) -> None:
        """调用者不能通过指定存在但未参与构建的文件制造成功计划。"""
        (self.target / "index.html").write_text('<script type="module" src="/src/main.tsx"></script>')
        (self.target / "src/unused.tsx").write_text("// unused\n")
        with self.assertRaisesRegex(ValueError, "入口"):
            self.plan(entry="src/unused.tsx")

    def test_stylesheet_evidence_uses_reviewed_output_directory(self) -> None:
        """样式证据必须来自审阅输出，不能误用 dist 中的旧构建。"""
        from app.services.agent_ui_dependency_evidence import stylesheet_evidence
        (self.target / "build").mkdir()
        (self.target / "build/current.css").write_text(":root {--aui-current: 1}")
        (self.target / "dist").mkdir()
        (self.target / "dist/old.css").write_text(":root {--aui-old: 1}")
        evidence = stylesheet_evidence(self.target, output_dir="build")
        self.assertEqual([item["path"] for item in evidence], ["build/current.css"])

    def test_rsbuild_installation_and_refresh_validate_actual_output(self) -> None:
        """安装和后续读取均核验本轮 build 样式，不能仍要求 dist。"""
        self.package["scripts"] = {"build": "tsc -b && rsbuild build"}
        self.write_package()
        (self.target / "src/index.tsx").write_text("// entry\n")
        (self.target / "rsbuild.config.ts").write_text(
            "export default {source: {entry: {index: './src/index.tsx'}}, output: {distPath: {root: 'build'}}}")
        plan = self.plan()
        with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
             patch("app.services.agent_ui_dependency_install._command", side_effect=self.fake_command):
            result = self.execute(plan)
        self.assertEqual(result["state"], "verified", result.get("message"))
        self.assertEqual(result["styles"][0]["path"], "build/styles.css")
        self.assertEqual(read_installation(self.workspace, plan["planId"])["state"], "verified")
        (self.target / "build/styles.css").write_text(":root {--aui-manual: 2}")
        self.assertEqual(read_installation(self.workspace, plan["planId"])["state"], "stale")

    def test_custom_output_is_not_treated_as_source_mutation(self) -> None:
        """当前配置声明的自定义输出可完成验证，生成的 CSS 不污染源码快照。"""
        (self.target / "vite.config.ts").write_text(
            "export default {plugins: ['@tailwindcss/vite'], build: {outDir: 'artifacts/client'}}")
        self.package["devDependencies"] = {"@tailwindcss/vite": "^4.3.0"}
        self.write_package()
        (self.target / "artifacts/client").mkdir(parents=True)
        (self.target / "artifacts/client/old.css").write_text(":root {--aui-old: 1}")
        plan = self.plan()
        self.assertNotIn("artifacts/client/old.css", plan["before"])
        with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
             patch("app.services.agent_ui_dependency_install._command", side_effect=self.fake_command):
            result = self.execute(plan)
        self.assertEqual(result["state"], "verified", result.get("message"))
        self.assertEqual(read_installation(self.workspace, plan["planId"])["state"], "verified")

    def test_rsbuild_postcss_override_is_repaired_in_reviewed_diff(self) -> None:
        """模板的浅合并覆盖必须在计划中移除，不能假定存在配置文件就会加载插件。"""
        self.package["scripts"] = {"build": "tsc -b && rsbuild build"}
        self.write_package()
        path = self.target / "rsbuild.config.ts"
        content = """import path from 'node:path';
export default {source: {entry: {index: './src/main.tsx'}}, tools: {
 postcss: {postcssOptions: {config: path.resolve(projectRoot, 'postcss.config.js')}},
 // Existing host customization stays intact.
 rspack: (config) => {return config;},
}};
"""
        path.write_text(content)
        (self.target / 'postcss.config.js').write_text(
            "export default {plugins: {'@tailwindcss/postcss': {}, autoprefixer: {}}}")
        plan = self.plan()
        changes = {item['path']: item['after'] for item in plan['changes']}
        self.assertIn('rsbuild.config.ts', changes)
        self.assertNotIn('postcssOptions', changes['rsbuild.config.ts'])
        self.assertIn('rspack: (config) => {return config;}', changes['rsbuild.config.ts'])
        self.assertIn('// Existing host customization stays intact.', changes['rsbuild.config.ts'])
        self.assertNotIn('postcss.config.js', changes)
        self.assertEqual(path.read_text(), content)

    def fake_command(self, _argv: list[str], **kwargs: object) -> dict:
        """用真实磁盘 metadata/锁/CSS 夹具替代联网命令，保留其验收边界。"""
        stage = kwargs["stage"]
        plan = kwargs["plan"]
        if stage == "install":
            lock = {"lockfileVersion": "9.0", "importers": {".": {"dependencies": {}}}, "packages": {}}
            for item in plan["packages"]:
                artifact = (self.target / item["specifier"][5:]).resolve()
                with tarfile.open(artifact) as archive:
                    metadata = archive.extractfile("package/package.json").read()
                target = self.target / "node_modules" / item["name"] / "package.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(metadata)
                lock["importers"]["."]["dependencies"][item["name"]] = {
                    "specifier": item["specifier"], "version": item["specifier"]}
                lock["packages"][item["name"] + "@" + item["specifier"]] = {
                    "resolution": {"integrity": item["integrity"], "tarball": item["specifier"]}}
            for item in plan['sourceDependencies']:
                target = self.target / 'node_modules' / item['name'] / 'package.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(item))
                lock['importers']['.']['dependencies'][item['name']] = {
                    'specifier': item['version'], 'version': item['version']}
                lock['packages'][item['name'] + '@' + item['version']] = {
                    'resolution': {'integrity': 'sha512-source-fixture'}}
            (self.target / "pnpm-lock.yaml").write_text(yaml.safe_dump(lock), encoding="utf-8")
        if stage == "build":
            output = self.target / plan["outputDir"]
            output.mkdir(parents=True, exist_ok=True)
            (output / "styles.css").write_text(":root { --aui-test: 1; }")
        return {"stage": stage, "returncode": 0}

    def execute(self, plan: dict, **options: str) -> dict:
        """只批准当前真实计划，工具进程由用例选择是否替代。"""
        return execute_dependency_plan(self.workspace, plan_id=plan["planId"], source="local-vendor",
                                       entry=plan["entry"], thread_id="install-test", run_id="install-test-run", **options)

    def test_executes_install_then_checks_and_replay_does_not_install(self) -> None:
        """只有实际安装、锁及样式均匹配才 verified，重复请求不重新运行。"""
        plan = self.plan()
        with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
             patch("app.services.agent_ui_dependency_install._command", side_effect=self.fake_command) as command:
            result = self.execute(plan)
            self.assertEqual(result["state"], "verified", result.get("message"))
            self.assertEqual([call.kwargs["stage"] for call in command.call_args_list], ["install", "typecheck", "build"])
            self.assertEqual(self.execute(plan)["state"], "verified")
            self.assertEqual(command.call_count, 3)
        self.assertTrue((record_directory(self.workspace, plan["planId"]) / "before/package.json").is_file())

    def test_stale_plan_never_starts_tools(self) -> None:
        """批准后有用户修改时在任何写入和命令之前停止。"""
        plan = self.plan()
        (self.target / "src/main.tsx").write_text("// manual edit")
        with patch("app.services.agent_ui_dependency_install._tools") as tools:
            with self.assertRaisesRegex(ValueError, "过期"):
                self.execute(plan)
        tools.assert_not_called()
        self.assertEqual((self.target / "src/main.tsx").read_text(), "// manual edit")

    def test_failed_install_is_not_rolled_back_or_automatically_replayed(self) -> None:
        """缺包/安装失败保留声明与恢复证据，不声称副作用被恢复。"""
        plan = self.plan()
        with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
             patch("app.services.agent_ui_dependency_install._command", return_value={"stage": "install", "returncode": 1}) as command:
            result = self.execute(plan)
            self.assertEqual(result["state"], "failed")
            self.assertIn("install", result["message"])
            self.assertTrue(result["applied"])
            self.assertEqual(self.execute(plan)["state"], "failed")
            command.assert_called_once()

    def test_concurrent_edit_is_preserved_and_rejects_completion(self) -> None:
        """安装中修改保护源码不能被完成结果掩盖。"""
        plan = self.plan()
        def command(argv: list[str], **kwargs: object) -> dict:
            """在命令成功后模拟独立编辑者写入。"""
            result = self.fake_command(argv, **kwargs)
            (self.target / "src/business.ts").write_text("// new manual work")
            return result
        with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
             patch("app.services.agent_ui_dependency_install._command", side_effect=command):
            result = self.execute(plan)
        self.assertEqual(result["state"], "failed")
        self.assertEqual((self.target / "src/business.ts").read_text(), "// new manual work")

    def test_cancel_stops_before_build_and_keeps_actual_files(self) -> None:
        """取消只停止当前命令链，已经写入的文件和锁均保留。"""
        plan = self.plan()
        def command(argv: list[str], **kwargs: object) -> dict:
            """模拟安装完成边界收到停止请求。"""
            result = self.fake_command(argv, **kwargs)
            workspace_process_registry.cancel_run(process_run_id(self.workspace, "install-test-run"))
            return result
        try:
            with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
                 patch("app.services.agent_ui_dependency_install._command", side_effect=command) as run:
                result = self.execute(plan)
            self.assertEqual(result["state"], "stopped")
            self.assertEqual(run.call_count, 1)
            self.assertTrue((self.target / "pnpm-lock.yaml").exists())
            self.assertEqual(read_installation(self.workspace, plan["planId"])["state"], "stopped")
        finally:
            workspace_process_registry.allow_run(process_run_id(self.workspace, "install-test-run"))

    def test_lock_integrity_tampering_is_rejected(self) -> None:
        """同名同版本不足以证明安装来自已选 tarball。"""
        plan = self.plan()
        self.fake_command([], stage="install", plan=plan)
        path = self.target / "pnpm-lock.yaml"
        lock = yaml.safe_load(path.read_text())
        next(iter(lock["packages"].values()))["resolution"]["integrity"] = "sha512-other"
        path.write_text(yaml.safe_dump(lock))
        with self.assertRaisesRegex(ValueError, "integrity"):
            installed_evidence(self.target, plan)

    def test_source_dependency_is_direct_and_verified_after_install(self) -> None:
        """四个包已安装也不能掩盖官方应用层依赖缺失或被替换。"""
        plan = self.plan()
        with patch('app.services.agent_ui_dependency_install._tools', return_value=[sys.executable, __file__]), \
             patch('app.services.agent_ui_dependency_install._command', side_effect=self.fake_command):
            result = self.execute(plan)
        self.assertEqual(result['state'], 'verified', result.get('message'))
        self.assertEqual(len(result['installation']['sourcePackages']), 2)
        path = self.target / 'node_modules/lucide-react/package.json'
        path.write_text('{"name":"lucide-react","version":"0.0.1"}')
        with self.assertRaisesRegex(ValueError, 'lucide-react'):
            installed_evidence(self.target, plan)
        self.assertEqual(read_installation(self.workspace, plan['planId'])['state'], 'stale')

    def test_existing_source_dependency_customization_is_not_overwritten(self) -> None:
        """已有其他版本或开发声明不自动升级、降级或移动到生产字段。"""
        self.package['dependencies']['zod'] = '^3.0.0'
        self.write_package()
        with self.assertRaisesRegex(ValueError, 'zod'):
            self.plan()
        self.package['dependencies'].pop('zod')
        self.package['devDependencies'] = {'zod': '4.5.4'}
        self.write_package()
        with self.assertRaisesRegex(ValueError, 'zod'):
            self.plan()

    def test_windows_and_traversal_paths_are_rejected(self) -> None:
        """在真实文件操作前拒绝盘符、ADS、保留名和路径穿越。"""
        for path in ("../outside.tsx", "/absolute.tsx", "C:/src/main.tsx", "C:main.tsx",
                     "src\\main.tsx", "src/main.tsx:stream", "src/CON.tsx", "src/trailing."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(self.target, path)

    def test_verified_read_detects_later_manual_edit(self) -> None:
        """历史 verified 不能冒充当前源码仍通过。"""
        plan = self.plan()
        with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
             patch("app.services.agent_ui_dependency_install._command", side_effect=self.fake_command):
            self.assertEqual(self.execute(plan)["state"], "verified")
        (self.target / "src/main.tsx").write_text("// later user edit")
        self.assertEqual(read_installation(self.workspace, plan["planId"])["state"], "stale")

    def test_new_review_can_retry_without_replaying_old_confirmation(self) -> None:
        """同一输入的新审阅与旧请求不同，原请求重连仍不重复安装。"""
        first = prepare_dependency_plan(self.workspace, source="local-vendor", review_id="a" * 32)
        second = prepare_dependency_plan(self.workspace, source="local-vendor", review_id="b" * 32)
        self.assertNotEqual(first["planId"], second["planId"])
        self.assertEqual(first["changes"], second["changes"])

    def test_npm_registry_lock_identity(self) -> None:
        """npm v3 锁必须同时匹配根声明、resolved 和 tarball integrity。"""
        self.package["packageManager"] = "npm@10.8.2"
        self.write_package()
        # 本地发布物只用于制造真实包 metadata；正式 npm 计划仍是 registry 精确版本。
        plan = prepare_dependency_plan(self.workspace, source="registry")
        for item in plan["packages"]:
            artifact = REPOSITORY / "vendor/agent-ui" / ("za38.03-" + item["name"].split("/")[-1] + "-" + item["version"] + ".tgz")
            with tarfile.open(artifact) as archive:
                metadata = archive.extractfile("package/package.json").read()
            path = self.target / "node_modules" / item["name"] / "package.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(metadata)
        lock = {"lockfileVersion": 3, "packages": {"": {"dependencies": {
            item["name"]: item["version"] for item in plan["packages"]}}}}
        for item in plan["packages"]:
            lock["packages"]["node_modules/" + item["name"]] = {
                "version": item["version"], "integrity": item["integrity"],
                "resolved": "https://registry.example/" + item["name"] + ".tgz"}
        for item in plan['sourceDependencies']:
            path = self.target / 'node_modules' / item['name'] / 'package.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(item))
            self.package['dependencies'][item['name']] = item['version']
            lock['packages']['']['dependencies'][item['name']] = item['version']
            lock['packages']['node_modules/' + item['name']] = {
                'version': item['version'], 'integrity': 'sha512-source-fixture'}
        self.write_package()
        (self.target / "package-lock.json").write_text(json.dumps(lock))
        self.assertEqual(len(installed_evidence(self.target, plan)["packages"]), 4)

    def test_formal_change_during_install_prevents_completion(self) -> None:
        """并发正式规划变更使安装验证失效，不推进新规划。"""
        plan = self.plan()
        def command(argv: list[str], **kwargs: object) -> dict:
            """模拟安装成功后提交了新的应用配置。"""
            result = self.fake_command(argv, **kwargs)
            path = self.workspace / ".devagentstudio/application.json"
            path.write_text('{"changed":true}')
            return result
        with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
             patch("app.services.agent_ui_dependency_install._command", side_effect=command):
            result = self.execute(plan)
        self.assertEqual(result["state"], "failed")
        self.assertIn("正式规划", result["message"])

    def test_existing_lock_backup_preserves_recovery_bytes(self) -> None:
        """安装更新锁之前必须保存原字节，不能只保存旧摘要。"""
        before = b"lockfileVersion: '9.0'\n# original lock\n"
        (self.target / "pnpm-lock.yaml").write_bytes(before)
        plan = self.plan()
        with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
             patch("app.services.agent_ui_dependency_install._command", side_effect=self.fake_command):
            self.assertEqual(self.execute(plan)["state"], "verified")
        self.assertEqual((record_directory(self.workspace, plan["planId"]) / "before/pnpm-lock.yaml").read_bytes(), before)

    def test_vite_without_official_adapter_is_blocked_before_changes(self) -> None:
        """Vite 提前内联 CSS 时不能套用普通 PostCSS 配置并误报样式准备成功。"""
        (self.target / "vite.config.ts").write_text("export default {}")
        with self.assertRaisesRegex(ValueError, "官方 Vite"):
            self.plan()

    def test_react_range_cannot_authorize_multiple_majors(self) -> None:
        """宿主 React 范围不能在安装时悄悄选择另一主版本。"""
        self.package["dependencies"]["react"] = "^18.2.0 || ^19.0.0"
        self.write_package()
        with self.assertRaisesRegex(ValueError, "React"):
            self.plan()


if __name__ == "__main__":
    unittest.main()
