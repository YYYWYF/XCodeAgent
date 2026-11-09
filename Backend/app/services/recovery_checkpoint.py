"""只读判断当前线程是否有执行现场，缺失的 checkpoint 不属于应用资产错误。"""

import aiosqlite

from app.persistence.checkpoints import workflow_checkpoint_db_path


async def checkpoint_thread_exists(workspace: str, thread_id: str) -> bool:
    """仅读当前格式的 root checkpoint；没有文件、表或线程时返回空，不创建数据库。"""
    path = workflow_checkpoint_db_path(workspace=workspace)
    if not thread_id or not path.is_file():
        return False
    async with aiosqlite.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        async with db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'") as cursor:
            if await cursor.fetchone() is None:
                return False
        async with db.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' LIMIT 1", (thread_id,)
        ) as cursor:
            return await cursor.fetchone() is not None
