"""一次性桌面验收任务桥；只有已确认开发流程能创建，AG-UI 只领取和交回事实。"""
from __future__ import annotations

from threading import Condition
from time import monotonic
from secrets import token_urlsafe

_CONDITION = Condition()
_JOBS: dict[str, dict] = {}
_DESKTOP_VERIFIER = ""


def await_desktop_acceptance(job: dict, timeout: float = 125) -> dict:
    """在内存签发不可重放任务；超时删除，不把供应商结果未知变成待重试任务。"""
    token = token_urlsafe(32)
    from app.services.run_workers import current_run_id
    from app.services.workspace_process_registry import workspace_process_registry
    run_id = current_run_id()
    with _CONDITION:
        _JOBS[token] = {"job": {**job, "token": token}, "claimed": False, "result": None}
        _CONDITION.notify_all()
        deadline = monotonic() + timeout
        try:
            while _JOBS[token]["result"] is None:
                if workspace_process_registry.is_run_cancelled(run_id):
                    raise ValueError("本次目标验收已取消，结果可能未知，不重新提交。")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ValueError("桌面验收未在期限内交回终态，结果未知；本次不会重新提交。")
                _CONDITION.wait(min(remaining, 1))
            return _JOBS[token]["result"]
        finally:
            _JOBS.pop(token, None)


def claim_desktop_acceptance(verifier_sha256: str) -> dict:
    """桌面一次性领取一个未执行任务，连接断开也不重新入队。"""
    global _DESKTOP_VERIFIER
    with _CONDITION:
        _DESKTOP_VERIFIER = verifier_sha256
        _CONDITION.notify_all()
        deadline = monotonic() + 20
        while True:
            for item in _JOBS.values():
                if not item["claimed"]:
                    item["claimed"] = True
                    return {"job": item["job"]}
            remaining = deadline - monotonic()
            if remaining <= 0:
                return {"job": None}
            _CONDITION.wait(remaining)


def desktop_verifier_sha256(*, required: bool = True) -> str:
    """返回实际运行桌面进程嵌入源码的摘要，打包后不依赖开发机源码路径。"""
    with _CONDITION:
        # 后端重启会清空内存注册；短暂等待桌面恢复控制连接，不把启动竞态变成开发失败。
        if required and not _CONDITION.wait_for(lambda: bool(_DESKTOP_VERIFIER), timeout=10):
            raise ValueError("Electron 验收桥未连接，请确认桌面主进程已加载最新代码；仅刷新页面不会重启验收桥。连接恢复后可重试，本次尚未发送验收请求。")
        return _DESKTOP_VERIFIER


def complete_desktop_acceptance(token: str, evidence: dict) -> dict:
    """仅接受已领取且仍有效的一次性签发身份，不保存对话文本或凭据。"""
    with _CONDITION:
        item = _JOBS.get(token)
        if item is None or not item["claimed"] or item["result"] is not None:
            raise ValueError("桌面验收身份不存在、已消费或已过期。")
        item["result"] = dict(evidence)
        _CONDITION.notify_all()
        return {"accepted": True}
