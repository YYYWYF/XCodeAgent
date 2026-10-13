"""二次修改版本控制服务与 AG-UI 协议测试。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols.version_control import (
    build_version_control_ag_ui_stream,
    version_control_capabilities,
)
from app.services.version_publish import PUBLISH_REMOTE_NAME
from app.services.version_control import (
    CommitVersionControlRequest,
    InspectAllVersionControlRequest,
    InspectVersionControlRequest,
    SummarizeVersionControlRequest,
    VersionControlError,
    _read_head_message,
    commit_version_control,
    inspect_all_version_control,
    inspect_version_control,
    summarize_version_control,
)


class _StubModel:
    """替代真实聊天模型：回固定 content，可选记录收到的提示词。"""

    def __init__(self, content: object, captured: list[str] | None = None) -> None:
        """保存待回 content；传 captured 时把收到的提示词追加进去。"""

        self._content = content
        self._captured = captured

    async def ainvoke(self, messages: list[object]) -> object:
        """按聊天模型的最小契约返回带 content 的响应。"""

        if self._captured is not None:
            self._captured.append(str(getattr(messages[-1], "content", "")))
        return type("Response", (), {"content": self._content})()


class VersionControlTests(unittest.TestCase):
    """验证提交前复核、并发保护和精确文件提交。"""

    def test_code_paths_exclude_platform_artifacts_but_keep_them_eligible(self) -> None:
        """验证业务代码口径排除 .devagentstudio 产物，但产物仍可提交。

        平台产物（规划文档、状态快照）要进版本、要能被追溯，所以必须在 eligible_paths 里；
        但它们不该让"用户改了代码"的提醒亮起来，所以要从 code_paths 里排除。
        """

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / ".devagentstudio" / "plans").mkdir(parents=True)
            (root / ".devagentstudio" / "plans" / "product-plan.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (root / ".devagentstudio" / "application-lifecycle.json").write_text(
                '{"revision": 2}\n', encoding="utf-8"
            )
            (root / "src").mkdir()
            (root / "src" / "App.tsx").write_text("changed\n", encoding="utf-8")

            snapshot = inspect_all_version_control(
                InspectAllVersionControlRequest(action="inspect_all", workspaceRoot=str(root))
            )

            self.assertIn("src/App.tsx", snapshot.eligible_paths)
            self.assertIn(".devagentstudio/plans/product-plan.json", snapshot.eligible_paths)
            self.assertIn(".devagentstudio/application-lifecycle.json", snapshot.eligible_paths)
            # 产物仍可提交（追溯能力不变），但不算业务代码。
            self.assertEqual(snapshot.code_paths, ["src/App.tsx"])

    def test_snapshot_reports_head_commit_message(self) -> None:
        """验证快照带上 HEAD 的提交信息，供界面说明自动提交存了什么。

        自动提交（模板 baseline、验收）由平台发起，用户没参与写信息；界面要把它显示
        出来，否则那个 commit 对用户是黑盒。
        """

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            snapshot = inspect_all_version_control(
                InspectAllVersionControlRequest(action="inspect_all", workspaceRoot=str(root))
            )
            # _init_repository 用的提交信息是 "initial"。
            self.assertEqual(snapshot.head_message, "initial")
            # 未建立基线时为空串，而不是抛错或占位文本。
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            self.assertEqual(
                inspect_all_version_control(
                    InspectAllVersionControlRequest(
                        action="inspect_all", workspaceRoot=str(root)
                    )
                ).head_message,
                "initial",
            )

    def test_code_paths_empty_when_only_artifacts_changed(self) -> None:
        """验证只有产物变更时业务代码口径为空 —— 迭代启动后角标不该亮。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / ".devagentstudio").mkdir()
            (root / ".devagentstudio" / "application-lifecycle.json").write_text(
                '{"revision": 2}\n', encoding="utf-8"
            )

            snapshot = inspect_all_version_control(
                InspectAllVersionControlRequest(action="inspect_all", workspaceRoot=str(root))
            )

            self.assertTrue(snapshot.eligible_paths, "产物仍应可提交")
            self.assertEqual(snapshot.code_paths, [], "只有产物变更时业务代码变更应为空")

    def test_commit_allows_platform_artifacts_with_trailing_whitespace(self) -> None:
        """验证平台产物里的行尾空格不会挡住提交。

        回归：设计版本提醒提交的是清一色 `.devagentstudio` 文件，而 AGENTS.md 由平台生成、
        曾因 `', '.join` 产出悬空逗号带出行尾空格，被提交前的 `git diff --check` 判为
        空白错误 —— 平台生成的内容触发平台自己的门禁、反过来挡住用户提交。
        业务代码仍照常做空白预检（见下一个用例）。
        """

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / ".devagentstudio").mkdir()
            # 模拟平台生成物：行尾带空格。
            (root / ".devagentstudio" / "AGENTS.md").write_text(
                "# 迭代上下文\n\n  - 信息项：, \n", encoding="utf-8"
            )
            paths = [".devagentstudio/AGENTS.md"]
            snapshot = self._inspect(root, paths)

            result = commit_version_control(
                CommitVersionControlRequest(
                    action="commit",
                    confirmed=True,
                    workspaceRoot=str(root),
                    requestedPaths=paths,
                    selectedPaths=paths,
                    expectedFingerprint=snapshot.fingerprint,
                    message="docs: 保存设计版本",
                )
            )

            self.assertEqual(result.committed_paths, paths)
            self.assertFalse(result.remaining_dirty)

    def test_commit_still_checks_whitespace_in_business_code(self) -> None:
        """验证业务代码的行尾空格仍被拦下 —— 排除产物不能顺手放过用户代码。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("bad trailing space \n", encoding="utf-8")
            snapshot = self._inspect(root, ["tracked.txt"])

            with self.assertRaisesRegex(VersionControlError, "空白"):
                commit_version_control(
                    CommitVersionControlRequest(
                        action="commit",
                        confirmed=True,
                        workspaceRoot=str(root),
                        requestedPaths=["tracked.txt"],
                        selectedPaths=["tracked.txt"],
                        expectedFingerprint=snapshot.fingerprint,
                        message="fix: 修改",
                    )
                )
            # 失败后必须把已暂存内容还原，不能污染用户的暂存区。
            self.assertEqual(self._git(root, "diff", "--cached", "--name-only").strip(), "")

    def test_inspect_filters_current_status_to_requested_paths(self) -> None:
        """验证只返回本轮变更集仍然存在的实际 Git 修改。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (root / "untracked.txt").write_text("new\n", encoding="utf-8")

            snapshot = inspect_version_control(
                InspectVersionControlRequest(
                    action="inspect",
                    workspaceRoot=str(root),
                    requestedPaths=["tracked.txt", "already-clean.txt"],
                )
            )

            self.assertEqual(snapshot.eligible_paths, ["tracked.txt"])
            self.assertEqual(snapshot.unavailable_paths, ["already-clean.txt"])
            self.assertTrue(snapshot.dirty)
            self.assertFalse(snapshot.has_staged_changes)

    def test_commit_only_selected_files_and_preserves_other_changes(self) -> None:
        """验证显式提交只包含用户选择文件并保留其他未提交内容。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (root / "other.txt").write_text("other change\n", encoding="utf-8")
            snapshot = self._inspect(root, ["tracked.txt", "other.txt"])

            result = commit_version_control(
                CommitVersionControlRequest(
                    action="commit",
                    confirmed=True,
                    workspaceRoot=str(root),
                    requestedPaths=["tracked.txt", "other.txt"],
                    selectedPaths=["tracked.txt"],
                    expectedFingerprint=snapshot.fingerprint,
                    message="fix: 完成二次修改",
                )
            )

            self.assertEqual(result.committed_paths, ["tracked.txt"])
            self.assertTrue(result.remaining_dirty)
            self.assertEqual(self._git(root, "show", "--format=", "--name-only").strip(), "tracked.txt")
            self.assertIn("other.txt", self._git(root, "status", "--short"))

    def test_commit_rejects_stale_fingerprint(self) -> None:
        """验证审阅后又发生变化时必须重新生成状态快照。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            target = root / "tracked.txt"
            target.write_text("changed\n", encoding="utf-8")
            snapshot = self._inspect(root, ["tracked.txt"])
            target.write_text("changed again\n", encoding="utf-8")

            with self.assertRaisesRegex(VersionControlError, "重新审阅"):
                commit_version_control(
                    CommitVersionControlRequest(
                        action="commit",
                        confirmed=True,
                        workspaceRoot=str(root),
                        requestedPaths=["tracked.txt"],
                        selectedPaths=["tracked.txt"],
                        expectedFingerprint=snapshot.fingerprint,
                        message="fix: 完成二次修改",
                    )
                )

    def test_commit_rejects_preexisting_staged_changes(self) -> None:
        """验证已有暂存内容不会被静默合并进自动建议提交。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            self._git(root, "add", "tracked.txt")
            snapshot = self._inspect(root, ["tracked.txt"])

            with self.assertRaisesRegex(VersionControlError, "已有暂存"):
                commit_version_control(
                    CommitVersionControlRequest(
                        action="commit",
                        confirmed=True,
                        workspaceRoot=str(root),
                        requestedPaths=["tracked.txt"],
                        selectedPaths=["tracked.txt"],
                        expectedFingerprint=snapshot.fingerprint,
                        message="fix: 完成二次修改",
                    )
                )

    def test_parent_repository_is_rejected(self) -> None:
        """验证子工作区不会误用上级目录中的 Git 仓库。"""

        with tempfile.TemporaryDirectory() as repository:
            root = self._init_repository(Path(repository))
            nested = root / "apps" / "demo"
            nested.mkdir(parents=True)
            (nested / "demo.txt").write_text("demo\n", encoding="utf-8")

            with self.assertRaisesRegex(VersionControlError, "父目录仓库"):
                self._inspect(nested, ["demo.txt"])

    def test_protocol_emits_capabilities_and_completed_payload(self) -> None:
        """验证独立动作公开 AG-UI 元数据并返回标准完成态。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            payload = {
                "threadId": "version-control-thread",
                "runId": "version-control-run",
                "forwardedProps": {
                    "versionControl": {
                        "action": "inspect",
                        "workspaceRoot": str(root),
                        "requestedPaths": ["tracked.txt"],
                    }
                },
            }

            chunks = asyncio.run(self._collect_stream(payload))

            self.assertEqual(version_control_capabilities()["endpoint"], "/version-control/run")
            self.assertIn("version-control", "".join(chunks))
            self.assertIn('"status":"completed"', "".join(chunks))

    async def _collect_stream(self, payload: dict[str, object]) -> list[str]:
        """收集异步 AG-UI 事件流以供协议断言。"""

        stream = build_version_control_ag_ui_stream(payload=payload)
        return [chunk async for chunk in stream]

    def test_capabilities_expose_summarize_action(self) -> None:
        """变更说明生成必须作为公开动作出现在能力清单里。"""

        self.assertIn("summarize", version_control_capabilities()["actions"])

    def test_summarize_prefills_business_summary_and_normalizes_it(self) -> None:
        """有改动时返回模型摘要，并去掉引号与换行。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (root / "brand-new.txt").write_text("new\n", encoding="utf-8")

            with self._stub_model('  "新增订单列表页，支持按状态筛选。"  '):
                result = asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            self.assertEqual(result.action, "summarize")
            self.assertEqual(result.change_summary, "新增订单列表页，支持按状态筛选。")

    def test_summarize_prompt_carries_business_artifacts_and_change_digest(self) -> None:
        """提示词要同时带上业务产物名与改动清单 —— 只有文件名写不出业务语言。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            # 未跟踪的**业务**文件：新建页面靠这条进清单。
            (root / "brand-new-page.tsx").write_text("export default 0\n", encoding="utf-8")
            artifact_dir = root / ".devagentstudio" / "plans"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "product-plan.json").write_text(
                json.dumps({"pages": [{"name": "订单列表", "description": "按状态筛选"}]}),
                encoding="utf-8",
            )
            (artifact_dir / "technical-plan.json").write_text(
                json.dumps(
                    {
                        "endpoints": [{"method": "GET", "path": "/orders"}],
                        "entities": [{"name": "订单"}],
                    }
                ),
                encoding="utf-8",
            )

            captured: list[str] = []

            def fake_create_chat_model(*_args, **_kwargs):
                """记录提示词并返回固定文本的假模型。"""

                return _StubModel("固定摘要", captured)

            with patch(
                "app.agents.model_factory.create_chat_model", fake_create_chat_model
            ):
                asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            prompt = captured[0]
            self.assertIn("订单列表", prompt, "提示词缺少产品规划的页面业务名")
            self.assertIn("GET /orders", prompt, "提示词缺少技术规划的接口")
            self.assertIn("订单", prompt, "提示词缺少技术规划的实体")
            self.assertIn("tracked.txt", prompt, "提示词缺少改动清单")
            self.assertIn(
                "brand-new-page.tsx（新增）", prompt, "未跟踪文件要标出，否则新建代码看不见"
            )
            self.assertNotIn(
                ".devagentstudio", prompt, "平台产物不是业务内容，不该进提示词"
            )

    def test_summarize_covers_committed_changes_not_just_working_tree(self) -> None:
        """回归：改动已在本地提交、工作区干净时，仍要生成摘要。

        这是实际踩到的坑。验收阶段的 `AcceptanceCommitDock` 会把这一轮代码自动提交，
        用户点「提交并推送」时工作区通常是**干净的**，改动全在本地提交里、只差推远端。
        早先按 `git status` 判断"有没有改动"，于是这种最常见的情形返回空摘要、输入框
        空白。基线必须是「远端已经到哪」（首次推送时是根提交），不是工作区。
        """

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            # 模拟验收自动提交：改动提交进本地，工作区随之变干净。
            (root / "src-page.tsx").write_text("export default 0\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "chore: 验收通过，保存当前版本代码")
            self.assertEqual(
                self._git(root, "status", "--porcelain").strip(), "", "前置：工作区应为干净"
            )

            with self._stub_model("新增首页与路由。"):
                result = asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            self.assertEqual(result.change_summary, "新增首页与路由。")

    def test_summarize_uses_remote_tracking_base_after_a_publish(self) -> None:
        """回归：发布过之后，基线是远端已有的提交，不是根提交。

        发布流程按显式 refspec 推送、**不配置 upstream**，所以 `@{u}` 永远取不到；能用的
        信号是 `git push` 留下的远端跟踪引用 `refs/remotes/devagentstudio-publish/<分支>`。
        若漏掉这条，第二次发布会把从模板初始化以来的历史全量重算一遍，摘要里会混进
        上一轮已经交付过的内容。
        """

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            remote = Path(workspace) / "remote.git"
            self._git(Path(workspace), "init", "--bare", str(remote))

            # 第一次发布：把当前内容推上去，留下跟踪引用。
            self._git(root, "remote", "add", PUBLISH_REMOTE_NAME, str(remote))
            self._git(root, "push", PUBLISH_REMOTE_NAME, "HEAD:refs/heads/v1_0", "--force")

            # 发布之后的新一轮改动。
            (root / "src-new-page.tsx").write_text("export default 0\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "feat: 新增页面")

            captured: list[str] = []

            def fake_create_chat_model(*_args, **_kwargs):
                """记录提示词并返回固定文本的假模型。"""

                return _StubModel("固定摘要", captured)

            with patch(
                "app.agents.model_factory.create_chat_model", fake_create_chat_model
            ):
                asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            prompt = captured[0]
            self.assertIn("src-new-page.tsx", prompt, "本次改动应进清单")
            # 基线之前的文件属于上一轮已交付内容，不该重新出现在本次摘要里。
            self.assertNotIn("tracked.txt", prompt, "已发布的旧改动不该重算进来")

    def test_summarize_ignores_platform_artifact_only_commits(self) -> None:
        """只有平台产物变化时不生成摘要 —— 那对用户不是"业务变更"。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            artifact_dir = root / ".devagentstudio" / "reports"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "test-report.md").write_text("通过\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "chore: 报告")

            def explode(*_args, **_kwargs):
                """只有平台产物时不该调用模型。"""

                raise AssertionError("只有平台产物时不应调用模型")

            with patch("app.agents.model_factory.create_chat_model", explode):
                result = asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            self.assertEqual(result.change_summary, "")

    def test_summarize_truncates_overlong_model_output(self) -> None:
        """模型超长输出要按输入框上限截断，否则前端会被 maxLength 截出半截话。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

            with self._stub_model("改" * 500):
                result = asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            self.assertEqual(len(result.change_summary), 200)

    def test_summarize_reads_text_from_reasoning_model_content_blocks(self) -> None:
        """推理模型的 content 列表混着 thinking block 与正文，必须只取正文。

        本项目实际用的 glm-5.2 会把思考过程作为 `{"type":"thinking", ...}` block 与正文
        并列返回（列表里还夹着空字符串元素）。按 `item["text"]` 自行拼接会把这些 block
        读成空串、把正文整个漏掉 —— 表现为"模型明明答了，摘要却是空的"。这里锁定
        必须走 `app.agents.messages._coerce_content_text` 的行为。
        """

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            reasoning_content = [
                "",
                {"thinking": "先分析改动范围…", "type": "thinking", "index": 0},
                "新增订单列表页，支持按状态筛选。",
            ]

            with patch(
                "app.agents.model_factory.create_chat_model",
                lambda *_a, **_k: _StubModel(reasoning_content),
            ):
                result = asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            self.assertEqual(result.change_summary, "新增订单列表页，支持按状态筛选。")
            self.assertNotIn("先分析", result.change_summary, "思考过程不能混进摘要")

    def test_summarize_degrades_to_empty_summary_without_changes(self) -> None:
        """没有未提交改动时不该调用模型，返回空摘要让用户自己写。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))

            def explode(*_args, **_kwargs):
                """模型被调用即失败：无改动时不该走到这里。"""

                raise AssertionError("无未提交改动时不应调用模型")

            with patch("app.agents.model_factory.create_chat_model", explode):
                result = asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            self.assertEqual(result.change_summary, "")

    def test_summarize_swallows_model_failure(self) -> None:
        """模型失败只降级为空摘要，绝不能把用户挡在提交弹窗外。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

            def explode(*_args, **_kwargs):
                """模拟模型侧异常。"""

                raise RuntimeError("模型不可用")

            with patch("app.agents.model_factory.create_chat_model", explode):
                result = asyncio.run(
                    summarize_version_control(
                        SummarizeVersionControlRequest(
                            action="summarize", workspaceRoot=str(root)
                        )
                    )
                )

            self.assertEqual(result.change_summary, "")

    def test_summarize_protocol_returns_completed_payload(self) -> None:
        """summarize 走标准 AG-UI 生命周期并回传 summary 字段。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            payload = {
                "threadId": "summarize-thread",
                "runId": "summarize-run",
                "forwardedProps": {
                    "versionControl": {
                        "action": "summarize",
                        "workspaceRoot": str(root),
                    }
                },
            }

            with self._stub_model("新增订单列表页。"):
                chunks = asyncio.run(self._collect_stream(payload))

            joined = "".join(chunks)
            self.assertIn('"status":"completed"', joined)
            self.assertIn("新增订单列表页。", joined)

    def _stub_model(self, text: str):
        """把模型调用替换为固定文本，测试不依赖真实模型。"""

        return patch(
            "app.agents.model_factory.create_chat_model",
            lambda *_args, **_kwargs: _StubModel(text),
        )

    def test_chinese_head_message_round_trips_through_the_git_runner(self) -> None:
        """中文提交信息经 Git 往返后必须原样返回。

        这条守的是编码：Windows 上 `text=True` 默认按本机 ANSI 代码页解码，而 Git 输出
        的是 UTF-8，于是「chore: 模板初始化」在界面上显示成乱码。修复是让经工作区进程
        登记执行的 Git 固定按 UTF-8 解码（见 `workspace_process_registry`）。
        """

        with tempfile.TemporaryDirectory() as directory:
            root = self._init_repository(Path(directory))
            message = "chore: 模板初始化"
            self._git(root, "commit", "--allow-empty", "-m", message)

            self.assertEqual(_read_head_message(root), message)

    def test_chinese_paths_round_trip_through_the_git_runner(self) -> None:
        """中文文件路径同样不能被按本机代码页解错。

        `git status --porcelain` 与 `rev-parse --show-toplevel` 的输出都含路径，
        工作区路径带中文时是同一类问题。
        """

        with tempfile.TemporaryDirectory() as directory:
            root = self._init_repository(Path(directory))
            (root / "中文目录").mkdir()
            (root / "中文目录" / "说明.md").write_text("内容\n", encoding="utf-8")

            snapshot = self._inspect(root, ["中文目录/说明.md"])

            self.assertIn("中文目录/说明.md", snapshot.files[0].path)

    def _init_repository(self, root: Path) -> Path:
        """创建具有基线提交的最小独立 Git 仓库。"""

        self._git(root, "init")
        self._git(root, "config", "user.email", "tests@example.com")
        self._git(root, "config", "user.name", "Tests")
        (root / "tracked.txt").write_text("base\n", encoding="utf-8")
        (root / "other.txt").write_text("other base\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "initial")
        return root

    def _inspect(self, root: Path, paths: list[str]):
        """用测试默认参数读取版本控制状态。"""

        return inspect_version_control(
            InspectVersionControlRequest(
                action="inspect",
                workspaceRoot=str(root),
                requestedPaths=paths,
            )
        )

    def _git(self, root: Path, *arguments: str) -> str:
        """运行测试限定的 Git 命令并返回标准输出。"""

        completed = subprocess.run(
            ["git", *arguments],
            cwd=str(root),
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr or completed.stdout)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
