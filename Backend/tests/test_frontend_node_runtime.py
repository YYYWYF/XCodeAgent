"""验证 Node 解析、操作内锁定与实际包管理器 Node 核对。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import frontend_node_runtime as node_runtime


class FrontendNodeRuntimeTests(unittest.TestCase):
    """通过可控的安装目录模拟跨平台的 Node 选择。"""

    def setUp(self) -> None:
        """创建带空格的隔离安装路径。"""
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="node versions "))
        self.root = Path(directory).resolve()

    def _installed_node(self, version: str, name: str = "node") -> Path:
        """创建可供解析器查找的可执行文件。"""
        directory = self.root / version
        directory.mkdir()
        executable = directory / name
        executable.touch(mode=0o755)
        return executable

    @unittest.skipIf(os.name == "nt", "使用 POSIX 临时链接模拟 Windows NVM 链接切换")
    def test_windows_nvm_retarget_only_changes_next_operation(self) -> None:
        """已解析操作保留原版本，新操作读取重新指向的 NVM 链接。"""
        first = self._installed_node("v20.19.0", "node.exe")
        second = self._installed_node("v22.12.0", "node.exe")
        link = self.root / "active node"
        link.symlink_to(first.parent, target_is_directory=True)
        for node in (first, second):
            (node.parent / "pnpm.cmd").touch(mode=0o755)

        def probe(argv: list[str], *_args: object) -> tuple[Path, str]:
            """返回本次被探测的 Node 真实路径及完整版本。"""
            executable = Path(argv[0])
            return executable, executable.parent.name

        def which(name: str) -> str | None:
            """模拟 Windows 的可执行文件与 CMD 启动脚本后缀查找。"""
            candidate = Path(name)
            if not candidate.suffix:
                candidate = candidate.with_suffix(".cmd")
            return str(candidate) if candidate.is_file() else None

        with (
            patch.object(node_runtime.sys, "platform", "win32"),
            patch.dict(os.environ, {"PATH": str(link), "NVM_SYMLINK": str(link)}, clear=True),
            patch.object(node_runtime, "_probe_node", side_effect=probe),
            patch.object(node_runtime.shutil, "which", side_effect=which),
        ):
            original = node_runtime.resolve_frontend_node(self.root)
            link.unlink()
            link.symlink_to(second.parent, target_is_directory=True)
            switched = node_runtime.resolve_frontend_node(self.root)
            self.assertEqual(os.environ["PATH"], str(link))
            self.assertEqual(original.command("pnpm"), str(first.parent / "pnpm.cmd"))
            self.assertEqual(switched.command("pnpm"), str(second.parent / "pnpm.cmd"))
            (second.parent / "pnpm.cmd").unlink()
            with self.assertRaisesRegex(node_runtime.FrontendNodeError, r"pnpm.*v22\.12\.0"):
                switched.command("pnpm")
            del os.environ["NVM_SYMLINK"]
            fallback = node_runtime.resolve_frontend_node(self.root)
            self.assertEqual(fallback.source, "PATH")
            self.assertEqual(fallback.executable, second)
        self.assertEqual(original.executable, first)
        self.assertEqual(switched.executable, second)
        self.assertFalse(switched.matches(original.evidence()))

    def test_macos_nvm_and_path_default_have_explicit_precedence(self) -> None:
        """NVM_BIN 优先，没有 NVM 变量时才按 PATH 选择。"""
        default = self._installed_node("v20.19.0")
        nvm = self._installed_node("v22.12.0")
        with (
            patch.object(node_runtime.sys, "platform", "darwin"),
            patch.dict(os.environ, {"PATH": str(default.parent)}, clear=True),
            patch.object(node_runtime, "_probe_node", side_effect=[(default, "v20.19.0"), (nvm, "v22.12.0")]),
        ):
            self.assertEqual(node_runtime.resolve_frontend_node(self.root).source, "PATH")
            os.environ["NVM_BIN"] = str(nvm.parent)
            selected = node_runtime.resolve_frontend_node(self.root)
            self.assertEqual(selected.source, "NVM_BIN")
            self.assertEqual(selected.env["PATH"].split(os.pathsep)[0], str(nvm.parent))
            os.environ["NVM_BIN"] = str(self.root / "missing")
            with self.assertRaisesRegex(node_runtime.FrontendNodeError, "nvm use"):
                node_runtime.resolve_frontend_node(self.root)

    def test_package_manager_override_reports_actual_and_selected_versions(self) -> None:
        """包管理器选择了另一个 Node 时失败，缺工具同样包含当前 Node。"""
        wanted = self._installed_node("v22.12.0")
        override = self._installed_node("v20.19.0")
        runtime = node_runtime.FrontendNodeRuntime(wanted, "v22.12.0", "NVM_BIN", {"PATH": str(wanted.parent)})
        with patch.object(node_runtime, "_probe_node", return_value=(override, "v20.19.0")):
            with self.assertRaisesRegex(node_runtime.FrontendNodeError, r"v20\.19\.0.*v22\.12\.0"):
                runtime.verify("pnpm", workspace=self.root, cwd=self.root)
        with self.assertRaisesRegex(node_runtime.FrontendNodeError, r"pnpm.*v22\.12\.0"):
            runtime.command("pnpm")

    def test_yarn_uses_launcher_before_install_and_script_environment_after(self) -> None:
        """初次安装先核对 Yarn 启动器，依赖就绪后才用 exec 核对脚本。"""
        node = self._installed_node("v22.12.0")
        runtime = node_runtime.FrontendNodeRuntime(node, "v22.12.0", "NVM_BIN", {"PATH": str(node.parent)})
        self.assertEqual(node_runtime.frontend_package_manager(self.root, {"packageManager": "yarn@4.6.0"}), "yarn")
        with patch.object(node_runtime, "_probe_node", return_value=(node, "v22.12.0")) as probe:
            runtime.verify("yarn", workspace=self.root, cwd=self.root, before_install=True)
            self.assertEqual(probe.call_args.args[0], ["yarn", "--version"])
            self.assertIn("--require", probe.call_args.args[1]["NODE_OPTIONS"])
            runtime.verify("yarn", workspace=self.root, cwd=self.root)
            self.assertEqual(probe.call_args.args[0][:3], ["yarn", "exec", "node"])
            self.assertNotIn("NODE_OPTIONS", probe.call_args.args[1])
