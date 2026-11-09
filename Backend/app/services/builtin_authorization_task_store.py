"""隔离编排快照、跨进程短事务及网络边界输入保护。"""

from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
import re
from typing import Callable, Literal

import portalocker
from pydantic import BaseModel, ConfigDict, Field

from app.services.authorization_workspace_projector import RUNTIME, read_record
from app.utils.atomic_json import atomic_write_json

input_guard: ContextVar[Callable[[], None] | None] = ContextVar("builtin_task_input_guard", default=None)
_state_observer: ContextVar[Callable[[dict], None] | None] = ContextVar("builtin_task_state_observer", default=None)
_transactions: ContextVar[frozenset[str]] = ContextVar("builtin_task_transactions", default=frozenset())


def check_task_inputs() -> None:
    """仅在受管 tick 中复核任务身份，独立原子操作保持既有行为。"""

    guard = input_guard.get()
    if guard is not None:
        guard()


def observe_operation_state(record: dict) -> None:
    """原操作保护已落盘后同步编排防丢标记，在 POST 获准执行前完成。"""

    observer = _state_observer.get()
    if observer is not None:
        observer(record)


def read_operation_record(root: Path) -> dict:
    """编排只接受当前权威状态结构，未知字段值不能被当作首次操作。"""

    task_path(root, "state.json")
    record = read_record(root, "state.json")
    if not record:
        if task_path(root, "state.json").exists():
            raise ValueError("权限原操作文件为空，不能视作首次提交。")
        return record
    if (record.get("schemaVersion") != 1 or record.get("datasourceMode") != "builtin"
            or not isinstance(record.get("datasourceId"), int) or isinstance(record["datasourceId"], bool)
            or record["datasourceId"] <= 0
            or not re.fullmatch(r"[0-9a-f]{12}", str(record.get("datasourceKey", "")))
            or not isinstance(record.get("ddlFingerprint"), str)
            or record.get("status") not in {"pending", "submitting", "unknown", "running", "succeeded", "failed", "schema_ready"}):
        raise ValueError("权限原操作状态结构无效。")
    if "operationId" in record and (not isinstance(record["operationId"], str)
                                    or not re.fullmatch(r"[1-9][0-9]{0,18}", record["operationId"])):
        raise ValueError("权限原操作编号无效。")
    if "seed" in record:
        seed = record["seed"]
        if (not isinstance(seed, dict) or seed.get("status") not in {
                "submitting", "unknown", "running", "succeeded", "failed", "seed_ready"}
                or not re.fullmatch(r"[0-9a-f]{64}", str(seed.get("idempotencyKey", "")))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(seed.get("seedFingerprint", "")))
                or "operationId" in seed and (not isinstance(seed["operationId"], str)
                                            or not re.fullmatch(r"[1-9][0-9]{0,18}", seed["operationId"]))):
            raise ValueError("权限原 Seed 身份结构无效。")
    return record


class AuthorizationTask(BaseModel):
    """仅持久化不可逆摘要和调度身份，成功事实仍须读取 state.json。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[1] = 1
    generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    inputFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationStateExpected: bool = False
    seedStateExpected: bool = False
    status: Literal["pending", "running", "waiting", "ready", "needs_attention", "failed", "cancelled"]
    stage: Literal["schema", "seed", "verify", "done"] = "schema"
    failureCategory: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,95}$")
    updatedAt: str = Field(min_length=1, max_length=64)


def task_path(root: Path, name: str) -> Path:
    """拒绝编排文件和所有父路径的软链接，限制于真实工作区。"""

    path = root / RUNTIME / name
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("权限编排路径不安全。")
    if not path.resolve().is_relative_to(root):
        raise ValueError("权限编排路径越界。")
    return path


def read_task(root: Path) -> AuthorizationTask | None:
    """严格读取当前合同，损坏和空文件不能视作首次提交。"""

    path = task_path(root, "orchestration.json")
    if not path.exists():
        return None
    if path.stat().st_size > 4096:
        raise ValueError("权限编排文件超过大小限制。")
    return AuthorizationTask.model_validate(read_record(root, path.name))


def save_task(root: Path, task: AuthorizationTask) -> None:
    """以已有原子写入器保存独立快照，不改动 Seed 的输入快照。"""

    atomic_write_json(task_path(root, "orchestration.json"), task.model_dump())


@contextmanager
def task_input_scope(root: Path, task: AuthorizationTask, current_fingerprint: Callable[[], str]):
    """为当前 tick 安装输入与代次检查，独立记录 Seed 子对象的防丢事实。"""

    def guard() -> None:
        """远端请求及状态写入边界重新读取当前任务与正式输入。"""

        current = read_task(root)
        if (current is None or current.generation != task.generation or current.status not in {"pending", "running", "waiting"}
                or current_fingerprint() != task.inputFingerprint):
            raise ValueError("权限初始化输入或执行身份已变化。")
        record = read_operation_record(root)
        if task.operationStateExpected and not record or task.seedStateExpected and "seed" not in record:
            raise ValueError("权限原操作保护丢失。")

    def observe(record: dict) -> None:
        """只记录 Seed 保护已经出现，不复制操作身份或业务成功事实。"""

        if "seed" in record and not task.seedStateExpected:
            guard()
            task.seedStateExpected = True
            save_task(root, task)

    token = input_guard.set(guard)
    observer_token = _state_observer.set(observe)
    try:
        yield guard
    finally:
        _state_observer.reset(observer_token)
        input_guard.reset(token)


@contextmanager
def task_transaction(root: Path, *, timeout: float = 1):
    """在整个 tick 中持有跨进程锁，等待轮询时调用方必须释放。"""

    key = str(root)
    held = _transactions.get()
    if key in held:
        yield
        return
    path = task_path(root, "orchestration.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(path), mode="a", timeout=timeout):
        token = _transactions.set(held | {key})
        try:
            yield
        finally:
            _transactions.reset(token)
