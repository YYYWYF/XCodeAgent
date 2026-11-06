"""独立 Creator 工具会话 owner，产品调用仍由既有 AG-UI 流程承载。"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from app.services.agent_ui_tool_host import AgentUiToolHostError, AgentUiToolHostInspection, inspect_agent_ui_tool_host
from app.services.agent_ui_tool_process import AgentUiToolProcess
from app.services.run_workers import current_run_id


@dataclass(frozen=True)
class AgentUiToolHostOptions:
    """绑定内部工具安装和版本；Creator 模型参数由平台当前 Settings 提供。"""

    workspace: Path
    installation_root: Path
    node_executable: Path
    creator_version: str
    run_id: str = ''
    startup_timeout_seconds: float = 15.0


@dataclass(frozen=True)
class AgentUiToolHostStatus:
    """不含 SDK endpoint/token 的内部工具状态。"""

    workspace: Path
    process_id: int
    creator_version: str
    busy: bool
    creator_process_id: int


class AgentUiToolHosts:
    """每个工具安装只绑定一个工程，不锁定无关普通会话。"""

    def __init__(self) -> None:
        """建立仅限本进程的 owner 表和关闭栅栏，不恢复历史 PID。"""
        self._guard = RLock()
        self._hosts: dict[Path, AgentUiToolProcess] = {}
        self._closed = False

    def _inspect(self, options: AgentUiToolHostOptions) -> AgentUiToolHostInspection:
        """每次启动重新核对 Node 和包，不使用过期预检签发永久启动权限。"""
        if (isinstance(options.startup_timeout_seconds, bool) or not math.isfinite(options.startup_timeout_seconds)
                or options.startup_timeout_seconds <= 0):
            raise AgentUiToolHostError('start_failed', '工具启动必须设置有限的正超时预算。')
        with self._guard:
            if self._closed:
                raise AgentUiToolHostError('host_closed', '后端工具宿主已关闭，拒绝迟到启动。')
        return inspect_agent_ui_tool_host(workspace=options.workspace, installation_root=options.installation_root,
                                          node_executable=options.node_executable,
                                          creator_version=options.creator_version, run_id=options.run_id)

    def _start(self, options: AgentUiToolHostOptions, inspection: AgentUiToolHostInspection) -> AgentUiToolHostStatus:
        """先登记包装以便并发关闭能发现它，再在锁外等待 SDK 启动。"""
        with self._guard:
            if self._closed:
                raise AgentUiToolHostError('host_closed', '工具宿主已关闭。')
            if inspection.installation_root in self._hosts:
                raise AgentUiToolHostError('tool_busy', '该工具安装已有工程绑定，请显式停止或切换。')
            try:
                host = AgentUiToolProcess(inspection, options.run_id or current_run_id(), options.startup_timeout_seconds)
            except (OSError, RuntimeError):
                raise AgentUiToolHostError('start_failed', '无法启动 Agent UI 工具包装进程。') from None
            self._hosts[inspection.installation_root] = host
        try:
            host.wait_ready(options.startup_timeout_seconds)
            with self._guard:
                if self._closed or self._hosts.get(inspection.installation_root) is not host:
                    raise AgentUiToolHostError('host_closed', '当前工具启动已退出或被关闭。')
            status = AgentUiToolHostStatus(inspection.workspace, host.process.pid, inspection.creator_version,
                                           host.busy(), host.creator_process_id or 0)
            logging.getLogger('uvicorn.error').info('agent_ui.tool.ready wrapperPid=%d creatorPid=%d',
                                                    status.process_id, status.creator_process_id)
            return status
        except Exception:
            self._close(host)
            raise

    def start(self, options: AgentUiToolHostOptions) -> AgentUiToolHostStatus:
        """启动已选择的工具，不安装依赖、不创建 UI 修改任务。"""
        return self._start(options, self._inspect(options))

    def _find(self, workspace: Path) -> AgentUiToolProcess:
        """只取得当前工程的持有句柄，不使用端口或磁盘历史猜身份。"""
        root = workspace.resolve()
        with self._guard:
            for host in self._hosts.values():
                if host.inspection.workspace == root:
                    return host
        raise AgentUiToolHostError('host_closed', '当前工程没有已登记的 Agent UI 工具。')

    def status(self, workspace: Path) -> AgentUiToolHostStatus:
        """从当前工具 owner 查询公开 SDK 健康及占用。"""
        host = self._find(workspace)
        return AgentUiToolHostStatus(host.inspection.workspace, host.process.pid, host.inspection.creator_version,
                                    host.busy(), host.creator_process_id or 0)

    def _close(self, host: AgentUiToolProcess) -> bool:
        """只有确认 SDK 清理后才移除 owner，否则留待明确处置。"""
        confirmed = host.close()
        logging.getLogger('uvicorn.error').log(logging.INFO if confirmed else logging.WARNING,
                                               'agent_ui.tool.cleanup confirmed=%s wrapperPid=%d', confirmed, host.process.pid)
        if confirmed:
            with self._guard:
                if self._hosts.get(host.inspection.installation_root) is host:
                    self._hosts.pop(host.inspection.installation_root)
        return confirmed

    def stop(self, workspace: Path) -> None:
        """停止指定绑定；不把失去管道或强制退出当作 SDK 清理成功。"""
        if not self._close(self._find(workspace)):
            raise AgentUiToolHostError('stop_unconfirmed', '工具包装已回收，但 SDK 子进程清理尚未确认。')

    def switch(self, workspace: Path, options: AgentUiToolHostOptions) -> AgentUiToolHostStatus:
        """空闲工具先清理旧绑定，再以相同安装和平台配置建立新工程绑定。"""
        inspection = self._inspect(options)
        old = self._find(workspace)
        if (inspection.installation_root != old.inspection.installation_root
                or inspection.creator_version != old.inspection.creator_version
                or inspection.node_executable != old.inspection.node_executable):
            raise AgentUiToolHostError('binding_changed', '切换工程不能隐式改变工具安装、Node 或版本。')
        if old.busy():
            raise AgentUiToolHostError('tool_busy', 'Creator 当前仍有任务或待办，不能切换工程。')
        self.stop(workspace)
        return self._start(options, inspection)

    def close_all(self) -> tuple[Path, ...]:
        """封锁迟到启动并逐一回收，某个失败不跳过其余工具。"""
        with self._guard:
            self._closed = True
            hosts = list(self._hosts.values())
        failures: list[Path] = []
        for host in hosts:
            try:
                if not self._close(host):
                    failures.append(host.inspection.workspace)
            except Exception:
                failures.append(host.inspection.workspace)
        return tuple(failures)


agent_ui_tool_hosts = AgentUiToolHosts()


async def shutdown_agent_ui_tool_hosts() -> None:
    """后端退出时等待清理，失败只输出安全计数，不回显 SDK 日志。"""
    failures = await asyncio.to_thread(agent_ui_tool_hosts.close_all)
    if failures:
        logging.getLogger('uvicorn.error').error('Agent UI tool cleanup unconfirmed: count=%d', len(failures))
