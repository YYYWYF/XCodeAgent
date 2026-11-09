from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.workspace_bootstrap.run_log import TemplateRunLog


class TemplateRunLogTests(unittest.TestCase):
    """验证 Bootstrap 日志的隔离、脱敏和非致命写入/清理语义。"""

    def test_wrapped_failure_keeps_original_stage_and_one_traceback(self) -> None:
        """跨层包装保留同一错误引用、底层系统错误码和精确阶段。"""

        with tempfile.TemporaryDirectory() as directory:
            run_log = TemplateRunLog.create(directory)
            try:
                with run_log.stage("template_engine_generate", "生成模板"):
                    try:
                        raise ConnectionRefusedError(61, "connection refused")
                    except OSError as exc:
                        run_log.event(stage="template_request", event="stage_failed",
                                      message="连接失败", level="ERROR", exception=exc)
                        raise RuntimeError("生成失败") from exc
            except RuntimeError as exc:
                run_log.finish_failure(exc)
            content = run_log.path.read_text(encoding="utf-8")
            self.assertEqual(content.count("\ntraceback:\n"), 1)
            self.assertEqual(content.count('errorRef="error-1"'), 3)
            self.assertIn('stage="template_request" event="bootstrap_failed"', content)
            self.assertIn("errno=61", content)
            self.assertIn('rootExceptionType="ConnectionRefusedError"', content)

    def test_failure_retains_full_traceback_and_redacts_sensitive_values(self) -> None:
        """失败日志保留完整堆栈，同时脱敏 URL 凭据和敏感字段。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            run_log = TemplateRunLog.create(workspace)
            try:
                with run_log.stage(
                    "template_request",
                    "请求模板引擎",
                    endpoint="https://engine-user:engine-pass@example.test/generate?token=query-secret",
                    token="field-secret",
                    config={
                        "privateKey": "private-key-secret with spaces",
                        "publicKey": "-----BEGIN PUBLIC KEY-----\npublic-key-secret\n-----END PUBLIC KEY-----",
                        "clientId": "client-id-secret",
                    },
                ):
                    raise RuntimeError("request failed token=body-secret password=pass-secret")
            except RuntimeError as exc:
                run_log.finish_failure(exc)

            content = run_log.path.read_text(encoding="utf-8")
            self.assertIn("Traceback (most recent call last)", content)
            self.assertIn("RuntimeError", content)
            self.assertIn("stage=\"template_request\"", content)
            self.assertIn("[REDACTED]", content)
            for secret in (
                "engine-user",
                "engine-pass",
                "query-secret",
                "field-secret",
                "body-secret",
                "pass-secret",
                "private-key-secret",
                "public-key-secret",
                "client-id-secret",
            ):
                self.assertNotIn(secret, content)

    def test_success_deletes_only_its_own_execution_log(self) -> None:
        """成功清理只删除当前执行日志，另一任务的失败日志继续保留。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            succeeded = TemplateRunLog.create(workspace)
            failed = TemplateRunLog.create(workspace)
            self.assertNotEqual(succeeded.execution_id, failed.execution_id)
            succeeded.event(stage="bootstrap", event="stage_started", message="started")
            failed.event(stage="bootstrap", event="stage_started", message="started")

            succeeded.finish_success(final_path=str(workspace))
            failed.finish_failure(RuntimeError("expected failure"))

            self.assertFalse(succeeded.path.exists())
            self.assertTrue(failed.path.is_file())
            self.assertEqual([failed.path], list(failed.path.parent.glob("*.log")))

    def test_delete_failure_is_recorded_without_raising(self) -> None:
        """成功后的日志删除失败仅留下清理告警，不改变成功分支。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            run_log = TemplateRunLog.create(workspace)
            run_log.event(stage="bootstrap", event="stage_started", message="started")
            original_unlink = Path.unlink

            def fail_current_log(path: Path, *args: object, **kwargs: object) -> None:
                """只模拟本次日志文件删除失败，其他路径仍走原实现。"""

                if path == run_log.path:
                    raise PermissionError("log handle is locked")
                original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", new=fail_current_log):
                run_log.finish_success(final_path=str(workspace))

            content = run_log.path.read_text(encoding="utf-8")
            self.assertIn('event="bootstrap_succeeded"', content)
            self.assertIn('event="log_cleanup_failed"', content)
            self.assertIn("Traceback (most recent call last)", content)

    def test_write_failure_disables_logging_without_raising(self) -> None:
        """日志写盘异常会关闭该日志器，不向模板初始化调用方传播。"""

        class BrokenStream:
            """模拟不可写文件句柄。"""

            def write(self, _value: str) -> None:
                """模拟磁盘写入失败。"""

                raise OSError("disk is full")

            def close(self) -> None:
                """模拟句柄关闭。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            run_log = TemplateRunLog.create(workspace)
            run_log._stream.close()
            run_log._stream = BrokenStream()

            run_log.event(stage="template_download", event="download_started", message="download")
            run_log.finish_failure(RuntimeError("business failure"))

            self.assertIsNone(run_log._stream)
            self.assertTrue(run_log.path.exists())

    def test_success_never_deletes_a_log_file_it_did_not_create(self) -> None:
        """日志文件创建失败时，成功清理不能删除相同路径上的其他文件。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            run_log = TemplateRunLog.create(workspace)
            run_log.close()
            run_log._created = False

            run_log.finish_success(final_path=str(workspace))

            self.assertTrue(run_log.path.is_file())


if __name__ == "__main__":
    unittest.main()
