"""以后台真实任务和当前清单指纹控制单页停止，不依赖前端磁盘镜像。"""

import hashlib
import json
from typing import Any

from app.services.ui_design_generation_pool import UiDesignGenerationPool
from app.workspace.spec_documents import write_ui_designs_json

ACTIVE_STATUSES = {"queued", "generating", "stopping"}


def _token(pool: UiDesignGenerationPool, workspace: str, page_id: str, identity: Any) -> str:
    """签发绑定本次后台实例、工作区及精确观察身份的停止凭据。"""
    return hashlib.sha256(json.dumps([pool.control_identity, workspace, page_id, identity],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _snapshot(pool: UiDesignGenerationPool, workspace: str, page_id: str) -> dict[str, Any]:
    """在池锁内读取真实 owner；活任务绑定代次，残留页绑定内容及后台实例。"""
    from app.protocols.workflow.run_control import workflow_run_registry
    from app.services.run_workers import active_worker_runs

    job = pool._jobs.get((workspace, page_id))
    manifest = pool._read_manifest(workspace, "")
    page = next((p for p in manifest.get("pages", []) if p.get("pageId") == page_id), {})
    # 有 Graph/同步 writer 时不能把尚未登记的页面判断为已结束。
    pool_runs = {entry.id for (root, _), entry in pool._jobs.items() if root == workspace}
    preparing = not job and bool(workflow_run_registry.active_run_ids(workspace) or
        (active_worker_runs(workspace) - pool_runs))
    if not job and not page and not preparing:
        raise ValueError("当前页面生成记录不存在或无法读取，请重新读取设计稿。")
    identity = job.id if job else {k: v for k, v in page.items() if k != "code"}
    token = _token(pool, workspace, page_id, identity)
    return {"pageId": page_id, "generationId": job.id if job else page.get("generation_id"),
        "pageStatus": ("stopping" if job.cancelled.is_set() else "generating" if job.active else "queued")
        if job else page.get("status", "pending"),
        "taskState": "active" if job else "preparing" if preparing else "orphaned"
        if page.get("status") in ACTIVE_STATUSES else "settled", "stopToken": token}


async def inspect_page(pool: UiDesignGenerationPool, workspace: str, page_id: str) -> dict[str, Any]:
    """只读返回可取消身份，不入队、不改清单、不恢复模型。"""
    async with pool._lock_for(workspace):
        return _snapshot(pool, workspace, page_id)


async def stop_page(pool: UiDesignGenerationPool, workspace: str, page_id: str, token: str) -> dict[str, Any]:
    """复核用户观察到的任务，安全收口孤立状态并对同一次停止幂等回执。"""
    key = (workspace, page_id)
    async with pool._lock_for(workspace):
        current = _snapshot(pool, workspace, page_id)
        if current["taskState"] == "preparing":
            raise ValueError("生成动作正在提交，请等待任务登记后重试停止。")
        receipt = pool.stop_receipts.get(key)
        if receipt == (token, current["stopToken"]):
            return {**current, "stopToken": token, "settled": True}
        # 查询后任务恰好自然完成也算已收口；只接受同一代终态，不能触及下一代。
        if (current["taskState"] == "settled" and current["generationId"] and
                current["pageStatus"] in {"confirmed", "generation_failed", "cancelled"} and
                token == _token(pool, workspace, page_id, current["generationId"])):
            pool.stop_receipts[key] = (token, current["stopToken"])
            return {**current, "stopToken": token, "settled": True}
        if current["stopToken"] != token:
            raise ValueError("页面生成任务已变化，请同步当前任务后重试停止。")
        if current["taskState"] != "active":
            if current["taskState"] == "orphaned":
                manifest = pool._read_manifest(workspace, "")
                page = next(p for p in manifest["pages"] if p.get("pageId") == page_id)
                pool._replace_page(manifest, page_id, {**page, "status": "cancelled", "error": "设计稿生成已停止。"})
                write_ui_designs_json({"workspace": workspace}, manifest)
            final = _snapshot(pool, workspace, page_id)
            pool.stop_receipts[key] = (token, final["stopToken"])
            return {**final, "stopToken": token, "settled": True}
        generation_id = current["generationId"]
    # 等待线程退出时释放锁，让 worker 能提交最终取消状态；仍只取消已核验的代次。
    await pool.cancel_page(workspace, page_id, generation_id=generation_id)
    async with pool._lock_for(workspace):
        final = _snapshot(pool, workspace, page_id)
        if final.get("generationId") != generation_id:
            raise ValueError("页面生成任务已变化，请同步当前任务。")
        pool.stop_receipts[key] = (token, final["stopToken"])
        return {**final, "stopToken": token, "settled": True}
