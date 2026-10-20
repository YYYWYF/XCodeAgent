"""仅为能唯一对应冻结职责分组的任务补入缺失 ID。"""

from collections.abc import Mapping
from typing import Any

from app.services.business_acceptance import DELIVERABLE_TARGET_IDENTITY_FIELD_BY_KIND
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import UnitGenerationContext
from app.services.unit_task_manifest import unit_task_identity_manifest


def bind_missing_task_ids(
    context: UnitGenerationContext, tasks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[ValidationIssue]]:
    """全量证明任务与职责分组一一对应后原子补 ID；已有值、原文及嵌套内容不改。"""

    if not any("id" not in task for task in tasks):
        return tasks, []
    manifest = unit_task_identity_manifest(context)
    requirements = {item.requirement_id: item for item in context.generation_requirements}
    task_by_requirement = {rid: tid for tid, ids in manifest.items() for rid in ids}
    matched: dict[str, int] = {}
    assignments: dict[int, str] = {}
    issues: list[ValidationIssue] = []

    def reject(index: int | None, reason: str, **details: Any) -> None:
        """把无法证明的身份绑定归为当前 Unit 的可重试内容错误。"""

        target = f"tasks[{index}]" if index is not None else "tasks"
        issues.append(ValidationIssue(
            code="RAW_CANDIDATE_TASK_ID_BINDING_INVALID", level="unit", category="generation",
            unit_ids=(context.unit_id,), retry_unit_ids=(context.unit_id,), retryable=True,
            message=f"Unit Candidate {target} 无法唯一绑定冻结任务 ID：{reason}。",
            details={"index": index, "reason": reason, **details},
        ))

    for index, task in enumerate(tasks):
        if task.get("unit_id") != context.unit_id or "task_id" in task:
            reject(index, "任务归属不符或使用了非法 task_id 字段")
            continue
        deliverables = task.get("deliverables")
        if not isinstance(deliverables, (list, tuple)) or not deliverables:
            reject(index, "缺少可证明职责的交付物")
            continue
        provided: list[str] = []
        valid = True
        for deliverable in deliverables:
            if not isinstance(deliverable, Mapping):
                valid = False
                break
            ids = deliverable.get("provides")
            if not isinstance(ids, (list, tuple)) or not ids:
                valid = False
                break
            for rid in ids:
                if not isinstance(rid, str) or rid not in requirements:
                    valid = False
                    break
                refs = requirements[rid].source_refs
                kind = refs.get("kind")
                target_field = DELIVERABLE_TARGET_IDENTITY_FIELD_BY_KIND.get(kind)
                if (target_field is None or not refs.get(target_field)
                    or deliverable.get("kind") != kind
                    or deliverable.get("target_id") != refs[target_field]):
                    valid = False
                    break
                provided.append(rid)
            if not valid:
                break
        if not valid or len(provided) != len(set(provided)):
            reject(index, "交付物职责重复、未知或 kind/target_id 不匹配")
            continue
        expected_ids = {task_by_requirement[rid] for rid in provided}
        if len(expected_ids) != 1:
            reject(index, "一个任务混合了多个冻结任务分组")
            continue
        expected_id = next(iter(expected_ids))
        if set(provided) != set(manifest[expected_id]):
            reject(index, "任务未完整覆盖其冻结职责分组")
            continue
        if expected_id in matched:
            reject(index, "多个任务重复声明同一冻结职责分组", expected_id=expected_id)
            continue
        matched[expected_id] = index
        if "id" in task:
            if task["id"] != expected_id:
                reject(index, "已有 ID 与冻结清单不符，禁止覆盖", expected_id=expected_id)
        else:
            assignments[index] = expected_id
    missing = sorted(set(manifest) - set(matched))
    if missing:
        reject(None, "候选未完整覆盖冻结任务清单", missing_task_ids=missing)
    if issues:
        return tasks, issues
    # 只复制待补入的任务对象，不原地修改 Parser 的原始任务，也不按数组顺序分配身份。
    bound = list(tasks)
    for index, task_id in assignments.items():
        bound[index] = {**tasks[index], "id": task_id}
    return bound, []
