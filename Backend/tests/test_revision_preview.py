"""历史版本预览的物化与依赖复用判定测试。

覆盖"把某个 tag 的树物化到独立目录"这条路径。不测真正的 dev server 启动
（需要 pnpm 与网络），那部分由端到端验证覆盖。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.protocols.revision_preview import build_revision_preview_ag_ui_stream
from app.services.backend_project_launcher import _detect_backend_port
from app.services.frontend_node_runtime import record_frontend_dependencies
from app.services.frontend_project_launcher import _dependency_check_bypass_argv
from app.services.revision_preview import (
    RevisionPreviewError,
    _await_revision_preview_url,
    _can_reuse_dependencies,
    materialize_revision,
    revision_preview_dir,
    revision_preview_runtime_subdir,
    start_revision_preview,
    stop_revision_preview,
    stop_workspace_revision_previews,
)
from app.services.workspace_process_registry import workspace_process_registry
from app.utils.process_liveness import pid_is_running
from tests.frontend_node_fixture import stub_frontend_node


class RevisionPreviewTests(unittest.TestCase):
    """验证物化出的是该版本当时的树，且不碰工作区。"""

    def test_workspace_cleanup_finds_worktrees_and_orphaned_runtime(self) -> None:
        """工作区清扫删除现存 worktree、进程登记和仅剩日志的历史目录。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = revision_preview_dir(root, "feature/one")
            second = revision_preview_dir(root, "feature/two")
            orphan = revision_preview_dir(root, "orphan")
            log_only = revision_preview_dir(root, "log-only")
            runtime = first.parent.parent
            orphan_runtime = runtime / revision_preview_runtime_subdir("orphan")
            log_only_runtime = runtime / revision_preview_runtime_subdir("log-only")
            normal_runtime = runtime / "launch"
            for path in (first, second, orphan_runtime, log_only_runtime, normal_runtime):
                path.mkdir(parents=True, exist_ok=True)
            (orphan_runtime / "frontend.pid").write_text("99999", encoding="utf-8")
            for path in (log_only_runtime, normal_runtime):
                (path / "frontend.stdout.log").write_text("preview log", encoding="utf-8")
            with (
                patch("app.services.revision_preview.stop_frontend_project", return_value={"status": "stopped"}) as frontend,
                patch("app.services.revision_preview.stop_workspace_backend_project", return_value={"status": "stopped"}) as backend,
                patch("app.services.revision_preview._remove_worktree", side_effect=lambda _root, path: path.rmdir()) as remove,
            ):
                stop_workspace_revision_previews(root)
            self.assertEqual({call.kwargs["runtime_subdir"] for call in frontend.call_args_list}, {
                revision_preview_runtime_subdir(branch) for branch in ("feature/one", "feature/two", "orphan", "log-only")
            })
            self.assertEqual({call.args for call in backend.call_args_list}, {(first,), (second,), (orphan,), (log_only,)})
            self.assertEqual({call.args for call in remove.call_args_list}, {(root, first), (root, second)})
            self.assertFalse(first.parent.exists())
            self.assertFalse(orphan_runtime.exists())
            self.assertFalse(log_only_runtime.exists())
            self.assertTrue((normal_runtime / "frontend.stdout.log").is_file())

    def test_materializes_the_revision_tree(self) -> None:
        """物化目录里的内容必须是该 tag 当时的版本，而不是工作区当前内容。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            page = root / "frontend" / "src" / "pages" / "Home" / "index.tsx"
            page.parent.mkdir(parents=True)
            page.write_text("export default function Home() { return null }\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "feat: 首页")
            self._git(root, "tag", "v1.0")

            # 之后工作区继续演进：新增一个页面。
            second = root / "frontend" / "src" / "pages" / "Second" / "index.tsx"
            second.parent.mkdir(parents=True)
            second.write_text("export default function Second() { return null }\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "feat: 第二个页面")

            materialized = materialize_revision(root, "v1.0")

            self.assertTrue((materialized / "frontend" / "src" / "pages" / "Home").is_dir())
            self.assertFalse(
                (materialized / "frontend" / "src" / "pages" / "Second").exists(),
                "物化目录不能包含 tag 之后才有的文件",
            )
            # 工作区本身不受影响，两个页面都还在。
            self.assertTrue((root / "frontend" / "src" / "pages" / "Second").is_dir())

    def test_materialize_is_idempotent(self) -> None:
        """同一版本重复物化复用同一目录，不重建树。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            self._git(root, "tag", "v1.0")

            first = materialize_revision(root, "v1.0")
            # 在物化目录里留一个标记文件：若重复物化重建了目录，它就会消失。
            marker = first / "marker.txt"
            marker.write_text("kept\n", encoding="utf-8")

            second = materialize_revision(root, "v1.0")

            self.assertEqual(first, second)
            self.assertTrue(marker.exists(), "重复物化不应重建目录")

    def test_materialize_recovers_missing_registered_worktree(self) -> None:
        """物理目录丢失但 Git 仍有注册时，应清理失效记录后重新物化。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            self._git(root, "tag", "v1.0")
            materialized = materialize_revision(root, "v1.0")

            # 模拟应用退出或外部清理只删掉目录、没有执行 git worktree remove 的中断状态。
            shutil.rmtree(materialized)
            listed = self._git(root, "worktree", "list", "--porcelain")
            self.assertIn(str(materialized), listed, "测试前置必须保留一条失效注册")

            recovered = materialize_revision(root, "v1.0")

            self.assertEqual(recovered, materialized)
            self.assertTrue(recovered.is_dir(), "失效注册清理后应重新创建版本工作区")
            self.assertEqual(
                self._git(recovered, "rev-parse", "HEAD"),
                self._git(root, "rev-parse", "v1.0^{commit}"),
            )

    def test_unknown_revision_reports_clear_error(self) -> None:
        """不存在的版本报明确错误，而不是抛底层 Git 失败。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))

            with self.assertRaisesRegex(RevisionPreviewError, "不存在"):
                materialize_revision(root, "v9.9")

    def test_empty_revision_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))

        with self.assertRaisesRegex(RevisionPreviewError, "缺少要预览的分支标识"):
                materialize_revision(root, "   ")

    def test_stop_removes_materialized_directory(self) -> None:
        """停止后物化目录与 worktree 注册都要清干净。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            self._git(root, "tag", "v1.0")
            with patch("app.services.revision_preview._start_revision_preview", side_effect=self._checkout_preview):
                outcome = start_revision_preview(root, "v1.0", preview_id="first")
            materialized = Path(outcome["materialized_path"])
            self.assertTrue(materialized.is_dir())
            runtime = materialized.parent.parent / revision_preview_runtime_subdir("v1.0")
            (runtime / "frontend.stdout.log").write_text("preview log", encoding="utf-8")

            stop_revision_preview(root, "v1.0", preview_id="first")

            self.assertFalse(materialized.exists(), "物化目录应被摘除")
            # worktree 注册项也要清掉，否则会一直堆在 git 的元数据里。
            listed = self._git(root, "worktree", "list")
            self.assertNotIn("revision-preview", listed)
            self.assertFalse(materialized.parent.exists())
            self.assertFalse(runtime.exists())

    @staticmethod
    def _checkout_preview(root: Path, revision: str, _report: object) -> dict[str, str]:
        """使用实际 Git 检出替代服务启动，验证 worktree 是否正确回收。"""

        return {"materialized_path": str(materialize_revision(root, revision))}

    def test_switch_replaces_checkout_and_stale_same_branch_stop_is_ignored(self) -> None:
        """连续 A→B→C→A 永远只有一个 worktree，最早的 A 不能停止最后的 A。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            for branch in ("one", "two", "three"):
                self._git(root, "branch", branch)
            head = self._git(root, "rev-parse", "HEAD")
            local_edit = root / "local-edit.txt"
            local_edit.write_text("working", encoding="utf-8")
            unrelated = root / "another-worktree"
            self._git(root, "worktree", "add", "--detach", str(unrelated), "HEAD")
            # 旧实现可能留下多个已注册的历史目录。首次启动也应回收这些残留。
            materialize_revision(root, "two")
            materialize_revision(root, "three")
            with patch("app.services.revision_preview._start_revision_preview", side_effect=self._checkout_preview):
                for index, branch in enumerate(("one", "two", "three", "one")):
                    target = revision_preview_dir(root, branch)
                    start_revision_preview(root, branch, preview_id=str(index))
                    self.assertEqual(list(target.parent.iterdir()), [target])
                    self.assertEqual(self._git(root, "worktree", "list").count("revision-preview"), 1)
                    self.assertEqual(
                        list(target.parent.parent.glob("launch-revision-*")),
                        [target.parent.parent / revision_preview_runtime_subdir(branch)],
                    )
                stop_revision_preview(root, "one", preview_id="0")
                self.assertTrue(target.is_dir())
                self.assertTrue((target.parent.parent / revision_preview_runtime_subdir("one")).is_dir())
                stop_revision_preview(root, "one", preview_id="3")
            self.assertFalse(target.parent.exists())
            self.assertEqual(list(target.parent.parent.glob("launch-revision-*")), [])
            self.assertTrue((unrelated / ".git").is_file())
            self.assertEqual(self._git(root, "rev-parse", "HEAD"), head)
            self.assertEqual(local_edit.read_text(encoding="utf-8"), "working")

    def test_locked_preview_blocks_replacement(self) -> None:
        """Git 拒绝移除锁定目录时保留原登记，并拒绝检出另一分支。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            self._git(root, "branch", "one")
            self._git(root, "branch", "two")
            with patch("app.services.revision_preview._start_revision_preview", side_effect=self._checkout_preview):
                start_revision_preview(root, "one", preview_id="first")
                target = revision_preview_dir(root, "one")
                self._git(root, "worktree", "lock", str(target))
                with self.assertRaisesRegex(RevisionPreviewError, "无法清理历史分支 worktree"):
                    start_revision_preview(root, "two", preview_id="second")
            self.assertTrue(target.is_dir())
            self.assertFalse(revision_preview_dir(root, "two").exists())

    def test_stop_cancels_install_process_and_removes_checkout(self) -> None:
        """退出时终止真实的受控安装子进程，并等待 Git worktree 清理。"""

        stub_frontend_node(self, "app.services.revision_preview")
        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            self._git(root, "branch", "one")
            marker = root / "install-pid.txt"
            target = revision_preview_dir(root, "one")
            preview_id = str(uuid4())

            def install_and_launch(*_args: object, **_kwargs: object) -> dict[str, str]:
                """用长时间运行的命令模拟包管理器，沿真实运行身份登记子进程。"""

                workspace_process_registry.run(
                    [sys.executable, "-c", "import os, pathlib, sys, time\n"
                     "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(120)", str(marker)],
                    workspace=root, cwd=target, timeout=125,
                )
                self.fail("安装命令应被取消")

            async def exercise() -> None:
                """用两个完整 AG-UI 请求启动并停止同一轮预览。"""

                start = asyncio.create_task(_request_preview(root, "start", "one", preview_id))
                try:
                    async with asyncio.timeout(8):
                        while not marker.exists() or not marker.stat().st_size:
                            if start.done():
                                self.fail(f"未执行安装就结束：{start.result()}")
                            await asyncio.sleep(0.02)
                    pid = int(marker.read_text(encoding="utf-8"))
                    self.assertTrue(pid_is_running(pid))
                    stopped = await asyncio.wait_for(_request_preview(root, "stop", "one", preview_id), 10)
                    self.assertEqual(stopped["previewStatus"], "stopped")
                    self.assertEqual((await asyncio.wait_for(start, 10))["status"], "failed")
                    self.assertFalse(pid_is_running(pid))
                    self.assertFalse(target.parent.exists())
                    self.assertNotIn("revision-preview", self._git(root, "worktree", "list"))
                finally:
                    if not start.done():
                        await _request_preview(root, "stop", "one", preview_id)
                        await start

            with patch("app.services.revision_preview.launch_frontend_project", side_effect=install_and_launch):
                asyncio.run(exercise())

    def test_runtime_subdir_is_isolated_per_revision(self) -> None:
        """每个版本有独立运行时目录，进程登记不会互相覆盖。"""

        self.assertNotEqual(
            revision_preview_runtime_subdir("v1.0"),
            revision_preview_runtime_subdir("v1.1"),
        )
        # tag 可以含 `/`，不能直接当目录名。
        self.assertNotIn("/", revision_preview_runtime_subdir("release/v1.0"))

    def test_reuse_requires_identical_package_json(self) -> None:
        """依赖复用只在 package.json 完全一致时成立。

        锁文件不同不影响复用：线上 v1.0 的锁文件因安全 override 修复而与工作区不同，
        但声明的依赖一致，足以支撑渲染。
        """

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "frontend").mkdir()
            package = root / "frontend" / "package.json"
            package.write_text(json.dumps({"name": "app", "dependencies": {"react": "18"}}), encoding="utf-8")
            (root / "frontend" / "node_modules").mkdir()
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "chore: 前端工程")
            self._git(root, "tag", "v1.0")

            materialized = materialize_revision(root, "v1.0")
            self.assertTrue(_can_reuse_dependencies(root, materialized), "声明一致时应可复用")

            # 工作区新增依赖后不再复用。
            package.write_text(
                json.dumps({"name": "app", "dependencies": {"react": "18", "axios": "1"}}),
                encoding="utf-8",
            )
            self.assertFalse(
                _can_reuse_dependencies(root, materialized),
                "声明不一致时必须重新安装",
            )

    def test_reuse_requires_installed_workspace_dependencies(self) -> None:
        """工作区没装依赖时不能复用（没有可软链的目标）。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "frontend").mkdir()
            (root / "frontend" / "package.json").write_text(
                json.dumps({"name": "app"}), encoding="utf-8"
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "chore: 前端工程")
            self._git(root, "tag", "v1.0")

            materialized = materialize_revision(root, "v1.0")
            self.assertFalse(_can_reuse_dependencies(root, materialized))

    def test_revision_preview_dir_stays_inside_runtime(self) -> None:
        """物化目录必须落在 .devagentstudio/runtime 下（该路径已被 gitignore）。"""

        directory = revision_preview_dir("/tmp/ws", "v1.0")
        self.assertIn(".devagentstudio/runtime/revision-preview", str(directory))

    def _init_repository(self, root: Path) -> Path:
        """创建具有基线提交的最小仓库。"""

        self._git(root, "init")
        self._git(root, "config", "user.email", "tests@example.com")
        self._git(root, "config", "user.name", "Tests")
        (root / "README.md").write_text("base\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "initial")
        return root

    def _git(self, root: Path, *arguments: str) -> str:
        """运行测试限定的 Git 命令并返回标准输出。"""

        completed = subprocess.run(
            ["git", *arguments],
            cwd=str(root),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr or completed.stdout)
        return completed.stdout


class RevisionPreviewLaunchTests(unittest.TestCase):
    """复用软链依赖时，启动参数必须让包管理器跳过"启动前依赖校验"。

    pnpm 那一步按 lstat 判断 node_modules 是不是目录，软链会被判成"没有依赖目录"，于是它
    去执行 install，而 install 又要先清空这个它不认识的目录 —— 无终端时直接以
    ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY 失败，dev server 起不来（线上实测如此）。
    """

    def test_java_worktree_and_api_proxy_are_bound_to_the_same_revision(self) -> None:
        """Java 从历史 worktree 构建，前端使用该 Java 实际端口，原配置保持不变。"""
        stub_frontend_node(self, "app.services.revision_preview")
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace).resolve()
            target = revision_preview_dir(root, "release")
            config = target / "frontend/rsbuild.config.ts"
            original = "export default { server: { proxy: { '/api': 'http://127.0.0.1:8080' } } };"

            def checkout(_root: Path, _revision: str) -> Path:
                """在启动清扫完成后创建本轮历史工程。"""

                (target / "backend").mkdir(parents=True)
                (target / "backend/pom.xml").touch()
                (target / "frontend").mkdir()
                config.write_text(original, encoding="utf-8")
                return target

            with (
                patch("app.services.revision_preview.materialize_revision", side_effect=checkout),
                patch("app.services.revision_preview._await_revision_preview_url", return_value="http://localhost:3001"),
                patch("app.services.revision_preview.launch_backend_project", return_value={"status": "running", "server": {"port": 49152}}) as backend,
                patch("app.services.revision_preview.launch_frontend_project", return_value={"status": "running"}) as frontend,
                patch("app.services.revision_preview.stop_workspace_backend_project", return_value={"status": "stopped"}) as stop_backend,
            ):
                result = start_revision_preview(root, "release", preview_id="first")
                self.assertEqual(result["preview_url"], "http://localhost:3001")
                self.assertEqual(backend.call_args.args, (target,))
                self.assertEqual(backend.call_args.kwargs["preview_application_workspace"], root)
                self.assertEqual(frontend.call_args.kwargs["project_dir"], target / "frontend")
                generated = frontend.call_args.kwargs["server_config"]
                self.assertIn("target: 'http://127.0.0.1:49152'", generated.read_text(encoding="utf-8"))
                self.assertEqual(config.read_text(encoding="utf-8"), original)

                frontend.return_value = {"status": "failed", "message": "前端启动失败"}
                stop_backend.reset_mock()
                with self.assertRaisesRegex(RevisionPreviewError, "前端启动失败"):
                    start_revision_preview(root, "release", preview_id="second")
                self.assertEqual([call.args for call in stop_backend.call_args_list], [(target,), (target,)])
                self.assertFalse(target.parent.exists())

    def test_reads_dynamic_java_port_after_zero_port_initialization(self) -> None:
        """Spring Boot 初始化时记录端口零，Tomcat 就绪后才输出所分配的端口。"""
        with tempfile.TemporaryDirectory() as runtime:
            stdout = Path(runtime) / "backend.stdout.log"
            stdout.write_text(
                "Tomcat initialized with port(s): 0 (http)\nTomcat started on port(s): 49152 (http)\n",
                encoding="utf-8",
            )
            self.assertEqual(_detect_backend_port(
                stdout_log=stdout, stdout_offset=0, stderr_log=Path(runtime) / "stderr.log", stderr_offset=0,
            ), 49152)

    def test_reuse_also_requires_install_node_evidence(self) -> None:
        """相同依赖声明只有在安装 Node 匹配时才允许历史预览链接。"""
        node = stub_frontend_node(self, "app.services.revision_preview")
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace).resolve()
            revision = root / "revision"
            frontend = root / "frontend"
            for cwd in (frontend, revision / "frontend"):
                cwd.mkdir(parents=True)
                (cwd / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
            (frontend / "node_modules").mkdir()
            with (
                patch("app.services.revision_preview.materialize_revision", return_value=revision),
                patch("app.services.revision_preview._await_revision_preview_url", return_value="http://localhost:3001"),
                patch("app.services.revision_preview.launch_frontend_project", return_value={"status": "running"}) as launch,
                patch("app.services.revision_preview._link_reused_dependencies", return_value=True) as link,
            ):
                self.assertFalse(start_revision_preview(root, "release", preview_id="first")["reused_dependencies"])
                link.assert_not_called()
                self.assertFalse(launch.call_args.kwargs["skip_install"])
                self.assertEqual(launch.call_args.kwargs["install_timeout_seconds"], 600)
                record_frontend_dependencies(root / ".devagentstudio/runtime/launch", frontend, node)
                self.assertTrue(start_revision_preview(root, "release", preview_id="second")["reused_dependencies"])
                link.assert_called_once_with(root, revision)
                self.assertIs(launch.call_args.kwargs["node_runtime"], node)

    def test_pnpm_gets_the_bypass_flag(self) -> None:
        self.assertEqual(
            _dependency_check_bypass_argv("pnpm", True),
            ["--config.verify-deps-before-run=false"],
        )

    def test_flag_only_for_linked_dependencies(self) -> None:
        """没走软链复用时不能加这个参数：那会让真正的依赖缺失被静默放过。"""

        self.assertEqual(_dependency_check_bypass_argv("pnpm", False), [])

    def test_flag_only_for_pnpm(self) -> None:
        """只有 pnpm 有这一步校验，其它包管理器收到未知参数会直接报错。"""

        self.assertEqual(_dependency_check_bypass_argv("npm", True), [])
        self.assertEqual(_dependency_check_bypass_argv("yarn", True), [])

    def test_ready_log_is_accepted_when_desktop_http_probe_is_unavailable(self) -> None:
        """HTTP 探测受桌面环境限制时，存活进程的 Vite 就绪日志仍应结束等待。"""

        with tempfile.TemporaryDirectory() as runtime:
            runtime_root = Path(runtime)
            (runtime_root / "frontend.pid").write_text("12345", encoding="utf-8")
            (runtime_root / "frontend.stdout.log").write_text(
                '\n'.join(
                    (
                        'WARN Unsupported engine: wanted: {"node":">=20.19 <23"}',
                        'VITE v6.4.3 ready in 280 ms',
                        'Local: http://localhost:3000/',
                    )
                ),
                encoding="utf-8",
            )
            (runtime_root / "frontend.stderr.log").write_text("", encoding="utf-8")

            with (
                patch("app.services.revision_preview._preview_is_ready", return_value=False),
                patch("app.services.revision_preview._running_pids", return_value=[12345]),
            ):
                self.assertEqual(
                    _await_revision_preview_url(runtime_root),
                    "http://localhost:3000",
                )

    def test_ready_log_does_not_hide_fatal_stderr(self) -> None:
        """即使 stdout 已打印 Local 地址，致命编译错误仍必须阻止就绪。"""

        with tempfile.TemporaryDirectory() as runtime:
            runtime_root = Path(runtime)
            (runtime_root / "frontend.pid").write_text("12345", encoding="utf-8")
            (runtime_root / "frontend.stdout.log").write_text(
                "VITE ready in 280 ms\nLocal: http://localhost:3000/\n",
                encoding="utf-8",
            )
            (runtime_root / "frontend.stderr.log").write_text(
                "Cannot find module 'missing-package'\n",
                encoding="utf-8",
            )

            with (
                patch("app.services.revision_preview._preview_is_ready", return_value=False),
                patch(
                    "app.services.revision_preview._running_pids",
                    side_effect=([12345], []),
                ),
                patch("app.services.revision_preview.time.sleep", return_value=None),
            ):
                self.assertEqual(_await_revision_preview_url(runtime_root), "")


class RevisionPreviewProtocolTests(unittest.TestCase):
    """校验 AG-UI 载荷的外层契约。

    前端按 `schemaVersion` + `status ∈ {completed, failed}` 判定载荷是否有效，而信封先写
    `status` 再展开动作数据 —— 动作数据里一旦有同名字段就会把运行态顶掉，整条结果被判无效。
    这里把"预览状态必须另起字段名"钉住。
    """

    def test_preview_state_does_not_clobber_run_status(self) -> None:
        """动作数据里的预览状态必须叫 previewStatus，不能占用信封的 status。"""

        with tempfile.TemporaryDirectory() as workspace:
            payload = self._collect_final_payload(workspace, action="get", revision="v1.0")

            self.assertEqual(payload["status"], "completed", "信封的运行态被动作数据顶掉了")
            # 服务在空工作区里没有进程，就是 idle；关键是它出现在 previewStatus 上。
            self.assertEqual(payload["previewStatus"], "idle")
            self.assertEqual(payload["action"], "get")

    def test_stop_reports_its_own_status_separately(self) -> None:
        """stop 同样要避开 status：它的运行态是 stopped。"""

        with tempfile.TemporaryDirectory() as workspace:
            payload = self._collect_final_payload(workspace, action="stop", revision="v1.0")

            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["previewStatus"], "stopped")

    def test_unknown_action_fails_without_clobbering_status(self) -> None:
        """非法动作走失败分支：status 为 failed，错误信息可读。"""

        with tempfile.TemporaryDirectory() as workspace:
            payload = self._collect_final_payload(workspace, action="nope", revision="v1.0")

            self.assertEqual(payload["status"], "failed")
            self.assertIn("get、start 或 stop", payload["error"]["message"])

    def _collect_final_payload(
        self, workspace: str, *, action: str, revision: str
    ) -> dict:
        """跑完整条 AG-UI 流，取最后一个 revision-preview 自定义事件。"""

        return asyncio.run(_request_preview(workspace, action, revision, str(uuid4())))


async def _request_preview(workspace: str | Path, action: str, revision: str, preview_id: str) -> dict:
    """消费真实的历史预览 AG-UI 流并返回终态。"""

    stream = build_revision_preview_ag_ui_stream(payload={
        "threadId": str(uuid4()),
        "forwardedProps": {"revisionPreview": {
            "action": action, "workspace": str(workspace), "revision": revision, "previewId": preview_id,
        }},
    })
    events: list[dict] = []
    async for chunk in stream:
        for line in chunk.splitlines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: "):])
            if event.get("type") == "CUSTOM" and event.get("name") == "revision-preview":
                events.append(event["value"])
    return events[-1]
