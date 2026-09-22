"""发起新迭代保留应用工程本体，只清空 .devagentstudio 规划产物。

新迭代是在已有代码上继续加功能，因此 frontend/backend/.git 与 template-state.json
必须原样保留——那份代码（模板 + 历次迭代累积的业务代码）就是产品本身。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.application_lifecycle import (
    create_application_lifecycle,
    write_application_lifecycle,
)
from app.services.iteration_service import (
    StartIterationRequest,
    generate_agents_context,
    start_iteration,
)
from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH

_PLANNING_DIRS = ("specs", "plans", "drafts", "checkpoints", "ui-design")


def _bootstrapped_workspace(workspace: Path) -> None:
    """构造一个已完成首次 Bootstrap 的工作区（工程本体 + 规划产物齐全）。"""

    devagentstudio_dir = workspace / ".devagentstudio"
    devagentstudio_dir.mkdir(parents=True, exist_ok=True)
    (devagentstudio_dir / "AGENTS.md").write_text("# 迭代上下文\n", encoding="utf-8")
    (devagentstudio_dir / "application.json").write_text(
        json.dumps({"id": "app-1", "source": "new"}), encoding="utf-8"
    )
    # 上一轮迭代遗留的规划产物。
    for name in _PLANNING_DIRS:
        (devagentstudio_dir / name).mkdir(parents=True, exist_ok=True)
        (devagentstudio_dir / name / "artifact.json").write_text("{}", encoding="utf-8")
    # 真实 lifecycle 快照：start_iteration 会先读它收口遗留 execution，假 JSON 会被判为损坏。
    write_application_lifecycle(
        workspace,
        create_application_lifecycle(application_id="app-1", application_name="测试应用"),
    )
    (devagentstudio_dir / "application-lifecycle.json.bak").write_text("{}", encoding="utf-8")
    # 应用工程本体：模板受管根、基线仓库与模板状态标记。
    for name in ("frontend", "backend"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
        (workspace / name / "index.txt").write_text("template", encoding="utf-8")
    (workspace / ".git").mkdir(parents=True, exist_ok=True)
    (workspace / TEMPLATE_STATE_RELATIVE_PATH).write_text('{"schemaVersion": 2}', encoding="utf-8")
    # 模板随工程交付的契约（构建期 Route Projection 依赖它）。
    contracts = devagentstudio_dir / "template-contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    (contracts / "route-projector.json").write_text('{"schemaVersion": "route-projector-contract.v2"}', encoding="utf-8")


def _request(workspace: Path) -> StartIterationRequest:
    return StartIterationRequest(
        action="start_iteration",
        workspaceRoot=str(workspace),
        versionLabel="v1.1",
        description="",
    )


class StartIterationCleanupTests(unittest.TestCase):
    def test_preserves_application_project_artifacts(self) -> None:
        """工程本体必须原样保留：新迭代沿用已有代码，不再重新拉取模板。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _bootstrapped_workspace(workspace)

            start_iteration(_request(workspace))

            for name in ("frontend", "backend", ".git"):
                self.assertTrue((workspace / name).is_dir(), f"{name} 被误删")
                self.assertTrue((workspace / name / "index.txt").exists() or name == ".git")
            self.assertTrue(
                (workspace / TEMPLATE_STATE_RELATIVE_PATH).is_file(),
                "template-state.json 被删除：下一次 Bootstrap 会误判为未物化并重新拉取模板",
            )

    def test_preserves_template_contracts(self) -> None:
        """模板随工程交付的契约必须保留。

        `.devagentstudio` 同时承载平台数据与模板契约；新迭代不再重新物化，被删掉的契约
        没有任何东西能补回来，构建期会以"模板缺少 Route Projector Descriptor"暴露。
        """

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _bootstrapped_workspace(workspace)

            start_iteration(_request(workspace))

            contract = workspace / ".devagentstudio" / "template-contracts" / "route-projector.json"
            self.assertTrue(contract.is_file(), "模板契约被删除：构建期 Route Projection 会失败")

    def test_preserves_unlisted_entries(self) -> None:
        """未登记的平台未知条目一律保留，避免再次误删模板交付物。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _bootstrapped_workspace(workspace)
            unknown = workspace / ".devagentstudio" / "some-template-delivery"
            unknown.mkdir()
            (unknown / "descriptor.json").write_text("{}", encoding="utf-8")

            start_iteration(_request(workspace))

            self.assertTrue((unknown / "descriptor.json").is_file())

    def test_preserves_iteration_context_files(self) -> None:
        """AGENTS.md 与 application.json 是下一轮迭代的起点，必须保留。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _bootstrapped_workspace(workspace)

            start_iteration(_request(workspace))

            self.assertTrue((workspace / ".devagentstudio" / "AGENTS.md").is_file())
            self.assertTrue((workspace / ".devagentstudio" / "application.json").is_file())

    def test_clears_previous_planning_artifacts(self) -> None:
        """上一轮的规划产物与 lifecycle 快照不能带进新迭代。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _bootstrapped_workspace(workspace)

            start_iteration(_request(workspace))

            devagentstudio_dir = workspace / ".devagentstudio"
            self.assertFalse((devagentstudio_dir / "application-lifecycle.json").exists())
            self.assertFalse((devagentstudio_dir / "application-lifecycle.json.bak").exists())
            for name in _PLANNING_DIRS:
                self.assertFalse((devagentstudio_dir / name).exists(), f"{name} 未被清除")


class PreviewShutdownOrderTests(unittest.TestCase):
    """预览进程必须在清理前停止，否则会变成无法停止的孤儿。"""

    def _workspace_with_preview(self, workspace: Path) -> Path:
        _bootstrapped_workspace(workspace)
        pid_dir = workspace / ".devagentstudio" / "runtime" / "launch"
        pid_dir.mkdir(parents=True, exist_ok=True)
        (pid_dir / "frontend.pid").write_text("12345", encoding="utf-8")
        return pid_dir

    def test_preview_is_stopped_before_any_cleanup(self) -> None:
        """停预览时 PID 文件必须还在——顺序反了进程就永远停不掉。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            pid_dir = self._workspace_with_preview(workspace)
            observed: dict[str, bool] = {}

            def spy(_root: Path) -> dict[str, object]:
                observed["pid_file_exists"] = (pid_dir / "frontend.pid").is_file()
                return {"status": "stopped"}

            with patch("app.services.project_launcher.stop_project_preview", side_effect=spy):
                start_iteration(_request(workspace))

            self.assertTrue(
                observed.get("pid_file_exists"),
                "停预览时 PID 文件已被删除：进程会成为无法停止的孤儿",
            )

    def test_preview_stop_failure_does_not_block_iteration(self) -> None:
        """预览停止失败不应阻断迭代清理。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            self._workspace_with_preview(workspace)

            with patch(
                "app.services.project_launcher.stop_project_preview",
                return_value={"status": "failed", "message": "boom"},
            ):
                result = start_iteration(_request(workspace))

            self.assertTrue(result.cleared)
            self.assertTrue((workspace / "frontend").is_dir())


if __name__ == "__main__":
    unittest.main()


class AgentsContextGenerationTests(unittest.TestCase):
    """AGENTS.md 由平台生成，不该带出行尾空白 —— 那会被提交前预检判为空白错误。"""

    def test_generated_context_has_no_trailing_whitespace(self) -> None:
        """回归：信息项名称为空时 ', '.join 产出悬空 ", "，行尾空格挡住用户提交。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            devagentstudio = workspace / ".devagentstudio"
            (devagentstudio / "plans").mkdir(parents=True)
            (devagentstudio / "specs").mkdir(parents=True)
            # 两个信息项名称都为空 —— 正是产出悬空逗号的输入。
            (devagentstudio / "plans" / "product-plan.json").write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "page_id": "page_home",
                                "name": "Hello World 欢迎页",
                                "path": "/page/page-home",
                                "description": "展示 hello world",
                                "goal": "让访客看到 hello world",
                                "information_items": [{"name": ""}, {"name": ""}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            generate_agents_context(workspace, version_label="v1.0", description="首个版本")

            content = (devagentstudio / "AGENTS.md").read_text(encoding="utf-8")
            offenders = [
                (index, line)
                for index, line in enumerate(content.split("\n"), start=1)
                if line != line.rstrip()
            ]
            self.assertEqual(offenders, [], f"生成内容含行尾空白：{offenders}")
            # 名称为空时整行不输出，而不是留下 "信息项：, "。
            self.assertNotIn("信息项", content)

    def test_keeps_named_information_items(self) -> None:
        """有名称的信息项照常输出 —— 过滤空名不能把正常内容也丢掉。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            devagentstudio = workspace / ".devagentstudio"
            (devagentstudio / "plans").mkdir(parents=True)
            (devagentstudio / "plans" / "product-plan.json").write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "page_id": "page_home",
                                "name": "首页",
                                "information_items": [{"name": "标题"}, {"name": ""}, {"name": "正文"}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            generate_agents_context(workspace, version_label="v1.0", description="首个版本")

            content = (devagentstudio / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("信息项：标题, 正文", content)
