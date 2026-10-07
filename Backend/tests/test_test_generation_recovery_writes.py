"""验证恢复运行文件不会干扰单测生成，正式产物仍受写入保护。"""

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.test_generation.generator import (
    _internal_artifact_snapshot,
    generate_or_update_unit_tests_with_agent,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionLease,
    ExecutionLeaseStatus,
)
from app.persistence.execution_recovery import (
    insert_execution_with_lease,
    renew_execution_lease,
)


class TestGenerationRecoveryWritesTests(unittest.TestCase):
    """覆盖真实续租与测试生成并行写入时的安全边界。"""

    def _generate_during_renewal(self, extra_path: str | None = None) -> dict:
        """在合法测试生成期间真实续租，并按需模拟额外的越权写入。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "frontend/src/apis/orders.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const orders = [];\n", encoding="utf-8")
            now = datetime.now(timezone.utc)
            record = DurableExecutionRecord(
                run_id="test-generation-run", thread_id="test-generation-thread",
                workspace=workspace, execution_kind="workbench",
                first_node="unit_test", current_node="unit_test",
                status=DurableExecutionStatus.RUNNING,
                started_at=now, updated_at=now,
            )
            lease = ExecutionLease(
                run_id=record.run_id, owner_backend_instance_id="test-backend",
                owner_pid=os.getpid(), status=ExecutionLeaseStatus.ACTIVE,
                acquired_at=now, heartbeat_at=now,
                expires_at=now + timedelta(seconds=60),
            )
            asyncio.run(insert_execution_with_lease(record=record, lease=lease))

            def invoke(_payload: dict) -> str:
                """模拟合法模型输出；数据库变化由真实平台续租函数产生。"""

                moment = now + timedelta(seconds=5)
                renewed = asyncio.run(renew_execution_lease(
                    workspace=workspace, run_id=record.run_id,
                    owner_backend_instance_id=lease.owner_backend_instance_id,
                    heartbeat_at=moment, expires_at=moment + timedelta(seconds=60),
                ))
                self.assertTrue(renewed)
                test = root / "frontend/tests/orders.test.ts"
                test.parent.mkdir(parents=True)
                test.write_text("test('orders', () => expect(true).toBe(true));\n", encoding="utf-8")
                if extra_path:
                    extra = root / extra_path
                    extra.parent.mkdir(parents=True, exist_ok=True)
                    extra.write_text("unauthorized change\n", encoding="utf-8")
                return json.dumps({"status": "completed", "test_files": ["frontend/tests/orders.test.ts"]})

            bundle = SimpleNamespace(test_generation=SimpleNamespace(invoke=invoke))
            with patch("app.agents.create_agent_bundle", return_value=bundle):
                return generate_or_update_unit_tests_with_agent({
                    "workspace": workspace,
                    "unit_test_generation_context": {
                        "source_files": ["frontend/src/apis/orders.ts"],
                        "affected_layers": ["frontend"],
                    },
                }, workspace)

    def test_real_lease_renewal_does_not_fail_test_generation(self) -> None:
        """普通运行中的恢复心跳不得让合法单测生成失败。"""

        result = self._generate_during_renewal()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["test_files"], ["frontend/tests/orders.test.ts"])
        self.assertNotIn("unauthorized_paths", result["validation"])

    def test_renewal_does_not_hide_other_unauthorized_writes(self) -> None:
        """数据库例外不能放宽恢复目录、正式计划、源码或敏感配置的保护。"""

        for path in (
            ".devagentstudio/recovery/other.json",
            ".devagentstudio/recovery/nested/execution-recovery.sqlite",
            ".devagentstudio/plans/technical-plan.json",
            "frontend/src/apis/orders.ts",
            ".env",
        ):
            with self.subTest(path=path):
                result = self._generate_during_renewal(path)
                self.assertEqual(result["status"], "failed")
                self.assertIn(path, result["validation"]["unauthorized_paths"])

    def test_only_exact_database_and_sqlite_sidecars_are_excluded(self) -> None:
        """仅排除平台数据库及 SQLite 自有附属文件，不按目录或前缀放行。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / ".devagentstudio/recovery"
            root.mkdir(parents=True)
            runtime_names = (
                "execution-recovery.sqlite", "execution-recovery.sqlite-wal",
                "execution-recovery.sqlite-shm", "execution-recovery.sqlite-journal",
            )
            for name in (*runtime_names, "execution-recovery.sqlite.backup", "other.sqlite"):
                (root / name).write_bytes(b"runtime")
            snapshot = _internal_artifact_snapshot(workspace)

        self.assertEqual(set(snapshot), {
            ".devagentstudio/recovery/execution-recovery.sqlite.backup",
            ".devagentstudio/recovery/other.sqlite",
        })
