"""验证失败卡片拿到当次检查的完整日志，而非被截断的摘要。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.subgraphs.testing import _check_progress_snapshot_writer, _diagnostic_progress_reporter
from app.protocols.workflow.stream_events import integration_test_checks
from app.workspace.content_artifacts import read_artifact
from app.workspace.spec_documents import workflow_artifact_root


class TestErrorLogTests(unittest.TestCase):
    def test_live_and_final_results_reference_same_immutable_output(self) -> None:
        """跨片段保留原始输出，并且重测覆盖文件后仍能复制旧失败。"""
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            state = {"workspace": str(workspace)}
            runtime = workflow_artifact_root(state) / "runtime"
            runtime.mkdir(parents=True)
            stdout, stderr = runtime / "stdout.log", runtime / "stderr.log"
            output = "最早的错误\r\n" + "x" * (1024 * 1024) + "\n最后一行\n"
            stdout.write_bytes(output.encode("utf-8"))
            stderr.write_bytes("详细堆栈\n".encode("utf-8"))
            check = {
                "id": "frontend_build", "name": "前端构建", "passed": False,
                "evidence": "构建失败 " + "a" * 1000,
                "execution": {
                    "stdout_log_virtual": f"/{stdout.relative_to(workspace).as_posix()}",
                    "stderr_log_virtual": f"/{stderr.relative_to(workspace).as_posix()}",
                },
            }
            events = []
            with patch("app.graph.subgraphs.testing.get_stream_writer", return_value=events.append):
                reporter = _diagnostic_progress_reporter(state, _check_progress_snapshot_writer())
                reporter({"check": check, "status": "failed"})
            live = integration_test_checks(events[0])[0]
            final = integration_test_checks([check])[0]
            self.assertEqual(len(live["evidence"]), 1_000)
            self.assertEqual(live["errorLogReferences"], final["errorLogReferences"])

            stdout.write_text("新一轮输出", encoding="utf-8")
            text = "".join(read_artifact(workspace, ref) for ref in final["errorLogReferences"])
            self.assertEqual(
                text, f"前端构建\n{check['evidence']}\n\n--- stdout ---\n{output}\n\n--- stderr ---\n详细堆栈\n",
            )
