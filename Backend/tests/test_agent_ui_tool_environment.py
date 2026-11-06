"""验证项目工具链自动解析和 Creator 模型配置的最小进程边界。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.config import Settings
from app.services.agent_ui_tool_environment import resolve_design_tools
from app.services.agent_ui_creator_environment import creator_process_environment


class AgentUiToolEnvironmentTests(unittest.TestCase):
    """不安装依赖，使用隔离的 Corepack 缓存与可控进程回执。"""

    def setUp(self) -> None:
        """构造中文路径、当前 packageManager 和可运行文件占位。"""
        self.temp = TemporaryDirectory(prefix="工具自动配置 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.node = self.root / "node"
        self.node.touch()
        self.manifest = self.root / "package.json"
        self.manifest.write_text(json.dumps({"packageManager": "pnpm@9.15.9"}))
        self.cache = self.root / "cache"
        self.cli = self.cache / "v1/pnpm/9.15.9/bin/pnpm.cjs"
        self.env = patch.dict(os.environ, {"COREPACK_HOME": str(self.cache),
            "NODE_OPTIONS": "--require fixture-secret", "CREATOR_MODEL_API_KEY": "fixture-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def cached_cli(self) -> None:
        """模拟 Corepack 已缓存的精确版本，不执行安装。"""
        self.cli.parent.mkdir(parents=True, exist_ok=True)
        self.cli.touch()

    def test_cached_project_version_ignores_global_and_old_overrides(self) -> None:
        """项目版本优先，旧的 Agent UI 路径变量不参与当前解析。"""
        self.cached_cli()
        with patch.dict(os.environ, {"DEVAGENTSTUDIO_AGENT_UI_NODE": "/invalid/node",
                "DEVAGENTSTUDIO_AGENT_UI_PNPM_CLI": "/invalid/pnpm"}), \
                patch("app.services.agent_ui_tool_environment.shutil.which", return_value=str(self.node)), \
                patch("app.services.agent_ui_tool_environment.workspace_process_registry.run",
                    return_value=subprocess.CompletedProcess([], 0, "9.15.9\n", "")) as run:
            self.assertEqual(resolve_design_tools(self.root, self.manifest), (self.node, self.cli))
        self.assertEqual(run.call_args.args[0], [str(self.node), str(self.cli), "--version"])
        self.assertFalse({"NODE_OPTIONS", "CREATOR_MODEL_API_KEY"} & run.call_args.kwargs["env"].keys())

    def test_missing_default_node_has_actionable_error(self) -> None:
        """默认 Node 不存在时只提示安装正常工具链，不要求额外变量。"""
        with patch("app.services.agent_ui_tool_environment.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "Node") as caught:
                resolve_design_tools(self.root, self.manifest)
        self.assertNotIn("DEVAGENTSTUDIO_AGENT_UI_", str(caught.exception))

    def test_package_manager_must_be_exact(self) -> None:
        """工具链不能按 latest 或范围隐式升级。"""
        for value in ("pnpm@^9", "pnpm@latest", None, {"version": "9.15.9"}):
            with self.subTest(value=value):
                self.manifest.write_text(json.dumps({"packageManager": value}))
                with self.assertRaisesRegex(ValueError, "packageManager"):
                    resolve_design_tools(self.root, self.manifest)

    def test_cmd_wrapper_resolves_javascript_instead_of_running_shell(self) -> None:
        """Windows npm 包装只用于定位 JS，不能作为 shell/cmd 命令执行。"""
        wrapper = self.root / "pnpm.cmd"
        wrapper.touch()
        entry = self.root / "node_modules/pnpm/bin/pnpm.cjs"
        entry.parent.mkdir(parents=True)
        entry.touch()

        def locate(name: str) -> str | None:
            """模拟 Windows 全局命令目录的已安装 pnpm。"""
            return {"node": str(self.node), "pnpm": str(wrapper)}.get(name)

        with patch("app.services.agent_ui_tool_environment.shutil.which", side_effect=locate), \
                patch("app.services.agent_ui_tool_environment.workspace_process_registry.run",
                    return_value=subprocess.CompletedProcess([], 0, "9.15.9", "")) as run:
            self.assertEqual(resolve_design_tools(self.root, self.manifest), (self.node, entry))
        self.assertEqual(run.call_args.args[0], [str(self.node), str(entry), "--version"])

    def test_corepack_fetches_only_pinned_cache_without_changing_default(self) -> None:
        """缺版本时只填充 Corepack 缓存，后续直接使用精确 CLI。"""
        corepack = self.root / "corepack.js"
        corepack.touch()

        def locate(name: str) -> str | None:
            """不提供全局 pnpm，以覆盖标准 Corepack 缓存补齐路径。"""
            return {"node": str(self.node), "corepack": str(corepack)}.get(name)

        def execute(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            """模拟缓存安装成功后出现当前 CLI，禁止修改全局激活版本。"""
            if "install" in argv:
                self.assertEqual(argv, [str(self.node), str(corepack), "install", "--global", "--cache-only", "pnpm@9.15.9"])
                self.cached_cli()
                return subprocess.CompletedProcess(argv, 0, "cached", "")
            return subprocess.CompletedProcess(argv, 0, "9.15.9", "")

        with patch("app.services.agent_ui_tool_environment.shutil.which", side_effect=locate), \
                patch("app.services.agent_ui_tool_environment.workspace_process_registry.run", side_effect=execute):
            self.assertEqual(resolve_design_tools(self.root, self.manifest), (self.node, self.cli))

    def test_failed_install_never_echoes_process_output(self) -> None:
        """下载或 CLI 异常只显示受控文案，底层输出不泄露密钥。"""
        self.cached_cli()
        with patch("app.services.agent_ui_tool_environment.shutil.which", return_value=str(self.node)), \
                patch("app.services.agent_ui_tool_environment.workspace_process_registry.run",
                    side_effect=subprocess.TimeoutExpired("fixture-secret", 5)):
            with self.assertRaises(ValueError) as caught:
                resolve_design_tools(self.root, self.manifest)
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_project_version_dispatch_has_bounded_cold_start_budget(self) -> None:
        """全局 pnpm 按 packageManager 分派精确版本时，不能被五秒冷启动窗口误杀。"""
        self.cached_cli()

        def execute(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            """模拟分派耗时超五秒，仍保留有限超时和原取消 owner。"""
            if kwargs['timeout'] < 30:
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
            self.assertLessEqual(kwargs['timeout'], 30)
            self.assertEqual(kwargs['run_id'], 'original-owner')
            return subprocess.CompletedProcess(argv, 0, '9.15.9', '')

        with patch('app.services.agent_ui_tool_environment.shutil.which', return_value=str(self.node)), \
             patch('app.services.agent_ui_tool_environment.workspace_process_registry.run', side_effect=execute):
            self.assertEqual(resolve_design_tools(self.root, self.manifest, 'original-owner'), (self.node, self.cli))


class CreatorProcessEnvironmentTests(unittest.TestCase):
    """仅 Creator 运行进程获得项目模型配置，凭据不进入对象表示或工作区文件。"""

    def settings(self, **changes: object) -> Settings:
        """构造内存模型参数，不读取或复制真实环境密钥。"""
        return Settings(model_base_url="https://model.example/v1", model_api_key="fixture-secret",
            model_name="general [display]", **changes)

    def test_global_settings_map_without_mutating_parent_environment(self) -> None:
        """映射参数并显式固定模式，不继承同名 Creator 设置或外部 endpoint。"""
        with patch.dict(os.environ, {"CREATOR_MODEL_API_KEY": "stale", "NPM_TOKEN": "registry-secret",
                "NODE_OPTIONS": "--require untrusted", "CREATOR_ENDPOINT": "http://external"}):
            environment = creator_process_environment(self.settings())
            self.assertEqual(os.environ["CREATOR_MODEL_API_KEY"], "stale")
        self.assertEqual(environment["CREATOR_MODEL_API_KEY"], "fixture-secret")
        self.assertEqual(environment["CREATOR_MODEL_NAME"], "general")
        self.assertEqual(environment["CREATOR_AGENT_MODE"], "domain-write")
        self.assertEqual(environment["CREATOR_VERIFICATION_MODE"], "static_only")
        self.assertFalse({"NPM_TOKEN", "NODE_OPTIONS", "CREATOR_ENDPOINT"} & environment.keys())

    def test_ui_model_override_is_shared_with_creator(self) -> None:
        """UI 模型覆盖完整时生效，缺字段时沿用平台已有全局回退规则。"""
        settings = self.settings(ui_design_model_base_url="https://ui.example/v1",
            ui_design_model_api_key="ui-secret", ui_design_model_name="ui-model")
        environment = creator_process_environment(settings)
        self.assertEqual(environment["CREATOR_MODEL_BASE_URL"], "https://ui.example/v1")
        self.assertEqual(environment["CREATOR_MODEL_API_KEY"], "ui-secret")
        self.assertEqual(environment["CREATOR_MODEL_NAME"], "ui-model")
        self.assertEqual(creator_process_environment(self.settings(ui_design_model_name="incomplete"))["CREATOR_MODEL_NAME"], "general")

    def test_unsupported_model_provider_is_explicit(self) -> None:
        """SDK 不支持的提供方在启动前拒绝，不能偷偷切换服务。"""
        with self.assertRaisesRegex(ValueError, "OpenAI"):
            creator_process_environment(self.settings(model_provider="anthropic"))

    def test_creator_step_budget_preserves_context_and_lower_platform_limit(self) -> None:
        """多步 Creator 不继承整页长输出预算，且不超过用户设置的较小上限。"""
        for requested, expected in ((32768, 8192), (8192, 8192), (2048, 2048)):
            with self.subTest(requested=requested):
                environment = creator_process_environment(self.settings(ui_design_max_tokens=requested))
                self.assertEqual(environment["CREATOR_MODEL_MAX_TOKENS"], str(expected))

    def test_unsupported_headers_and_proxy_are_not_silently_dropped(self) -> None:
        """SDK 缺少的配置能力必须明确拒绝，不能伪称完整继承或回显敏感值。"""
        for changes in ({"model_custom_headers": {"x-secret": "fixture-secret"}}, {"model_trust_env": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError) as caught:
                creator_process_environment(self.settings(**changes))
            self.assertNotIn("fixture-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
