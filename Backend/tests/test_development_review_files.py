"""开发阶段 Diff 审查文件清单的边界测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.graph.nodes.code_review import review_phase_confirmation
from app.graph.nodes.lifecycle import test_phase_confirmation
from app.protocols.workflow.runtime import _source_development_review_files
from app.services.development_review_files import development_review_files


class DevelopmentReviewFilesTests(unittest.TestCase):
    """确保审查文件清单只来自开发 Diff 的可读文件。"""

    def test_deduplicates_and_excludes_deleted_binary_and_unsafe_files(self) -> None:
        """同一路径采用最终变更状态，危险路径不能进入审查。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            java = root / "backend/src/main/java/demo/App.java"
            manifest = root / "frontend/package.json"
            java.parent.mkdir(parents=True)
            manifest.parent.mkdir(parents=True)
            java.write_text("class App {}", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            changes = {
                "workspaceRoot": str(root.resolve()),
                "files": [
                    {"path": "backend/src/main/java/demo/App.java", "changeType": "modified"},
                    {"path": "backend/src/main/java/demo/App.java", "changeType": "modified"},
                    {"path": "frontend/package.json", "changeType": "added"},
                    {"path": "frontend/src/Old.tsx", "changeType": "deleted"},
                    {"path": "backend/src/main/java/demo/Other.java", "binary": True},
                    {"path": "frontend/.env", "changeType": "modified"},
                    {"path": "../private.java", "changeType": "modified"},
                ],
            }

            self.assertEqual(
                development_review_files(changes, root),
                ["backend/src/main/java/demo/App.java", "frontend/package.json"],
            )

    def test_rejects_another_workspace(self) -> None:
        """客户端或其他应用的变更集合不能成为当前工作区扫描依据。"""

        with tempfile.TemporaryDirectory() as workspace:
            self.assertEqual(
                development_review_files(
                    {"workspaceRoot": "/another", "files": [{"path": "frontend/package.json"}]},
                    workspace,
                ),
                [],
            )

    def test_review_gate_exposes_file_count_and_persists_mode(self) -> None:
        """审查入口按真实可读文件展示 Diff 可用性并保存选择。"""

        with tempfile.TemporaryDirectory() as workspace:
            manifest = Path(workspace) / "frontend/package.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")
            state = {
                "workspace": workspace,
                "quality_gate_passed": True,
                "development_review_files": ["frontend/package.json"],
            }
            waiting = review_phase_confirmation(state)
            confirmed = review_phase_confirmation({
                **state,
                "review_phase_confirmation": {"action": "confirm", "reviewMode": "diff"},
            })

        self.assertEqual(waiting["clarification"]["diffReviewFileCount"], 1)
        self.assertEqual(confirmed["code_review_mode"], "diff")

    def test_development_gate_freezes_final_code_change_paths(self) -> None:
        """进入测试前固定开发最终 Diff 的路径，后续阶段无需重新计算。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "backend/src/main/java/App.java"
            source.parent.mkdir(parents=True)
            source.write_text("class App {}", encoding="utf-8")
            gate = SimpleNamespace(allowed=True, reason="", model_dump=lambda **_kwargs: {})
            with patch("app.graph.nodes.lifecycle._completed_build_summary", return_value={"status": "completed"}), patch(
                "app.graph.nodes.lifecycle.complete_initial_development", return_value={}
            ), patch("app.graph.nodes.lifecycle.test_entry_gate", return_value=gate):
                result = test_phase_confirmation({
                    "workspace": workspace,
                    "unit_test_gate_passed": True,
                    "code_changes": {
                        "workspaceRoot": str(root.resolve()),
                        "files": [{"path": "backend/src/main/java/App.java", "changeType": "modified"}],
                    },
                })

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["development_review_files"], ["backend/src/main/java/App.java"])


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
