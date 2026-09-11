"""P0.3A RecoveryPoint 历史选择器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import list_recovery_points


@dataclass(frozen=True)
class RecoveryPointSelection:
    """保存选择器筛出的候选现场及无法选择时的稳定原因。"""

    point: RecoveryPoint | None
    reason_code: str
    reason: str
    candidates: tuple[RecoveryPoint, ...] = ()


class RecoveryPointSelector:
    """从完整 RecoveryPoint history 中选择仍可能恢复的 checkpoint。"""

    async def select_recovery_point(
        self,
        *,
        workspace: str,
        source: DurableExecutionRecord,
        graph: Any,
    ) -> RecoveryPointSelection:
        """按历史顺序折叠重复观察并从新到旧返回 checkpoint 候选。"""

        del graph
        history = await list_recovery_points(workspace, source.run_id)
        canonical = _collapse_checkpoint_observations(history)
        malformed_thread_point = next(
            (
                point
                for point in canonical
                if _has_checkpoint_identity(point)
                and point.thread_id != source.thread_id
            ),
            None,
        )
        if malformed_thread_point is not None:
            return RecoveryPointSelection(
                point=malformed_thread_point,
                reason_code="CHECKPOINT_THREAD_MISMATCH",
                reason="RecoveryPoint 的 threadId 与 source execution 不一致。",
            )

        candidates = tuple(
            point
            for point in reversed(canonical)
            if _is_native_checkpoint_candidate(point)
        )
        if not candidates:
            return RecoveryPointSelection(
                point=None,
                reason_code="NO_RECOVERY_POINT",
                reason="没有找到包含真实 checkpoint 和后继节点的历史恢复现场。",
            )
        return RecoveryPointSelection(
            point=candidates[0],
            reason_code="RECOVERY_POINT_SELECTED",
            reason="已从 RecoveryPoint history 选择最近的可验证 checkpoint 候选。",
            candidates=candidates,
        )


async def select_recovery_point(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    graph: Any,
) -> RecoveryPointSelection:
    """提供无状态选择器入口，保持 Coordinator 可直接注入或替换。"""

    return await RecoveryPointSelector().select_recovery_point(
        workspace=workspace,
        source=source,
        graph=graph,
    )


def _collapse_checkpoint_observations(
    history: list[RecoveryPoint],
) -> list[RecoveryPoint]:
    """按 checkpoint 身份折叠重复观测并优先保留成功边界记录。"""

    collapsed: list[RecoveryPoint] = []
    positions: dict[tuple[str, str, str], int] = {}
    for point in history:
        if point.kind is not RecoveryPointKind.CHECKPOINT or not point.checkpoint_id:
            collapsed.append(point)
            continue
        key = (point.thread_id, point.checkpoint_ns, point.checkpoint_id)
        position = positions.get(key)
        if position is None:
            positions[key] = len(collapsed)
            collapsed.append(point)
            continue
        previous = collapsed[position]
        if previous.completed_node is None and point.completed_node is not None:
            collapsed[position] = point
        elif previous.completed_node is not None and point.completed_node is None:
            continue
        else:
            collapsed[position] = point
    return collapsed


def _has_checkpoint_identity(point: RecoveryPoint) -> bool:
    """判断现场是否足够像 checkpoint，以便执行严格身份校验。"""

    return (
        point.kind is RecoveryPointKind.CHECKPOINT
        and bool(point.checkpoint_id)
        and bool(point.next_nodes)
    )


def _is_native_checkpoint_candidate(point: RecoveryPoint) -> bool:
    """过滤入口现场、空后继和已到终点的失败观察。"""

    if not _has_checkpoint_identity(point) or point.thread_id == "":
        return False
    normalized_next = {str(node).strip().lower() for node in point.next_nodes}
    if normalized_next and normalized_next.issubset({"end", "__end__"}):
        return False
    state_status = str(point.state_status or "").strip().lower()
    if state_status in {"failed", "cancelled", "stopped", "interrupted"}:
        return point.completed_node is not None
    return True
