"""内置后台的客户端租约、空闲退场及升级退出栅栏，不改变任何工作流确认或恢复事实。"""

import asyncio
import hmac
import os
import time
from collections.abc import Callable
from uuid import uuid4


class BackendRuntime:
    """只允许持有本次启动凭据的 Electron 管理后台进程。"""

    def __init__(self, token: str = "", *, lease_seconds: float = 45, idle_seconds: float = 5,
                 startup_seconds: float = 60, clock: Callable[[], float] = time.monotonic):
        """保存短生命周期控制状态，时间使用单调时钟，凭据不公开到健康接口。"""
        self.token, self.clock = token, clock
        self.lease_seconds, self.idle_seconds = lease_seconds, idle_seconds
        self.clients: dict[str, float] = {}
        self.phase = "open"
        self.ticket = ""
        self.exit_owner = ""
        self.requests = 0
        self.control_requests = 0
        self.idle_since = clock() + startup_seconds - idle_seconds
        self.busy: Callable[[], int] = lambda: 0
        self.shutdown: Callable[[], None] | None = None
        self.watcher: asyncio.Task | None = None

    @property
    def managed(self) -> bool:
        """只有由桌面启动器明确注入凭据的实例允许自行退出。"""
        return len(self.token) >= 32 and self.shutdown is not None

    def metadata(self) -> dict:
        """发布可核验的运行身份，不泄漏控制凭据和客户端身份。"""
        return {"enabled": self.managed, "phase": self.phase}

    def busy_count(self) -> int:
        """同时观察 HTTP、后台生成和收口任务；待确认的静止 checkpoint 不算执行。"""
        return self.requests + self.busy()

    def enter_request(self, *, preview_cleanup: bool = False) -> bool:
        """进入请求与设置退出栅栏均在事件循环中同步完成，消除空闲检查后的接收竞态。"""
        if self.phase != "open" and not (preview_cleanup and self.phase == "prepared"):
            return False
        self.requests += 1
        return True

    def leave_request(self) -> None:
        """响应或断连真正收口后释放请求占用，不代替后台任务自己的占用。"""
        self.requests -= 1

    def authorize(self, token: str) -> None:
        """不允许源码共享后台退场，也不允许其它本地调用者猜测实例控制权限。"""
        if not self.managed or not hmac.compare_digest(token, self.token):
            raise ValueError("无法管理当前后台服务，请重新打开客户端。")

    def act(self, action: str, client_id: str, ticket: str = "") -> dict:
        """两阶段退出：先关接收入口，清理完成再提交；忙碌时完全保留原执行。"""
        self.prune_clients()
        if action == "attach":
            if self.phase != "open" and self.exit_owner != client_id:
                return {"state": "exiting"}
            self.clients[client_id] = self.clock() + self.lease_seconds
            return {"state": self.phase}
        if action == "detach":
            self.clients.pop(client_id, None)
            if not self.clients:
                self.idle_since = self.clock()
            return {"state": self.phase}
        if action == "prepare_exit":
            if self.phase == "exiting":
                return {"state": "exiting"}
            if self.phase == "prepared":
                return {"state": "prepared", "ticket": self.ticket} if self.exit_owner == client_id else {"state": "busy"}
            if self.busy_count() or any(owner != client_id for owner in self.clients):
                return {"state": "busy"}
            self.phase, self.exit_owner, self.ticket = "prepared", client_id, uuid4().hex
            return {"state": "prepared", "ticket": self.ticket}
        if client_id != self.exit_owner or not hmac.compare_digest(ticket, self.ticket) or not ticket:
            raise ValueError("退出操作已失效，请重新同步后台状态。")
        if action == "cancel_exit" and self.phase == "prepared":
            self.phase, self.ticket, self.exit_owner = "open", "", ""
            return {"state": "open"}
        if action == "commit_exit" and self.phase == "prepared":
            if self.busy_count():
                return {"state": "busy"}
            self.phase = "exiting"
        return {"state": self.phase}

    def prune_clients(self) -> None:
        """崩溃客户端不永久占有后台；租约过期只释放连接，绝不停止业务任务。"""
        expired = [owner for owner, expiry in self.clients.items() if expiry <= self.clock()]
        for owner in expired:
            self.clients.pop(owner, None)
        if expired and not self.clients:
            self.idle_since = self.clock()

    def tick(self) -> None:
        """没有客户端且真实任务全部退出后自行退场；升级提交则等待流回执后有序停服。"""
        if not self.managed:
            return
        self.prune_clients()
        if not self.clients and not self.busy_count() and self.clock() - self.idle_since >= self.idle_seconds:
            self.phase = "exiting"
        if self.phase == "exiting" and not self.busy_count() and not self.control_requests:
            self.shutdown()

    async def start(self, busy: Callable[[], int]) -> None:
        """仅为内置实例启动空闲观察，源码 Uvicorn 始终由开发者控制。"""
        self.busy = busy
        if self.managed:
            self.watcher = asyncio.create_task(self.watch(), name="backend-runtime-idle")

    async def watch(self) -> None:
        """每秒检查真实活动和客户端租约，不读取或修改业务生命周期文件。"""
        while True:
            await asyncio.sleep(1)
            self.tick()

    async def close(self) -> None:
        """后台自身退出时回收观察器，不从这里取消任何业务任务。"""
        if self.watcher:
            self.watcher.cancel()
            await asyncio.gather(self.watcher, return_exceptions=True)
            self.watcher = None


runtime = BackendRuntime(os.environ.get("DEVAGENTSTUDIO_BACKEND_CONTROL_TOKEN", ""))


def runtime_busy_count(bootstrap) -> int:
    """汇总实际 owner，包括未写首个 checkpoint 的接受任务和断连后仍运行的生成池。"""
    from app.services.detached_execution import active_detached_count
    from app.services.ui_design_generation_pool import active_ui_design_count
    from app.services.builtin_authorization_orchestrator import active_builtin_authorization_count
    from app.protocols.preview_runtime import active_preview_runtime_count
    from app.protocols.workflow.run_control import workflow_run_registry
    from app.services.workspace_process_registry import workspace_process_registry
    return (active_detached_count() + active_ui_design_count() + active_builtin_authorization_count()
            + active_preview_runtime_count() + bootstrap.active_count()
            + len(workflow_run_registry.active_run_ids()) + workspace_process_registry.active_count())
