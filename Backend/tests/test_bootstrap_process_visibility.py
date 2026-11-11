"""验证模板初始化的 Git 身份与克隆命令在 Windows 下隐藏控制台。"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import git_identity
from app.services import workspace_process_registry as registry_module
from app.services.workspace_bootstrap.git_manager import BootstrapGitManager
from app.services.workspace_bootstrap.models import TemplateEngineError
from app.services.workspace_bootstrap.template_engine_client import _git_clone


class BootstrapProcessVisibilityTests(unittest.TestCase):
    """覆盖模板基线入口以及全局配置缺失时额外启动的系统配置读取。"""

    def test_baseline_identity_reads_hide_windows_console(self) -> None:
        """从真实基线初始化入口验证姓名和邮箱查询均隐藏且保留 UTF-8 身份。"""

        with tempfile.TemporaryDirectory() as directory:
            manager = BootstrapGitManager()
            with (
                patch.dict(os.environ, {git_identity.AUTHOR_NAME_ENV_VAR: "", git_identity.AUTHOR_EMAIL_ENV_VAR: ""}),
                patch.object(git_identity, "os", SimpleNamespace(name="nt", getenv=os.getenv)),
                patch.object(git_identity.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, "开发者".encode(), b""),
                    subprocess.CompletedProcess([], 0, b"developer@example.test", b""),
                ]) as run,
                patch.object(manager, "_run", return_value="head") as run_git,
            ):
                self.assertEqual(manager.initialize_baseline(directory), "head")

            self.assertEqual(run.call_count, 2)
            for call, key in zip(run.call_args_list, ("user.name", "user.email"), strict=True):
                self.assertEqual(call.args[0], ["git", "config", "--global", "--get", key])
                self.assertEqual(call.kwargs["creationflags"], 0x08000000)
                self.assertFalse(call.kwargs["text"])
                self.assertEqual(call.kwargs["timeout"], 15)
            root = Path(directory).resolve()
            run_git.assert_any_call(root, ["git", "config", "--local", "user.name", "开发者"])
            run_git.assert_any_call(root, ["git", "config", "--local", "user.email", "developer@example.test"])

    def test_system_identity_fallback_also_hides_console(self) -> None:
        """两项全局配置均缺失时，四次查询都必须隐藏控制台。"""

        with (
            patch.dict(os.environ, {git_identity.AUTHOR_NAME_ENV_VAR: "", git_identity.AUTHOR_EMAIL_ENV_VAR: ""}),
            patch.object(git_identity, "os", SimpleNamespace(name="nt", getenv=os.getenv)),
            patch.object(git_identity.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 1, b"", b""),
                subprocess.CompletedProcess([], 0, b"Developer", b""),
                subprocess.CompletedProcess([], 1, b"", b""),
                subprocess.CompletedProcess([], 0, b"developer@example.test", b""),
            ]) as run,
        ):
            self.assertEqual(git_identity.resolve_commit_identity(), ("Developer", "developer@example.test"))
        self.assertEqual([call.args[0][2] for call in run.call_args_list],
                         ["--global", "--system", "--global", "--system"])
        self.assertTrue(all(call.kwargs["creationflags"] == 0x08000000 for call in run.call_args_list))

    def test_posix_identity_uses_no_windows_flags(self) -> None:
        """macOS 身份读取允许零创建标志并保持原有结果。"""

        with (
            patch.object(git_identity, "os", SimpleNamespace(name="posix")),
            patch.object(git_identity.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"Developer", b"")) as run,
        ):
            self.assertEqual(git_identity._read_local_git_identity("user.name"), "Developer")
        self.assertEqual(run.call_args.kwargs["creationflags"], 0)

    def test_explicit_identity_skips_git_queries(self) -> None:
        """显式配置完整身份时仍直接使用配置，不启动 Git 查询。"""

        with (
            patch.dict(os.environ, {git_identity.AUTHOR_NAME_ENV_VAR: "Configured", git_identity.AUTHOR_EMAIL_ENV_VAR: "configured@example.test"}),
            patch.object(git_identity.subprocess, "run") as run,
        ):
            self.assertEqual(git_identity.resolve_commit_identity(), ("Configured", "configured@example.test"))
        run.assert_not_called()

    def test_identity_query_failures_keep_fallback(self) -> None:
        """启动失败和超时保持原来的逐级降级行为。"""

        for failure in (OSError("missing git"), subprocess.TimeoutExpired("git", 15)):
            with self.subTest(failure=failure), patch.object(git_identity.subprocess, "run", side_effect=failure) as run:
                self.assertEqual(git_identity._read_local_git_identity("user.name"), "")
                self.assertEqual(run.call_count, 2)

    def test_template_clone_uses_hidden_workspace_runner(self) -> None:
        """沿真实克隆入口走到 Popen，验证它使用已有执行器的隐藏和进程组设置。"""

        process = MagicMock()
        process.poll.return_value = 0
        process.communicate.return_value = ("", "")
        process.returncode = 0
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "template"
            with (
                patch.object(registry_module, "os", SimpleNamespace(name="nt", path=os.path)),
                patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True),
                patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
                patch.object(subprocess, "Popen", return_value=process) as popen,
            ):
                _git_clone("https://example.test/template.git", destination)
            self.assertEqual(popen.call_args.args[0], [
                "git", "clone", "--depth", "1", "https://example.test/template.git", str(destination),
            ])
            self.assertEqual(popen.call_args.kwargs["creationflags"], 0x08000200)
            self.assertEqual(registry_module.workspace_process_registry.active_count(), 0)

    def test_template_clone_preserves_errors(self) -> None:
        """克隆命令切换执行器后仍把缺失 Git 和克隆失败包装为模板错误。"""

        for failure in (FileNotFoundError("git"), subprocess.CalledProcessError(1, "git", stderr="clone failed")):
            with (
                self.subTest(failure=failure),
                patch.object(registry_module.workspace_process_registry, "run", side_effect=failure),
                self.assertRaises(TemplateEngineError),
            ):
                _git_clone("https://example.test/template.git", Path("template"))
