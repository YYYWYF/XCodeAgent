"""当前 Engine 文件 Operation 的 Reconcile 专属所有权预检。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.template_reconcile.models import (
    AddFileOperation,
    ChangeSetBody,
    DeleteFileOperation,
    FileOperation,
    TemplateState,
    UpdateFileOperation,
)


class OwnershipKind(StrEnum):
    """表示当前 Engine 可推导的最小 Workspace 所有权类别。"""

    ENGINE_EXCLUSIVE = "ENGINE_EXCLUSIVE"
    BUSINESS_AGENT = "BUSINESS_AGENT"


class TemplateOperationOwnershipError(ValueError):
    """表示 Engine Operation 越过当前 State 可证明的所有权边界。"""

    code = "TEMPLATE_OPERATION_OWNERSHIP_CONFLICT"


@dataclass(frozen=True)
class ClassifiedOperation:
    """保存预检后 Operation 的原始顺序、路径与所有权结论。"""

    index: int
    operation: FileOperation
    ownership: OwnershipKind


class ReconcileOwnershipRegistry:
    """按 Current/Next managedFiles 校验本次 Reconcile 的文件所有权。"""

    def __init__(self, current_state: TemplateState, next_state: TemplateState) -> None:
        """保存本次 ChangeSet 唯一允许引用的 Current 与 Next State。"""

        self._current_paths = frozenset(current_state.managedFiles)
        self._next_paths = frozenset(next_state.managedFiles)

    def classify_path(self, path: str) -> OwnershipKind:
        """把当前或目标 State 受管路径归类为 Engine Exclusive。"""

        if path in self._current_paths or path in self._next_paths:
            return OwnershipKind.ENGINE_EXCLUSIVE
        return OwnershipKind.BUSINESS_AGENT

    def classify_change_set(self, change_set: ChangeSetBody) -> list[ClassifiedOperation]:
        """按 Engine 原始顺序校验并返回每个 Operation 的所有权结论。"""

        return [
            self.classify_operation(index, operation)
            for index, operation in enumerate(change_set.operations)
        ]

    def classify_operation(
        self,
        index: int,
        operation: FileOperation,
    ) -> ClassifiedOperation:
        """校验单个文件 Operation 与 Current/Next State 的转移关系。"""

        path = operation.path
        ownership = self.classify_path(path)
        if ownership is not OwnershipKind.ENGINE_EXCLUSIVE:
            self._raise_conflict(index, operation, "路径不属于 Current 或 Next managedFiles")
        if isinstance(operation, AddFileOperation):
            if path in self._current_paths or path not in self._next_paths:
                self._raise_conflict(index, operation, "ADD_FILE 必须只新增 Next managedFiles 路径")
        elif isinstance(operation, UpdateFileOperation):
            if path not in self._current_paths or path not in self._next_paths:
                self._raise_conflict(
                    index,
                    operation,
                    "UPDATE_FILE 必须同时属于 Current 与 Next managedFiles",
                )
        elif isinstance(operation, DeleteFileOperation):
            if path not in self._current_paths or path in self._next_paths:
                self._raise_conflict(index, operation, "DELETE_FILE 必须只移除 Current managedFiles 路径")
        else:
            self._raise_conflict(index, operation, "当前 Engine 不支持该 Operation 类型")
        return ClassifiedOperation(index=index, operation=operation, ownership=ownership)

    def _raise_conflict(
        self,
        index: int,
        operation: FileOperation,
        reason: str,
    ) -> None:
        """构造含稳定错误码、Operation 下标和路径的预检失败。"""

        raise TemplateOperationOwnershipError(
            f"{TemplateOperationOwnershipError.code}：operation[{index}] "
            f"{operation.type} {operation.path}：{reason}。"
        )
