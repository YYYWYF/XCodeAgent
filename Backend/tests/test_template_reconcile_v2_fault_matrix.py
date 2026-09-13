"""覆盖 V2 Reconcile 可在本地确定性执行的故障矩阵基础项。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.template_reconcile.executor_v2 import (
    WorkingCopyStoreV2,
    apply_working_copy_v2,
    restore_working_copy_v2,
)
from app.services.template_reconcile.runtime_v2 import (
    ReconcileV2RuntimeError,
    reconcile_run_gate,
)
from app.services.template_reconcile.state_v2 import load_template_state_v2
from app.services.workspace_bootstrap.models import TemplateStateError


class TemplateReconcileV2FaultMatrixTests(unittest.TestCase):
    """验证多文件 Apply、并发 Gate 与旧协议拒绝的最低故障矩阵。"""

    def test_apply_second_file_failure_restores_first_file(self) -> None:
        """第二个原子写入失败时，进程内恢复必须还原已写入的第一个文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "src/first.ts"
            second = root / "src/second.ts"
            first.parent.mkdir(parents=True)
            first.write_text("old-first", encoding="utf-8")
            second.write_text("old-second", encoding="utf-8")
            store = WorkingCopyStoreV2(root)
            store.entry("src/first.ts").working_content = "new-first"
            store.entry("src/first.ts").changed = True
            store.entry("src/second.ts").working_content = "new-second"
            store.entry("src/second.ts").changed = True
            from app.services.template_reconcile import executor_v2

            real_write = executor_v2._atomic_write
            calls = 0

            def fail_second(path: Path, content: str) -> None:
                """仅对 Apply 的第二次写入注入失败，恢复写入仍使用真实实现。"""

                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("apply file 2 crash")
                real_write(path, content)

            with patch("app.services.template_reconcile.executor_v2._atomic_write", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "file 2"):
                    apply_working_copy_v2(root, store)
            restore_working_copy_v2(root, store)
            self.assertEqual("old-first", first.read_text(encoding="utf-8"))
            self.assertEqual("old-second", second.read_text(encoding="utf-8"))

    def test_concurrent_retry_is_rejected_by_workspace_gate(self) -> None:
        """同一 Workspace 的第二个 Retry/Update 不能越过已持有的 Run Gate。"""

        with tempfile.TemporaryDirectory() as directory:
            with reconcile_run_gate(directory):
                with self.assertRaises(ReconcileV2RuntimeError):
                    with reconcile_run_gate(directory):
                        pass

    def test_legacy_template_state_is_rejected_without_migration(self) -> None:
        """旧 State 不能被读取、迁移或继续执行，必须要求重新初始化。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".xcodeagent/template-state.json"
            state.parent.mkdir()
            state.write_text('{"templateRevision":"legacy","managedFiles":{},"requested":{},"effective":{}}', encoding="utf-8")
            with self.assertRaises((TemplateStateError, ValueError)):
                load_template_state_v2(root)
