"""PlanningRun 单写者：串行转换、持久化和轻量投影发布，不调度或调用 Worker。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.services import planning_run as transitions
from app.services.planning_frozen import freeze_json
from app.services.planning_run_contracts import PlanningRun
from app.services.planning_run_events import (
    AssemblyStarted, CandidateInvalid, CandidateReady, GenerationStarted,
    GlobalCheckStarted, GlobalRepairStarted, GlobalValidationStarted,
    PendingPersistenceStarted, PlanningRunEvent, RoundExhausted, RunCancelled,
    RunFailed, UnitAttemptStarted, UnitValidationStarted,
)
from app.workspace.planning_run_documents import project_planning_run, write_planning_run_atomic
from app.workspace.spec_documents import workspace_root


SnapshotPublisher = Callable[[Mapping[str, Any]], Awaitable[None]]
_TRANSITIONS = {
    GenerationStarted: (transitions.begin_generation, None),
    UnitAttemptStarted: (transitions.mark_unit_generating, "identity"),
    UnitValidationStarted: (transitions.mark_unit_validating, "identity"),
    CandidateInvalid: (transitions.record_candidate_invalid, "candidate"),
    CandidateReady: (transitions.record_candidate_ready, "candidate"),
    RoundExhausted: (transitions.mark_round_exhausted, "unit_id"),
    GlobalCheckStarted: (transitions.begin_global_check, None),
    GlobalRepairStarted: (transitions.begin_global_repair, "issues"),
    AssemblyStarted: (transitions.begin_assembly, None),
    GlobalValidationStarted: (transitions.begin_validation, None),
    PendingPersistenceStarted: (transitions.begin_pending_persistence, None),
    RunFailed: (transitions.fail, "issue"),
    RunCancelled: (transitions.cancel, None),
}


class PlanningRunPersistenceError(RuntimeError):
    """原子持久化失败；事件未提交，snapshot 和磁盘仍是此前的版本。"""

    def __init__(self, revision: int) -> None:
        """记录未能提交的 revision，底层 IO 错误通过异常链保留。"""

        self.revision = revision
        super().__init__(f"PlanningRun revision {revision} persistence failed; event not committed.")


class PlanningRunProjectionError(RuntimeError):
    """投影发布失败；事件已经落盘，调用方不得重放该事件。"""

    def __init__(self, snapshot: PlanningRun) -> None:
        """携带已经提交的只读快照，避免将通知失败误判成持久化失败。"""

        self.snapshot = snapshot
        super().__init__(f"PlanningRun revision {snapshot.revision} committed, but projection publication failed.")


class PlanningRunController:
    """一个活动 Run 由一个 Controller 实例持有，全部提交在同一事件循环中串行执行。

    构造只接收完整内存 Run，不读盘恢复、不写初态。第一次 apply 才提交文件。
    snapshot 是领域只读快照；projection/发布钩子仅含 T5.3 轻量数据。
    后续编排方负责为每个 Run 保持唯一实例，只向 Worker 传递 Job 和事件提交入口，
    不传递完整 Run、其他 Unit Candidate 或 workspace writer。
    """

    def __init__(
        self, initial_run: PlanningRun, workspace_state: Mapping[str, Any], *,
        publish: SnapshotPublisher | None = None,
    ) -> None:
        """冻结初始领域状态和工作区路径；注入的发布钩子供后续 AG-UI 适配器使用。"""

        if not isinstance(initial_run, PlanningRun):
            raise TypeError("Controller 必须接收完整内存 PlanningRun，不能从轻量磁盘投影恢复。")
        self._snapshot = PlanningRun.model_validate(initial_run)
        self._workspace_state = {"workspace": str(workspace_root(dict(workspace_state)).resolve())}
        self._publish = publish
        self._lock = asyncio.Lock()
        self._transaction: asyncio.Task[PlanningRun] | None = None

    @property
    def snapshot(self) -> PlanningRun:
        """返回最近提交的深度只读领域快照，持久化中的暂存状态不可见。"""

        return self._snapshot

    @property
    def projection(self) -> Mapping[str, Any]:
        """返回与持久化相同形状的递归只读轻量投影，不包含 Candidate 正文。"""

        return freeze_json(project_planning_run(self._snapshot))

    async def apply(self, event: PlanningRunEvent) -> PlanningRun:
        """串行验证和提交事件；取消等待者不允许中断已开始的写入或提前释放锁。"""

        if asyncio.current_task() is self._transaction:
            raise RuntimeError("发布钩子不能重入同一个 Controller.apply。")
        if type(event) not in _TRANSITIONS:
            raise TypeError("必须提交受支持的冻结 PlanningRunEvent，不能提交状态补丁或回调。")
        event = type(event).model_validate(event)
        async with self._lock:
            transition, field = _TRANSITIONS[type(event)]
            args = () if field is None else (getattr(event, field),)
            # T5.1 已经负责合法性验证和 revision+1，这里不能再递增一次。
            proposed = transition(self._snapshot, *args, at=event.at)
            self._transaction = asyncio.create_task(self._commit(proposed))
            cancelled = False
            try:
                # to_thread 的底层写入无法强制撤回；必须等提交/发布结束后再释放单写锁。
                while not self._transaction.done():
                    try:
                        await asyncio.shield(self._transaction)
                    except asyncio.CancelledError:
                        cancelled = True
                result = self._transaction.result()
                if cancelled:
                    raise asyncio.CancelledError
                return result
            finally:
                self._transaction = None

    async def _commit(self, proposed: PlanningRun) -> PlanningRun:
        """原子落盘成功后更新当前快照并发布同版投影；失败不暴露未提交状态。"""

        try:
            await asyncio.to_thread(write_planning_run_atomic, self._workspace_state, proposed)
        except Exception as exc:
            raise PlanningRunPersistenceError(proposed.revision) from exc
        self._snapshot = proposed
        if self._publish is not None:
            try:
                await self._publish(self.projection)
            except Exception as exc:
                # 发布失败不能撤回已经完成的原子替换，也不能再次递增 revision 重放事件。
                raise PlanningRunProjectionError(proposed) from exc
        return proposed
