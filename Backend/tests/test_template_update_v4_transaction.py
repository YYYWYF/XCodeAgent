"""V4 文件事务中断后的原始字节恢复。"""

from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.template_reconcile.executor_v3 import WorkingCopyStoreV3
from app.services.template_reconcile.transaction_v4 import apply_working_copy_v4, recover_v4_transaction
from app.services.workspace_bootstrap.models import TemplateStateError


class TemplateUpdateV4TransactionTests(unittest.TestCase):
    """覆盖普通成功、写入故障和冲突保护。"""

    def setUp(self) -> None:
        """建立包含两个受管目标的临时工作区。"""

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / ".devagentstudio").mkdir()
        self.first = self.root / "frontend/src/extensions/providers.ts"
        self.second = self.root / ".devagentstudio/template-state.json"
        self.first.parent.mkdir(parents=True)
        self.first.write_bytes(b"before-front")
        self.second.write_bytes(b"before-state")

    def tearDown(self) -> None:
        """释放临时目录。"""

        self.temporary.cleanup()

    def _store(self) -> WorkingCopyStoreV3:
        """在内存副本中准备两个受管目标。"""

        store = WorkingCopyStoreV3(self.root)
        store.entry("frontend/src/extensions/providers.ts").working = b"after-front"
        store.entry(".devagentstudio/template-state.json").working = b"after-state"
        return store

    def test_success_commits_both_and_removes_journal(self) -> None:
        """成功后两个文件均更新且没有待恢复 journal。"""

        apply_working_copy_v4(self.root, self._store())
        self.assertEqual(self.first.read_bytes(), b"after-front")
        self.assertEqual(self.second.read_bytes(), b"after-state")
        self.assertFalse((self.root / ".devagentstudio/runtime/template-update-v4-journal").exists())

    def test_write_failure_restores_before_images(self) -> None:
        """第二文件故障时两个文件回到原始字节。"""

        from app.services.template_reconcile import transaction_v4

        original = transaction_v4._atomic_replace

        def inject(target: Path, content: bytes, *, mode: int | None) -> None:
            """仅在提交第二个实际目标时模拟磁盘故障。"""

            if target.resolve() == self.first.resolve() and content == b"after-front":
                raise OSError("injected")
            original(target, content, mode=mode)

        with patch.object(transaction_v4, "_atomic_replace", side_effect=inject):
            with self.assertRaises(OSError):
                apply_working_copy_v4(self.root, self._store())
        self.assertEqual(self.first.read_bytes(), b"before-front")
        self.assertEqual(self.second.read_bytes(), b"before-state")

    def test_preflight_rejects_external_edit(self) -> None:
        """目标在准备后变化时不得写入 State。"""

        store = self._store()
        self.first.write_bytes(b"external")
        with self.assertRaises(TemplateStateError):
            apply_working_copy_v4(self.root, store)
        self.assertEqual(self.second.read_bytes(), b"before-state")

    def test_recovers_interrupted_prepared_transaction(self) -> None:
        """重启后按持久 before-image 恢复已写入的一半目标。"""

        journal = self.root / ".devagentstudio/runtime/template-update-v4-journal"
        journal.mkdir(parents=True)
        (journal / "0.before").write_bytes(b"before-state")
        (journal / "0.after").write_bytes(b"after-state")
        (journal / "1.before").write_bytes(b"before-front")
        (journal / "1.after").write_bytes(b"after-front")
        (journal / "manifest.json").write_text(json.dumps({
            "version": 1, "phase": "PREPARED", "files": [
                {"path": ".devagentstudio/template-state.json", "existed": True, "mode": 0o600},
                {"path": "frontend/src/extensions/providers.ts", "existed": True, "mode": 0o600},
            ],
        }))
        self.second.write_bytes(b"after-state")
        recover_v4_transaction(self.root)
        self.assertEqual(self.second.read_bytes(), b"before-state")
        self.assertEqual(self.first.read_bytes(), b"before-front")
        self.assertFalse(journal.exists())


if __name__ == "__main__":
    unittest.main()
