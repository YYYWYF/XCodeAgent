"""历史版本预览的物化与依赖复用判定测试。

覆盖"把某个 tag 的树物化到独立目录"这条路径。不测真正的 dev server 启动
（需要 pnpm 与网络），那部分由端到端验证覆盖。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols.revision_preview import build_revision_preview_ag_ui_stream
from app.services.frontend_project_launcher import _dependency_check_bypass_argv
from app.services.revision_preview import (
    RevisionPreviewError,
    _await_revision_preview_url,
    _can_reuse_dependencies,
    materialize_revision,
    revision_preview_dir,
    revision_preview_runtime_subdir,
    stop_revision_preview,
)


class RevisionPreviewTests(unittest.TestCase):
    """验证物化出的是该版本当时的树，且不碰工作区。"""

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

            with self.assertRaisesRegex(RevisionPreviewError, "缺少要预览的版本"):
                materialize_revision(root, "   ")

    def test_stop_removes_materialized_directory(self) -> None:
        """停止后物化目录与 worktree 注册都要清干净。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            self._git(root, "tag", "v1.0")
            materialized = materialize_revision(root, "v1.0")
            self.assertTrue(materialized.is_dir())

            stop_revision_preview(root, "v1.0")

            self.assertFalse(materialized.exists(), "物化目录应被摘除")
            # worktree 注册项也要清掉，否则会一直堆在 git 的元数据里。
            listed = self._git(root, "worktree", "list")
            self.assertNotIn("revision-preview", listed)

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

        stream = build_revision_preview_ag_ui_stream(
            payload={
                "threadId": "t-1",
                "runId": "r-1",
                "forwardedProps": {
                    "revisionPreview": {
                        "action": action,
                        "workspace": workspace,
                        "revision": revision,
                    }
                },
            },
            accept="text/event-stream",
        )

        async def consume() -> list[dict]:
            events: list[dict] = []
            async for chunk in stream:
                for line in chunk.splitlines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[len("data: ") :])
                    if event.get("type") == "CUSTOM" and event.get("name") == "revision-preview":
                        events.append(event["value"])
            return events

        events = asyncio.run(consume())
        self.assertTrue(events, "没有收到 revision-preview 事件")
        return events[-1]
