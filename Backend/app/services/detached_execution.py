"""后台拥有工作流，HTTP 连接只订阅输出；显式停止仍沿原 Run Registry。"""

import asyncio
import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator, Callable
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from pydantic import BaseModel, Field

from ag_ui.core import RunStartedEvent, RunErrorEvent, TextMessageStartEvent, TextMessageEndEvent, TextMessageContentEvent
from app.protocols.execution_event_encoder import EventEncoder, ExecutionFrame
from app.persistence.execution_streams import accept_execution, save_execution_output
from app.services.backend_instance import current_backend_instance
from app.services.execution_output import ExecutionOutput
from app.domain.execution_recovery import RecoveryExecutionError


class ExecutionSubmissionIdentity(BaseModel):
    """在后台接受边界严格校验请求和线程身份，禁止非法输入写入接受表。"""
    runId: str = Field(min_length=1, max_length=512)
    threadId: str = Field(min_length=1, max_length=512)


class BackgroundExecution:
    """持有真实业务生成器及有界订阅缓冲，慢连接不阻塞 Graph 或累积无限队列。"""

    def __init__(self, workspace: str, record: dict, source: AsyncIterator[str]):
        """绑定已被持久接受的请求，不继承调用连接的取消责任。"""
        self.workspace, self.record, self.source = workspace, record, source
        self.output = ExecutionOutput()
        self.condition = asyncio.Condition()
        self.frames: deque[tuple[int, str]] = deque()
        self.buffer_bytes = 0
        self.sequence = 0
        self.finished = False
        self.task: asyncio.Task | None = None

    async def publish(self, frame: str, *, finished: bool = False) -> None:
        """先保存官方类型事件的紧凑结果，再唤醒订阅者，网络速度不影响执行。"""
        if not isinstance(frame, ExecutionFrame):
            raise RuntimeError("后台执行必须输出官方类型化 AG-UI 事件。")
        output = ExecutionOutput(deepcopy(self.output.value))
        output.apply(frame.event)
        sequence = self.sequence + 1
        await save_execution_output(self.workspace, self.record["request_id"], output.value, sequence, finished)
        async with self.condition:
            # 可观察的序号、快照和新帧必须一起发布；先推进序号会让订阅者在无帧时空转。
            self.output = output
            self.sequence = sequence
            self.frames.append((self.sequence, frame))
            self.buffer_bytes += len(frame.encode("utf-8"))
            while len(self.frames) > 1 and self.buffer_bytes > 2 * 1024 * 1024:
                self.buffer_bytes -= len(self.frames.popleft()[1].encode("utf-8"))
            self.finished = finished
            self.condition.notify_all()

    async def run(self) -> None:
        """独立消费到原业务确认门或终态；连接消失不取消这里，停止仍可取消真实任务。"""
        encoder = EventEncoder()
        try:
            async for frame in self.source:
                # 内部 JSON token 从不作为产品回复消费；仅保存标准文本及业务进度，避免逐 token 重写快照。
                if isinstance(frame, ExecutionFrame) and frame.event.type == "CUSTOM" and frame.event.name == "llm.token":
                    continue
                await self.publish(frame)
        except BaseException as exc:
            # 原 Runtime 先执行 finally 收口；仅补齐未产生终帧的取消或基础设施异常。
            if not self.output.value.get("start"):
                await self.publish(encoder.encode(RunStartedEvent(threadId=self.record["thread_id"], runId=self.record["request_id"])))
            await self.publish(encoder.encode(RunErrorEvent(
                message="任务已停止，请同步当前执行状态。" if isinstance(exc, asyncio.CancelledError) else "后台任务未能完成，请同步恢复状态。",
                code="EXECUTION_CANCELLED" if isinstance(exc, asyncio.CancelledError) else "EXECUTION_STREAM_FAILED")))
        finally:
            sequence = self.sequence
            try:
                await self.source.aclose()
                sequence = self.sequence + 1
                await save_execution_output(self.workspace, self.record["request_id"], self.output.value, sequence, True)
            finally:
                async with self.condition:
                    self.sequence = sequence
                    self.finished = True
                    self.condition.notify_all()
                _executions.pop((self.workspace, self.record["request_id"]), None)

    async def subscribe(self) -> AsyncIterator[str]:
        """从已保存快照接入，再追踪有界新帧；关闭订阅只释放本地观察者。"""
        async with self.condition:
            cursor = self.sequence
            frames = ExecutionOutput(deepcopy(self.output.value)).replay(EventEncoder()) if cursor else []
        for frame in frames:
            yield frame
        while True:
            async with self.condition:
                await self.condition.wait_for(lambda: self.sequence > cursor or self.finished)
                lagged = bool(self.frames and cursor < self.frames[0][0] - 1)
                frames = [(seq, frame) for seq, frame in self.frames if seq > cursor]
                finished = self.finished
            if lagged:
                yield EventEncoder().encode(RunErrorEvent(code="EXECUTION_SUBSCRIPTION_LAG", message="状态更新较快，请重新同步原任务。"))
                return
            for seq, frame in frames:
                cursor = seq
                yield frame
            if finished:
                return


_executions: dict[tuple[str, str], BackgroundExecution] = {}
_preparations: set[asyncio.Task] = set()
_accept_locks: dict[str, asyncio.Lock] = {}


def active_detached_count() -> int:
    """退出检查也覆盖接受事务及结果落盘，不能只观察已经登记的 Graph。"""
    return len(_preparations) + sum(not item.finished for item in _executions.values())


def _finish_preparation(task: asyncio.Task) -> None:
    """连接已离开时也消费接受任务异常，避免孤立异常掩盖提交失败。"""
    _preparations.discard(task)
    if not task.cancelled():
        task.exception()


def background_execution_task(run_id: str, workspace: str | None = None) -> asyncio.Task | None:
    """让原停止入口也能取消首个 Graph checkpoint 之前已经接受的任务。"""
    for (root, request_id), execution in _executions.items():
        actual_id = (execution.output.value.get("start") or {}).get("runId", request_id)
        if run_id in {request_id, actual_id} and (not workspace or root == str(Path(workspace).resolve())):
            return execution.task
    return None


def _workspace_and_control(payload: dict, endpoint: str) -> tuple[str, bool]:
    """隔离原控制及只读动作；只有原工作流长任务进入后台执行管理。"""
    props = payload.get("forwardedProps") or {}
    if not isinstance(props, dict):
        return "", False
    nested = props.get("conversation")
    nested = nested if isinstance(nested, dict) else {}
    workspace = props.get("workspaceRoot") or nested.get("workspaceRoot") or ""
    control = bool(props.get("cancelRunId") or props.get("planControlAction") or any(
        props.get(key) for key in ("applicationPlanningRecovery", "requirementSpecDraft", "planningStageHandoff", "requirementAuthorization")))
    technical = props.get("technicalPlanningConversation")
    technical = technical if isinstance(technical, dict) else {}
    if technical.get("action") in {"prepare_revision", "get"}:
        control = True
    return str(Path(workspace).expanduser().resolve()) if isinstance(workspace, str) and workspace else "", control


async def _accept_and_start_locked(payload: dict, endpoint: str, workspace: str,
                            source: Callable[[dict], AsyncIterator[str]]) -> BackgroundExecution | dict:
    """提交去重和后台启动在同一事件循环边界完成，接受失败时绝不启动业务。"""
    props = payload.get("forwardedProps") or {}
    canonical = {"threadId": payload["threadId"], "endpoint": endpoint, "forwardedProps": props,
        "messages": [{"role": item.get("role"), "content": item.get("content")} for item in payload.get("messages", [])]}
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    record, created = await accept_execution(workspace, request_id=payload["runId"], thread_id=payload["threadId"],
        session_id=props.get("sessionId"), endpoint=endpoint, request_hash=digest, body=payload,
        backend_id=current_backend_instance().instance_id)
    key = workspace, payload["runId"]
    if not created:
        return _executions.get(key) or record
    execution = BackgroundExecution(workspace, record, source(payload))
    _executions[key] = execution
    execution.task = asyncio.create_task(execution.run(), name=f"execution:{payload['runId']}")
    # 后台异常必须被消费；业务错误已经以标准 AG-UI 事件和恢复证据保存。
    execution.task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
    return execution


async def _accept_and_start(payload: dict, endpoint: str, workspace: str,
                            source: Callable[[dict], AsyncIterator[str]]) -> BackgroundExecution | dict:
    """短锁只覆盖接受事务到内存 owner 的发布，重复连接不会撞入未发布窗口。"""
    async with _accept_locks.setdefault(workspace, asyncio.Lock()):
        return await _accept_and_start_locked(payload, endpoint, workspace, source)


def build_detached_execution_stream(*, payload: dict, endpoint: str,
                                   source: Callable[[dict], AsyncIterator[str]]) -> AsyncIterator[str]:
    """保留原 AG-UI 端点，重复提交仅订阅原执行，不重入 Graph 或消费确认凭据。"""
    workspace, control = _workspace_and_control(payload, endpoint)
    if not workspace or control:
        return source(payload)
    normalized = {**payload, "runId": payload.get("runId") or str(uuid4()), "threadId": payload.get("threadId") or str(uuid4())}

    async def stream() -> AsyncIterator[str]:
        """连接取消时保留接受事务和真实后台任务，错误仍输出完整 AG-UI 生命周期。"""
        try:
            ExecutionSubmissionIdentity.model_validate(normalized)
            preparation = asyncio.create_task(_accept_and_start(normalized, endpoint, workspace, source))
            _preparations.add(preparation)
            preparation.add_done_callback(_finish_preparation)
            execution = await asyncio.shield(preparation)
            if isinstance(execution, BackgroundExecution):
                async for frame in execution.subscribe():
                    yield frame
            elif execution["finished"]:
                for frame in ExecutionOutput(json.loads(execution["output"])).replay(EventEncoder()):
                    yield frame
            else:
                raise RecoveryExecutionError("EXECUTION_OWNER_LOST", "原后台执行已中断，请同步并使用现有恢复入口。")
        except Exception as exc:
            encoder = EventEncoder()
            thread_id = normalized["threadId"] if isinstance(normalized["threadId"], str) else str(uuid4())
            run_id = normalized["runId"] if isinstance(normalized["runId"], str) else str(uuid4())
            yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
            yield encoder.encode(TextMessageStartEvent(messageId=f"error:{run_id}", role="assistant"))
            yield encoder.encode(TextMessageContentEvent(messageId=f"error:{run_id}", delta=str(exc)))
            yield encoder.encode(TextMessageEndEvent(messageId=f"error:{run_id}"))
            yield encoder.encode(RunErrorEvent(code=getattr(exc, "code", "EXECUTION_ACCEPT_FAILED"), message=str(exc)))
    return stream()


async def shutdown_detached_executions() -> None:
    """Backend 退出时先收口后台任务，再允许关闭 checkpoint；不因前端退出调用此函数。"""
    if _preparations:
        await asyncio.gather(*list(_preparations), return_exceptions=True)
    executions = list(_executions.values())
    tasks = [item.task for item in executions if item.task]
    for item in executions:
        if item.task and not item.output.value.get("end"):
            item.task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
