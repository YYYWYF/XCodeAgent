"""技术规划变更快照与已重做证据的作用域回归。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.development_change_snapshot import write_change_snapshot


class DevelopmentChangeSnapshotTests(unittest.TestCase):
    """新快照作废旧证据，相同快照重复确认保持幂等。"""

    def setUp(self) -> None:
        """创建带真实 lifecycle 的隔离工作区。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        write_application_lifecycle(
            self.workspace,
            create_application_lifecycle(application_id="test", application_name="测试"),
        )

    def _plan(self) -> dict:
        """构造包含页面、实体和接口变更标记的最小展示计划。"""

        return {
            "pages": [{"pageId": "home", "_change_status": "modified"}],
            "entities": [{"id": "greeting", "_change_status": "unchanged"}],
            "api_contracts": [{
                "id": "greeting_api",
                "_change_status": "modified",
                "endpoints": [{"id": "greeting_api.get", "_change_status": "modified"}],
            }],
        }

    def _seed_redeveloped(self) -> None:
        """写入上一份快照留下的已重做证据。"""

        lifecycle = load_application_lifecycle(self.workspace)
        lifecycle.extensions = {"redevelopedChangedTargets": ["page:home"]}
        write_application_lifecycle(self.workspace, lifecycle)

    def test_new_facts_reset_but_same_facts_preserve_redeveloped_targets(self) -> None:
        """变化快照清空旧证据；相同快照再次写入不重复清空。"""

        self._seed_redeveloped()
        write_change_snapshot(self.workspace, technical_plan=self._plan())
        self.assertNotIn(
            "redevelopedChangedTargets",
            load_application_lifecycle(self.workspace).extensions,
        )

        self._seed_redeveloped()
        write_change_snapshot(self.workspace, technical_plan=self._plan())
        self.assertEqual(
            load_application_lifecycle(self.workspace).extensions["redevelopedChangedTargets"],
            ["page:home"],
        )

        changed = self._plan()
        changed["pages"][0]["_change_status"] = "deleted"
        write_change_snapshot(self.workspace, technical_plan=changed)
        self.assertNotIn(
            "redevelopedChangedTargets",
            load_application_lifecycle(self.workspace).extensions,
        )


if __name__ == "__main__":
    unittest.main()
