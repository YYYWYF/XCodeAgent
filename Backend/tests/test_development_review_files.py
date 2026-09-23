"""开发阶段 Diff 审查文件清单的边界测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.graph.nodes.code_review import review_phase_confirmation
from app.graph.nodes.lifecycle import test_phase_confirmation
from app.protocols.workflow.runtime import _source_development_review_files
from app.services.development_review_files import development_review_files, development_review_selection


class DevelopmentReviewFilesTests(unittest.TestCase):
    """确保审查清单与顶部已完成模块使用同一份正式 Build 计划。"""

    def _write_plan(self, root: Path, units: dict, tasks: dict) -> None:
        """在临时工作区写入正式 Build 任务计划。"""

        plan = root / ".devagentstudio/plans/build-task-plan.json"
        plan.parent.mkdir(parents=True)
        plan.write_text(json.dumps({"build_units": units, "task_registry": tasks}), encoding="utf-8")

    def test_uses_all_completed_module_files_and_excludes_unsafe_files(self) -> None:
        """一个文件未提交时纳入模块的全部目标文件，含后端配置和资源。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            java = root / "backend/src/main/java/demo/App.java"
            manifest = root / "frontend/package.json"
            pom = root / "backend/pom.xml"
            config = root / "backend/src/main/resources/application.yml"
            binary = root / "backend/src/main/resources/logo.bin"
            java.parent.mkdir(parents=True)
            manifest.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            java.write_text("class App {}", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            pom.write_text("<project/>", encoding="utf-8")
            config.write_text("app: ready", encoding="utf-8")
            binary.write_bytes(b"\x00\x01")
            self._write_plan(root, {"backend:bootstrap": {"task_ids": ["t1"]}}, {
                "t1": {"status": "completed", "target_files": [
                    "backend/pom.xml", "backend/src/main/resources/application.yml",
                    "backend/src/main/java/demo/App.java", "backend/pom.xml",
                    "frontend/package.json", "frontend/.env", "../private.java",
                    "backend/src/main/resources/missing.xml",
                    "backend/src/main/resources/logo.bin",
                ]},
            })
            snapshot = SimpleNamespace(eligible_paths=["backend/pom.xml"])
            with patch("app.services.development_review_files.inspect_all_version_control", return_value=snapshot):
                self.assertEqual(development_review_files(root), [
                    "backend/pom.xml", "backend/src/main/java/demo/App.java",
                    "backend/src/main/resources/application.yml", "frontend/package.json",
                ])
                self.assertEqual(development_review_selection(root)[1], [
                    "backend/src/main/resources/logo.bin",
                    "backend/src/main/resources/missing.xml",
                ])

    def test_unfinished_or_committed_module_is_not_in_dropdown_scope(self) -> None:
        """任务未全部完成或没有未提交目标文件时不进入 Diff 清单。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            for name in ("one.ts", "two.ts"):
                source = root / "frontend/src" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("export {}", encoding="utf-8")
            self._write_plan(root, {
                "one": {"task_ids": ["t1"]}, "two": {"task_ids": ["t2"]},
            }, {
                "t1": {"status": "completed", "target_files": ["frontend/src/one.ts"]},
                "t2": {"status": "pending", "target_files": ["frontend/src/two.ts"]},
            })
            with patch("app.services.development_review_files.inspect_all_version_control", return_value=SimpleNamespace(eligible_paths=["frontend/src/two.ts"])):
                self.assertEqual(development_review_files(root), [])

    def test_review_gate_exposes_file_count_and_persists_mode(self) -> None:
        """审查入口按真实可读文件展示 Diff 可用性并保存选择。"""

        with tempfile.TemporaryDirectory() as workspace:
            manifest = Path(workspace) / "frontend/package.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")
            state = {
                "workspace": workspace,
                "quality_gate_passed": True,
                "development_review_files": ["frontend/src/Old.tsx"],
            }
            self._write_plan(Path(workspace), {"frontend": {"task_ids": ["t1"]}}, {
                "t1": {"status": "completed", "target_files": ["frontend/package.json"]},
            })
            with patch("app.services.development_review_files.inspect_all_version_control", return_value=SimpleNamespace(eligible_paths=["frontend/package.json"])):
                waiting = review_phase_confirmation(state)
                confirmed = review_phase_confirmation({
                    **state,
                    "review_phase_confirmation": {"action": "confirm", "reviewMode": "diff"},
                })

        self.assertEqual(waiting["clarification"]["diffReviewFileCount"], 1)
        self.assertEqual(confirmed["code_review_mode"], "diff")
        self.assertEqual(confirmed["development_review_files"], ["frontend/package.json"])

    def test_development_gate_freezes_completed_module_paths(self) -> None:
        """进入测试前固定全部已完成模块路径，而非当前会话的 CodeChanges。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "backend/src/main/java/App.java"
            pom = root / "backend/pom.xml"
            source.parent.mkdir(parents=True)
            source.write_text("class App {}", encoding="utf-8")
            pom.write_text("<project/>", encoding="utf-8")
            self._write_plan(root, {"backend": {"task_ids": ["t1"]}}, {
                "t1": {"status": "completed", "target_files": [
                    "backend/src/main/java/App.java", "backend/pom.xml",
                ]},
            })
            gate = SimpleNamespace(allowed=True, reason="", model_dump=lambda **_kwargs: {})
            with patch("app.graph.nodes.lifecycle._completed_build_summary", return_value={"status": "completed"}), patch(
                "app.graph.nodes.lifecycle.complete_initial_development", return_value={}
            ), patch("app.graph.nodes.lifecycle.test_entry_gate", return_value=gate), patch(
                "app.services.development_review_files.inspect_all_version_control",
                return_value=SimpleNamespace(eligible_paths=["backend/src/main/java/App.java"]),
            ):
                result = test_phase_confirmation({
                    "workspace": workspace,
                    "unit_test_gate_passed": True,
                    "development_review_files": [],
                    "code_changes": {
                        "workspaceRoot": str(root.resolve()),
                        "files": [{"path": "backend/src/main/java/App.java", "changeType": "modified"}],
                    },
                })

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["development_review_files"], [
            "backend/pom.xml", "backend/src/main/java/App.java",
        ])


class DevelopmentReviewTransferTests(unittest.IsolatedAsyncioTestCase):
    """跨阶段只读取前一执行在服务端保存的文件清单。"""

    async def test_reads_previous_thread_checkpoint(self) -> None:
        """源执行的 thread ID 来自服务端生命周期。"""

        class FakeGraph:
            """记录查询的 checkpoint thread。"""

            async def aget_state(self, config):
                """返回开发阶段固定的文件清单。"""

                self.thread_id = config["configurable"]["thread_id"]
                return SimpleNamespace(values={"development_review_files": ["frontend/package.json"]})

        graph = FakeGraph()
        lifecycle = SimpleNamespace(active_executions={
            "development-run": SimpleNamespace(thread_id="development-thread")
        })
        with patch("app.protocols.workflow.runtime.load_application_lifecycle", return_value=lifecycle):
            files = await _source_development_review_files(
                graph, "/workspace", "development-run"
            )

        self.assertEqual(graph.thread_id, "development-thread")
        self.assertEqual(files, ["frontend/package.json"])

    async def test_missing_source_never_uses_client_diff(self) -> None:
        """没有有效服务端源执行时仅返回空清单。"""

        lifecycle = SimpleNamespace(active_executions={})
        with patch("app.protocols.workflow.runtime.load_application_lifecycle", return_value=lifecycle):
            self.assertEqual(
                await _source_development_review_files(object(), "/workspace", "missing"),
                [],
            )


if __name__ == "__main__":
    unittest.main()
