"""验证 V2 可恢复执行内核的最小不变量。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.template_reconcile.executor_v2 import (
    ModificationStrategyExecutorV2,
    StrategyExecutionV2Error,
    WorkingCopyStoreV2,
    apply_working_copy_v2,
    restore_working_copy_v2,
)
from app.services.template_reconcile.protocol_v2 import StrategyDescriptorV2
from app.services.template_reconcile.runtime_v2 import recovery_action


def _add_strategy() -> StrategyDescriptorV2:
    """构造最小 ADD_FILE Strategy。"""

    return StrategyDescriptorV2.model_validate({"strategyId": "add", "index": 0, "schemaVersion": 1, "type": "ADD_FILE", "target": "frontend/Login.tsx", "precondition": {}, "payloadRef": "payload/login"})


class TemplateReconcileV2RuntimeTests(unittest.TestCase):
    """覆盖 ADD 重放、冲突、恢复与 digest 恢复分支。"""

    def test_add_file_is_retry_safe_and_restorable(self) -> None:
        """确认 ADD_FILE 创建后可重放，并能在正常失败路径删除新增文件。"""

        with tempfile.TemporaryDirectory() as directory:
            store = WorkingCopyStoreV2(directory)
            executor = ModificationStrategyExecutorV2()
            executor.execute([_add_strategy()], store, {"payload/login": "login"})
            apply_working_copy_v2(directory, store)
            self.assertEqual((Path(directory) / "frontend/Login.tsx").read_text(), "login")
            retry = WorkingCopyStoreV2(directory)
            executor.execute([_add_strategy()], retry, {"payload/login": "login"})
            self.assertEqual(retry.changed_entries(), [])
            restore_working_copy_v2(directory, store)
            self.assertFalse((Path(directory) / "frontend/Login.tsx").exists())

    def test_add_file_rejects_different_existing_content(self) -> None:
        """确认 ADD_FILE 不覆盖当前 Workspace 的不同业务内容。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "frontend/Login.tsx"
            target.parent.mkdir()
            target.write_text("business", encoding="utf-8")
            with self.assertRaises(StrategyExecutionV2Error):
                ModificationStrategyExecutorV2().execute([_add_strategy()], WorkingCopyStoreV2(directory), {"payload/login": "login"})

    def test_text_anchor_insert_and_marked_structure_are_idempotent(self) -> None:
        """验证文本与前端结构化 Handler 都在唯一锚点下避免重复插入。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            target = root / "src/routes.tsx"
            target.write_text("const routes = [\n  // routes\n];\n", encoding="utf-8")
            strategies = [
                StrategyDescriptorV2.model_validate({"strategyId": "anchor", "index": 0, "schemaVersion": 1, "type": "TEXT_ANCHOR_INSERT", "target": "src/routes.tsx", "parameters": {"anchor": "  // routes", "content": "  { path: '/a' },\n"}}),
                StrategyDescriptorV2.model_validate({"strategyId": "route", "index": 1, "schemaVersion": 1, "type": "ENSURE_ROUTE", "target": "src/routes.tsx", "parameters": {"anchor": "  // routes", "managedMarker": "xcodeagent:route:b", "content": "  // xcodeagent:route:b\n  { path: '/b' },\n"}}),
            ]
            executor = ModificationStrategyExecutorV2()
            store = WorkingCopyStoreV2(root)
            executor.execute(strategies, store, {})
            apply_working_copy_v2(root, store)
            first = target.read_text(encoding="utf-8")
            retry = WorkingCopyStoreV2(root)
            executor.execute(strategies, retry, {})
            self.assertEqual([], retry.changed_entries())
            self.assertIn("xcodeagent:route:b", first)

    def test_npm_dependency_conflict_is_rejected(self) -> None:
        """验证 JSON 依赖 Handler 不会静默覆盖业务锁定的依赖版本。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text('{"dependencies":{"react":"18.0.0"}}', encoding="utf-8")
            strategy = StrategyDescriptorV2.model_validate({"strategyId": "dep", "index": 0, "schemaVersion": 1, "type": "ENSURE_NPM_DEPENDENCY", "target": "package.json", "parameters": {"name": "react", "version": "19.0.0"}})
            with self.assertRaisesRegex(StrategyExecutionV2Error, "NPM_DEPENDENCY_CONFLICT"):
                ModificationStrategyExecutorV2().execute([strategy], WorkingCopyStoreV2(root), {})

    def test_recovery_action_has_unique_digest_branches(self) -> None:
        """确认当前、目标和未知 State digest 分别进入唯一恢复分支。"""

        class Attempt:
            """为 recovery_action 提供只读 digest fixture。"""

            current_state_digest = "current"
            next_state_digest = "next"

        self.assertEqual(recovery_action(Attempt(), "current"), "REPLAY")
        self.assertEqual(recovery_action(Attempt(), "next"), "FINALIZE")
        self.assertEqual(recovery_action(Attempt(), "other"), "CONFLICT")
