"""协调 Bootstrap、删除与 Workspace Attach 的进程内事务边界。"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.domain.application_lifecycle import (
    ApplicationLifecycleError,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    utc_now,
)
from app.services.application_lifecycle import load_application_lifecycle, persist_application_lifecycle_transition
from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH
from app.services.workspace_bootstrap.materializer import BOOTSTRAP_STAGING_RELATIVE_PATH
from app.services.workspace_bootstrap.models import WorkspaceBootstrapError


class BootstrapPhase(StrEnum):
    """区分删除可取消的 Preparation 与必须等待的 Commit Section。"""

    PREPARATION = "preparation"
    COMMIT = "commit"


@dataclass
class _ActiveMutation:
    """保存单个工作区当前 Bootstrap 的阶段与取消信号。"""

    phase: BootstrapPhase
    cancel_requested: bool = False


@dataclass(frozen=True)
class WorkspaceAttachResult:
    """返回 Attach 是否完成了中断 Bootstrap 收尾。"""

    action: str
    cleaned: bool
    lifecycle_changed: bool


class TemplateMutationCoordinator:
    """以工作区为粒度串行化 Bootstrap、删除 fence 和 Attach 收尾。"""

    def __init__(self) -> None:
        """初始化可等待 Commit Section 结束的进程内状态表。"""

        self._condition = threading.Condition(threading.RLock())
        self._active: dict[str, _ActiveMutation] = {}
        self._deleting: set[str] = set()

    def begin_preparation(self, workspace: str | Path) -> None:
        """登记新的 Bootstrap Preparation，并拒绝重复任务或删除中的工作区。"""

        key = _workspace_key(workspace)
        with self._condition:
            if key in self._deleting:
                raise WorkspaceBootstrapError("应用正在删除，不能开始 Bootstrap。")
            if key in self._active:
                raise WorkspaceBootstrapError("该 Workspace 已有 Bootstrap 任务。")
            self._active[key] = _ActiveMutation(phase=BootstrapPhase.PREPARATION)

    def enter_commit_section(self, workspace: str | Path) -> None:
        """将已登记任务切入不可取消的 Commit Section。"""

        with self._condition:
            mutation = self._active.get(_workspace_key(workspace))
            if mutation is None:
                raise WorkspaceBootstrapError("Bootstrap 任务未登记，不能进入 Commit Section。")
            if mutation.cancel_requested:
                raise WorkspaceBootstrapError("Bootstrap Preparation 已被应用删除取消。")
            mutation.phase = BootstrapPhase.COMMIT

    def raise_if_preparation_cancelled(self, workspace: str | Path) -> None:
        """供下载、校验和解压等 Preparation 步骤在安全点响应删除取消。"""

        with self._condition:
            mutation = self._active.get(_workspace_key(workspace))
            if mutation is not None and mutation.cancel_requested:
                raise WorkspaceBootstrapError("Bootstrap Preparation 已被应用删除取消。")

    def finish(self, workspace: str | Path) -> None:
        """注销已完成或已回滚的 Bootstrap，并唤醒等待删除的调用方。"""

        with self._condition:
            self._active.pop(_workspace_key(workspace), None)
            self._condition.notify_all()

    def begin_deletion(self, workspace: str | Path, *, timeout_seconds: float = 30.0) -> None:
        """取消 Preparation，或等待 Commit Section 完成/回滚后建立删除 fence。"""

        key = _workspace_key(workspace)
        with self._condition:
            self._deleting.add(key)
            mutation = self._active.get(key)
            if mutation is not None and mutation.phase == BootstrapPhase.PREPARATION:
                mutation.cancel_requested = True
            deadline = time.monotonic() + max(timeout_seconds, 0.0)
            while key in self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._deleting.discard(key)
                    raise WorkspaceBootstrapError("等待 Bootstrap Commit Section 结束超时。")
                self._condition.wait(timeout=remaining)

    def cancel_deletion(self, workspace: str | Path) -> None:
        """在删除 Preparation 失败时解除本协调器的删除 fence。"""

        with self._condition:
            self._deleting.discard(_workspace_key(workspace))

    def has_active_mutation(self, workspace: str | Path) -> bool:
        """返回 Workspace 是否仍由本 Backend 持有 Bootstrap 任务。"""

        with self._condition:
            return _workspace_key(workspace) in self._active

    def attach_workspace(self, workspace: str | Path) -> WorkspaceAttachResult:
        """仅在孤儿 GENERATING 状态下清理首次 Bootstrap 受管范围并标记失败。"""

        root = Path(workspace).expanduser().resolve(strict=False)
        lifecycle = load_application_lifecycle(root)
        if lifecycle is None or lifecycle.initialization.stage != ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES:
            return WorkspaceAttachResult("none", False, False)
        with self._condition:
            if self.has_active_mutation(root):
                return WorkspaceAttachResult("active", False, False)
            self._deleting.add(_workspace_key(root))
        try:
            _cleanup_interrupted_bootstrap(root)
            updated = persist_application_lifecycle_transition(
                root,
                stage=ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
                status=ApplicationLifecycleStatus.FAILED,
                error=ApplicationLifecycleError(
                    code="application_template_generation_interrupted",
                    message="Backend 在 Bootstrap 完成前停止，Workspace Attach 已完成受管范围清理。",
                    recoverable=False,
                    occurredAt=utc_now(),
                ),
            )
            return WorkspaceAttachResult("recovered", True, updated.initialization.stage == ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED)
        except Exception:
            # 清理失败必须保持 GENERATING，下一次 Attach 才能继续确定性收尾。
            return WorkspaceAttachResult("cleanup_failed", False, False)
        finally:
            self.cancel_deletion(root)


def _cleanup_interrupted_bootstrap(workspace: Path) -> None:
    """精确删除首次 Bootstrap 受管 roots、仓库、State 与 staging。"""

    for relative in ("frontend", "backend", ".git", TEMPLATE_STATE_RELATIVE_PATH, BOOTSTRAP_STAGING_RELATIVE_PATH):
        path = workspace / relative
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
    remaining = [
        str(relative)
        for relative in ("frontend", "backend", ".git", TEMPLATE_STATE_RELATIVE_PATH, BOOTSTRAP_STAGING_RELATIVE_PATH)
        if (workspace / relative).exists() or (workspace / relative).is_symlink()
    ]
    if remaining:
        raise WorkspaceBootstrapError("Workspace Attach 未能清理受管产物：" + "、".join(remaining))


def _workspace_key(workspace: str | Path) -> str:
    """生成跨调用一致的工作区键。"""

    return os.path.normcase(str(Path(workspace).expanduser().resolve(strict=False)))


template_mutation_coordinator = TemplateMutationCoordinator()
