from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.persistence.checkpoints import (
    close_workflow_checkpointer,
    close_workflow_checkpointer_for_workspace,
    delete_workflow_checkpoints_for_workspace,
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

    async def test_deletes_only_checkpoint_threads_for_target_workspace(self) -> None:
        """共享库按 checkpoint 正文和已知线程清理，不影响其他工作区。"""

        with TemporaryDirectory() as directory:
            first_workspace = str(Path(directory) / "first")
            second_workspace = str(Path(directory) / "second")
            saver = await workflow_checkpointer(workspace=first_workspace)
            for thread_id, workspace in (
                ("thread-first", first_workspace),
                ("thread-second", second_workspace),
            ):
                type_tag, checkpoint_blob = saver.serde.dumps_typed(
                    {"channel_values": {"workspace": workspace}}
                )
                await saver.conn.execute(
                    """
                    INSERT INTO checkpoints(
                        thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata
                    ) VALUES (?, '', 'checkpoint-1', ?, ?, ?)
                    """,
                    (thread_id, type_tag, checkpoint_blob, "{}"),
                )
            type_tag, checkpoint_blob = saver.serde.dumps_typed({"channel_values": {}})
            await saver.conn.execute(
                """
                INSERT INTO checkpoints(
                    thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata
                ) VALUES ('thread-known', '', 'checkpoint-1', ?, ?, '{}')
                """,
                (type_tag, checkpoint_blob),
            )
            await saver.conn.commit()

            result = await delete_workflow_checkpoints_for_workspace(
                workspace=first_workspace,
                thread_ids={"thread-known"},
            )
            cursor = await saver.conn.execute(
                "SELECT thread_id FROM checkpoints ORDER BY thread_id"
            )
            rows = await cursor.fetchall()
            await cursor.close()

            self.assertEqual(result["deletedThreadCount"], 2)
            self.assertEqual(rows, [("thread-second",)])

    async def test_closes_and_reopens_local_workspace_connection(self) -> None:
        """删除准备关闭本地 SQLite 后，同路径仅能创建全新的 saver。"""

        with TemporaryDirectory() as directory:
            workspace = str(Path(directory) / "workspace")
            first = await workflow_checkpointer(workspace=workspace)

            closed = await close_workflow_checkpointer_for_workspace(
                workspace=workspace
            )
            second = await workflow_checkpointer(workspace=workspace)

            self.assertTrue(closed)
            self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
