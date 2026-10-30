"""Runtime 启动事实和不可收口进程回收的定向回归，不启动业务或模型。"""

import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.services.agent_runtime_cleanup import runtime_cleanup_observer
from app.services.agent_runtime_health import runtime_health
from app.services.runtime_log_redaction import _copy_redacted


class RuntimeExecutionGuardTests(unittest.TestCase):
    """验证受管应用/namespace 一致性和故障时的单次回收。"""

    def test_health_rejects_false_or_mismatched_deployment_facts(self):
        """启动身份、进程锁、容量闸门和摘要必须同时匹配，布尔值不能冒充数字。"""
        root = Path("/tmp/runtime-application/agent-runtime")
        namespace = "probe:current"
        facts = {"workers": 1, "processId": 123, "profile": "probe", "processLockHeld": True,
                 "acceptingRuns": True, "applicationSha256": hashlib.sha256(str(root.resolve()).encode()).hexdigest(),
                 "namespaceSha256": hashlib.sha256(namespace.encode()).hexdigest()}
        cases = [(None, None, True), ("workers", 2, False), ("workers", True, False),
                 ("processId", True, False), ("processLockHeld", False, False), ("acceptingRuns", False, False),
                 ("profile", "preview", False), ("applicationSha256", "a" * 64, False),
                 ("namespaceSha256", "b" * 64, False)]
        with patch("app.services.agent_runtime_health.httpx.Client") as client:
            response = client.return_value.__enter__.return_value.get.return_value
            response.status_code = 200
            for field, value, expected in cases:
                with self.subTest(field=field, value=value):
                    response.json.return_value = {"service": "agent-runtime", "launchId": "current", "profile": "probe",
                        "agentIds": ["chat"], "status": "ok", "deployment": {**facts, **({field: value} if field else {})},
                        "checks": {"businessApi": "ready", "agUi": "ready", "database": "not_required",
                                   "anonymousSession": "ready", "deployment": "ready", "execution": "ready"}}
                    ready, _ = runtime_health("http://127.0.0.1:1234", "current", database_required=False,
                        profile="probe", expected_agent_ids=["chat"], expected_application=root, expected_namespace=namespace)
                    self.assertEqual(ready, expected)

    def test_cleanup_marker_reaps_only_the_bound_process_once(self):
        """重复或无关日志不得重复回收，不可从日志自报新的 PID 或工作区。"""
        with tempfile.TemporaryDirectory() as directory:
            root, process = Path(directory), Mock()
            stderr = root / "stderr.log"
            with patch("app.services.agent_runtime_cleanup.terminate_runtime_process") as terminate:
                observe = runtime_cleanup_observer(root, process, stderr)
                observe("无关日志 AGENT_RUNTIME_CLEANUP_EXHAUSTED pid=123")
                terminate.assert_not_called()
                observe("AGENT_RUNTIME_CLEANUP_EXHAUSTED\n")
                observe("CRITICAL:app.server.execution:AGENT_RUNTIME_CLEANUP_EXHAUSTED\n")
                terminate.assert_called_once_with(root, process)
            self.assertIn("未自动重启", stderr.read_text())

    def test_cleanup_failure_is_recorded_without_retry(self):
        """进程无法回收时保留具体失败，停止自动操作。"""
        with tempfile.TemporaryDirectory() as directory:
            stderr = Path(directory) / "stderr.log"
            with patch("app.services.agent_runtime_cleanup.terminate_runtime_process", side_effect=RuntimeError("进程仍存活")) as terminate:
                observe = runtime_cleanup_observer(Path(directory), Mock(), stderr)
                observe("AGENT_RUNTIME_CLEANUP_EXHAUSTED")
                observe("AGENT_RUNTIME_CLEANUP_EXHAUSTED")
                terminate.assert_called_once()
            self.assertIn("RuntimeError: 进程仍存活", stderr.read_text())

    def test_log_observer_receives_only_redacted_text(self):
        """回收控制仍沿现有脱敏管道，不把原始凭据交给观察器或磁盘。"""
        with tempfile.TemporaryDirectory() as directory:
            path, seen = Path(directory) / "runtime.log", []
            _copy_redacted(BytesIO(b"password=test-runtime-password\n"), path,
                           {"MYSQL_PWD": "test-runtime-password"}, seen.append)
            self.assertNotIn("test-runtime-password", path.read_text())
            self.assertNotIn("test-runtime-password", "".join(seen))
