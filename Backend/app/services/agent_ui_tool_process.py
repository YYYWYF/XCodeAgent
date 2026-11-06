"""管理本机 Node 控制管道；模型参数仅进入子进程环境，SDK 凭据不对外发布。"""

from __future__ import annotations

import json
import queue
import subprocess
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Event, RLock, Thread
from time import monotonic
from typing import TypedDict

from app.services.agent_ui_tool_host import AgentUiToolHostError, AgentUiToolHostInspection
from app.config import Settings
from app.services.agent_ui_creator_environment import creator_process_environment
from app.services.workspace_process_registry import workspace_process_registry
from app.utils.process_liveness import pid_is_running


class _Frame(TypedDict, total=False):
    """仅允许本机包装定义的固定状态字段。"""

    type: str
    id: int
    busy: bool
    code: str
    pid: int


class AgentUiToolProcess:
    """持有一个包装进程，只有 SDK 清理回执和真实进程退出才算停机确认。"""

    def __init__(self, inspection: AgentUiToolHostInspection, run_id: str, startup_timeout: float) -> None:
        """启动固定脚本并登记工作区，在收到 ready 前不公开可用状态。"""
        self.inspection = inspection
        self.run_id = run_id
        self._guard = RLock()
        self._stopping = Event()
        self._reader_failed = Event()
        self._frames: queue.Queue[_Frame | None] = queue.Queue(maxsize=16)
        self._sequence = 0
        self._cleanup_confirmed = False
        self._context_closed = False
        self.creator_process_id: int | None = None
        script = Path(__file__).with_name('agent_ui_tool_bridge.mjs').read_text(encoding='utf-8')
        environment = creator_process_environment(Settings.from_env())
        self._context: AbstractContextManager[subprocess.Popen[bytes]] = workspace_process_registry.managed_process(
            [str(inspection.node_executable), '--input-type=module', '--eval', script],
            workspace=inspection.workspace, run_id=run_id, cwd=inspection.installation_root,
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.process = self._context.__enter__()
        self._reader = Thread(target=self._read_frames, daemon=True, name='agent-ui-tool-control')
        self._reader.start()
        try:
            self._send({'workspace': str(inspection.workspace),
                        'nodeVersion': inspection.node_version, 'packageJsonSha256': inspection.package_json_sha256,
                        'startupTimeoutMs': max(1, int(startup_timeout * 1000))})
        except Exception:
            self.close()
            raise

    def _read_frames(self) -> None:
        """有界读取固定控制回执，SDK 原始输出和未知字段不进入错误信息。"""
        try:
            assert self.process.stdout is not None
            while True:
                line = self.process.stdout.readline(8193)
                if not line:
                    self._frames.put_nowait(None)
                    return
                value = json.loads(line) if len(line) <= 8192 and line.endswith(b'\n') else None
                if (not isinstance(value, dict) or set(value) - {'type', 'id', 'busy', 'code', 'pid'}
                        or value.get('type') not in {'ready', 'status', 'failed', 'stopped'}
                        or type(value.get('id')) is not int):
                    raise ValueError('invalid frame')
                self._frames.put_nowait(value)
        except (ValueError, OSError, queue.Full):
            self._reader_failed.set()

    def _send(self, value: dict[str, object]) -> None:
        """参数只经 stdin JSON 传递，不拼接 shell，也不写凭据。"""
        try:
            assert self.process.stdin is not None
            self.process.stdin.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise AgentUiToolHostError('bridge_failed', 'Agent UI 工具控制管道已断开。') from None

    def _receive(self, identity: int, timeout: float, *, closing: bool = False) -> _Frame:
        """等待当前回执，启动/查询可取消，清理必须继续等待真实退出。"""
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            if not closing and (self._stopping.is_set() or workspace_process_registry.is_run_cancelled(self.run_id)):
                raise AgentUiToolHostError('probe_cancelled', 'Agent UI 工具启动或查询已取消。')
            if self._reader_failed.is_set():
                raise AgentUiToolHostError('bridge_failed', 'Agent UI 工具控制回执无效。')
            try:
                frame = self._frames.get(timeout=min(0.05, max(0, deadline - monotonic())))
            except queue.Empty:
                continue
            if frame is None:
                raise AgentUiToolHostError('bridge_failed', 'Agent UI 工具意外退出。')
            if closing:
                if frame['type'] == 'stopped':
                    return frame
                continue
            if frame['type'] == 'failed':
                code = 'start_failed' if identity == 0 else 'bridge_failed'
                raise AgentUiToolHostError(code, 'Agent UI 公开 SDK 操作失败，原始输出未回显。')
            if frame['id'] != identity:
                raise AgentUiToolHostError('bridge_failed', 'Agent UI 工具回执与当前操作不一致。')
            return frame
        code = 'start_timeout' if identity == 0 else 'bridge_timeout'
        raise AgentUiToolHostError(code, 'Agent UI 工具等待回执超时。')

    def wait_ready(self, timeout: float) -> None:
        """只在公开 SDK 完成健康启动后接受 ready。"""
        with self._guard:
            frame = self._receive(0, timeout)
            if frame['type'] != 'ready' or type(frame.get('pid')) is not int or frame['pid'] <= 0:
                raise AgentUiToolHostError('start_failed', 'Agent UI 工具尚未就绪。')
            self.creator_process_id = frame['pid']

    def busy(self) -> bool:
        """查询公开 SDK 的真实占用，不因包装进程尚在就虚构健康。"""
        with self._guard:
            if self.process.poll() is not None:
                raise AgentUiToolHostError('bridge_failed', 'Agent UI 工具已退出，未自动重启。')
            self._sequence += 1
            self._send({'action': 'status', 'id': self._sequence})
            frame = self._receive(self._sequence, 6)
            if frame['type'] != 'status' or type(frame.get('busy')) is not bool:
                raise AgentUiToolHostError('bridge_failed', 'Agent UI 工具健康状态无效。')
            return frame['busy']

    def close(self) -> bool:
        """先请求 SDK dispose，再回收包装；缺回执时保留未确认清理事实。"""
        self._stopping.set()
        with self._guard:
            if self._context_closed:
                return self._cleanup_confirmed
            try:
                if self.process.poll() is None:
                    try:
                        self._sequence += 1
                        self._send({'action': 'stop', 'id': self._sequence})
                    except AgentUiToolHostError:
                        pass
                self._receive(self._sequence, 8, closing=True)
                self.process.wait(timeout=2)
                # SDK 的回执之外，再只读确认其公开 PID 已退出；不按日志 PID 杀其他进程。
                self._cleanup_confirmed = not pid_is_running(self.creator_process_id or 0)
            except (AgentUiToolHostError, OSError, subprocess.TimeoutExpired):
                self._cleanup_confirmed = False
            finally:
                try:
                    self._context.__exit__(None, None, None)
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    self._cleanup_confirmed = False
                finally:
                    self._context_closed = True
                    self._reader.join(timeout=1)
            return self._cleanup_confirmed
