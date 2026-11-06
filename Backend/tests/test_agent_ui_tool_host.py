"""验证工具宿主预检，不安装或伪装已发布的 Creator。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.agent_ui_tool_host import (
    AgentUiToolHostError, AgentUiToolHostInspection, inspect_agent_ui_tool_host,
)
from app.services.workspace_process_registry import WorkspaceProcessRegistry


class AgentUiToolHostTests(unittest.TestCase):
    """以隔离 metadata 夹具测试准入和真实 Node 短进程边界。"""

    def setUp(self) -> None:
        """创建独立工具和目标目录，不需要另一份 Creator 配置。"""
        self.temporary = TemporaryDirectory(prefix="agent ui 测试 ")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.workspace = root / "目标工程"
        self.installation = root / "开发工具"
        for directory in (self.workspace, self.installation):
            directory.mkdir()
        self.package = self.installation / "node_modules/@za38.03/agent-ui-creator/package.json"
        self.package.parent.mkdir(parents=True)
        self.metadata: dict[str, object] = {
            "name": "@za38.03/agent-ui-creator", "version": "1.2.3",
            "engines": {"node": "^22.0.0 || ^24.22.1"},
            "exports": {".": {"import": "./dist/index.js"}, "./package.json": "./package.json"},
            "dependencies": {"@za38.03/agent-ui-runtime": "1.0.0"},
        }
        self.write_metadata()
        self.node = root / ("node.exe" if os.name == "nt" else "node")
        self.node.touch()
        self.registry = WorkspaceProcessRegistry()

    def write_metadata(self) -> None:
        """更新测试专用包清单，不创建或引用真实 Creator 实现。"""
        self.package.write_text(json.dumps(self.metadata), encoding="utf-8")

    def inspect(self, **changes: object) -> AgentUiToolHostInspection:
        """用当前隔离目录执行同一个生产预检入口。"""
        arguments = {
            "workspace": self.workspace, "installation_root": self.installation,
            "node_executable": self.node,
            "creator_version": "1.2.3", "run_id": "inspection",
        }
        arguments.update(changes)
        return inspect_agent_ui_tool_host(**arguments)

    def node_result(self, version: str = "24.22.1", **changes: object) -> subprocess.CompletedProcess[str]:
        """生成 Node 进程证据，独立于待检 Creator metadata。"""
        evidence = {"nodeVersion": version, "executable": str(self.node), "electronVersion": None}
        evidence.update(changes)
        return subprocess.CompletedProcess([], 0, json.dumps(evidence), "")

    def test_inspection_is_metadata_only_and_does_not_read_configuration(self) -> None:
        """环境证据不得泄露密钥或声称 Creator 已启动/registry 已验证。"""
        with patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry), \
                patch.object(self.registry, "run", return_value=self.node_result()) as run:
            result = self.inspect()
        self.assertEqual(result.creator_version, "1.2.3")
        self.assertEqual(result.node_version, "24.22.1")
        self.assertFalse(hasattr(result, "config_file"))
        self.assertEqual(len(result.package_json_sha256), 64)
        self.assertNotIn("fixture-secret", repr(result))
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], str(self.node))
        self.assertEqual(argv[1], "--eval")
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["workspace"], self.workspace)
        self.assertEqual(run.call_args.kwargs["run_id"], "inspection")

    def test_current_node_24_14_is_rejected_but_node_22_is_allowed(self) -> None:
        """按实际 engines 判断，不能只检查 Node major 为 24。"""
        for version, accepted in [("24.14.1", False), ("22.0.0", True), ("22.99.1", True),
                                  ("23.0.0", False), ("24.22.1", True), ("25.0.0", False)]:
            with self.subTest(version=version), patch.object(
                self.registry, "run", return_value=self.node_result(version)
            ), patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry):
                if accepted:
                    self.assertEqual(self.inspect().node_version, version)
                else:
                    with self.assertRaises(AgentUiToolHostError) as caught:
                        self.inspect()
                    self.assertEqual(caught.exception.code, "node_incompatible")

    def test_unreviewed_engine_syntax_is_rejected(self) -> None:
        """不支持的范围明确阻断，不能把部分解析当作完整 npm semver。"""
        self.metadata["engines"] = {"node": ">=22"}
        self.write_metadata()
        with self.assertRaises(AgentUiToolHostError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "unsupported_engine")

    def test_unpinned_version_and_mismatched_package_are_rejected(self) -> None:
        """拒绝 latest、版本范围及与审阅计划不同的安装身份。"""
        for version in ("latest", "^1.2.3", "1.2.4"):
            with self.subTest(version=version), self.assertRaises(AgentUiToolHostError):
                self.inspect(creator_version=version)

    def test_inspection_requires_no_creator_configuration(self) -> None:
        """未创建任何 Creator 配置目录，也能验证当前 Node 和公开成品包。"""
        with patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry), \
                patch.object(self.registry, "run", return_value=self.node_result()):
            self.assertEqual(self.inspect().node_version, "24.22.1")

    def test_local_dependencies_and_non_creator_identity_are_rejected(self) -> None:
        """源码工作区 metadata 不能冒充 registry 安装的准入证据。"""
        original = dict(self.metadata)
        for field, value in [("dependencies", {"x": "workspace:*"}),
                             ("dependencies", {"x": "file:../source"}),
                             ("optionalDependencies", {"x": r"C:\source\package"}),
                             ("name", "some-other-tool")]:
            with self.subTest(value=value):
                self.metadata = {**original, field: value}
                self.write_metadata()
                with self.assertRaises(AgentUiToolHostError) as caught:
                    self.inspect()
                expected = "creator_identity_mismatch" if field == "name" else "invalid_metadata"
                self.assertEqual(caught.exception.code, expected)

    def test_tools_must_be_separate_from_target(self) -> None:
        """工具安装与正式目标重叠时不得进入预检进程。"""
        for change in ({"installation_root": self.workspace},):
            with self.subTest(change=change), self.assertRaises(AgentUiToolHostError) as caught:
                self.inspect(**change)
            self.assertEqual(caught.exception.code, "invalid_location")

    def test_missing_package_and_invalid_json_fail_without_echoing_content(self) -> None:
        """缺包/损坏 metadata 有明确失败，原始内容不出现在诊断中。"""
        self.package.write_text("fixture-secret invalid json", encoding="utf-8")
        with self.assertRaises(AgentUiToolHostError) as caught:
            self.inspect()
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.package.rename(self.package.with_name("missing.json"))
        with self.assertRaises(AgentUiToolHostError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "creator_unavailable")

    @unittest.skipIf(os.name == "nt", "Windows 符号链接权限需真实平台另验")
    def test_package_symlink_cannot_escape_tool_installation(self) -> None:
        """外部仓库链接不能通过 tool/node_modules 冒充独立包安装。"""
        external = self.workspace / "package.json"
        self.package.rename(external)
        self.package.symlink_to(external)
        with self.assertRaises(AgentUiToolHostError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "invalid_location")

    def test_electron_and_wrong_node_identity_are_rejected(self) -> None:
        """显式 Node 路径不允许被 Electron 或另一个可执行文件替代。"""
        for changes in ({"electronVersion": "37.2.5"}, {"executable": str(self.workspace / "node")}):
            with self.subTest(changes=changes), patch(
                "app.services.agent_ui_tool_host.workspace_process_registry", self.registry
            ), patch.object(self.registry, "run", return_value=self.node_result(**changes)):
                with self.assertRaises(AgentUiToolHostError) as caught:
                    self.inspect()
                self.assertEqual(caught.exception.code, "invalid_node")

    def test_probe_failure_timeout_and_cancellation_are_safe_errors(self) -> None:
        """底层输出、超时和取消都不能泄漏原始日志或伪装就绪。"""
        for failure, code in [(subprocess.TimeoutExpired("node fixture-secret", 1), "probe_timeout"),
                              (RuntimeError("fixture-secret"), "probe_cancelled"),
                              (OSError("fixture-secret"), "probe_failed")]:
            with self.subTest(code=code), patch(
                "app.services.agent_ui_tool_host.workspace_process_registry", self.registry
            ), patch.object(self.registry, "run", side_effect=failure):
                with self.assertRaises(AgentUiToolHostError) as caught:
                    self.inspect()
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("fixture-secret", str(caught.exception))

    def test_nonzero_node_exit_does_not_expose_stdout_or_stderr(self) -> None:
        """Node 失败退出与身份不符分开报告，原始输出留在受控边界内。"""
        failure = subprocess.CompletedProcess([], 1, "fixture-secret", "fixture-secret")
        with patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry), \
                patch.object(self.registry, "run", return_value=failure):
            with self.assertRaises(AgentUiToolHostError) as caught:
                self.inspect()
        self.assertEqual(caught.exception.code, "probe_failed")
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_node_exe_path_is_passed_as_one_argument(self) -> None:
        """带空格/中文目录的 node.exe 保持单个原生参数，不经 shell 拼接。"""
        executable = self.node.with_name("node.exe")
        if executable != self.node:
            self.node.rename(executable)
            self.node = executable
        with patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry), \
                patch.object(self.registry, "run", return_value=self.node_result()) as run:
            self.inspect()
        self.assertEqual(run.call_args.args[0][0], str(executable))
        self.assertIsInstance(run.call_args.args[0], list)

    def test_probe_environment_does_not_inherit_preloads_or_credentials(self) -> None:
        """Node 探测不继承 NODE_OPTIONS/NODE_PATH 或模型/registry 密钥。"""
        with patch.dict(os.environ, {"NODE_OPTIONS": "--require fixture-secret", "NODE_PATH": "source",
                                    "CREATOR_MODEL_API_KEY": "fixture-secret", "NPM_TOKEN": "fixture-secret"}), \
                patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry), \
                patch.object(self.registry, "run", return_value=self.node_result()) as run:
            self.inspect()
        environment = run.call_args.kwargs["env"]
        self.assertFalse({"NODE_OPTIONS", "NODE_PATH", "CREATOR_MODEL_API_KEY", "NPM_TOKEN"} & environment.keys())

    def test_invalid_timeout_and_native_path_requirement(self) -> None:
        """file URL/相对路径必须先转换为原生路径，非法预算不能启动进程。"""
        for change in ({"node_executable": "file:///C:/Program%20Files/node.exe"},
                       {"node_executable": "node"}, {"timeout_seconds": 0},
                       {"timeout_seconds": float("nan")}):
            with self.subTest(change=change), self.assertRaises(AgentUiToolHostError):
                self.inspect(**change)

    @unittest.skipUnless(shutil.which("node"), "本机未提供可运行的 Node")
    def test_real_node_probe_registers_and_reaps_only_inspection_process(self) -> None:
        """真实短进程验证路径/登记/清理，不代表真实 Creator 包或服务通过。"""
        node = Path(shutil.which("node")).resolve()
        version = subprocess.check_output([str(node), "--version"], text=True).strip().removeprefix("v")
        self.metadata["engines"] = {"node": f"^{version}"}
        self.write_metadata()
        with patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry):
            result = self.inspect(node_executable=node)
        self.assertEqual(result.node_version, version)
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])
        self.assertEqual(self.registry.active_run_processes("inspection"), [])

    def test_cancelled_run_cannot_start_node(self) -> None:
        """已停止运行不允许迟到探测重新启动子进程。"""
        self.registry.cancel_run("inspection")
        with patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry):
            with self.assertRaises(AgentUiToolHostError) as caught:
                self.inspect()
        self.assertEqual(caught.exception.code, "probe_cancelled")
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])

    @unittest.skipUnless(shutil.which("node"), "本机未提供可运行的 Node")
    def test_real_timeout_reaps_inspection_process(self) -> None:
        """真实超时后必须回收探测进程，不等待 Creator 或模型服务。"""
        node = Path(shutil.which("node")).resolve()
        with patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry), \
                patch("app.services.agent_ui_tool_host._NODE_PROBE", "setTimeout(() => {}, 10000)"):
            with self.assertRaises(AgentUiToolHostError) as caught:
                self.inspect(node_executable=node, timeout_seconds=0.05)
        self.assertEqual(caught.exception.code, "probe_timeout")
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])
        self.assertEqual(self.registry.active_run_processes("inspection"), [])


if __name__ == "__main__":
    unittest.main()
