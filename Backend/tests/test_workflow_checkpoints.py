from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.persistence.checkpoints import (
    close_workflow_checkpointer,
    workflow_checkpoint_db_path,
    workflow_checkpointer,
)


class WorkflowCheckpointerTests(unittest.IsolatedAsyncioTestCase):
    """验证工作区重建后不会继续复用已被移动的 SQLite 连接。"""

    async def asyncTearDown(self) -> None:
        """关闭测试创建的全局 saver，避免临时目录清理后残留连接。"""

        await close_workflow_checkpointer()

    async def test_reopens_database_when_same_path_points_to_new_inode(self) -> None:
        """同路径 checkpoint 被移走后应创建新 saver，并让新数据库留在当前工作区。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            first = await workflow_checkpointer(workspace=str(workspace))
            db_path = workflow_checkpoint_db_path(workspace=str(workspace))
            moved_path = Path(directory) / "moved-checkpoints.sqlite"
            db_path.replace(moved_path)

            second = await workflow_checkpointer(workspace=str(workspace))

            self.assertIsNot(first, second)
            self.assertTrue(db_path.is_file())
            self.assertTrue(moved_path.is_file())


if __name__ == "__main__":
    unittest.main()
