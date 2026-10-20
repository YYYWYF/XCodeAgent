"""设计正式修订的 durable 入场：先保存 Graph 输入，再消费影响确认。"""
from __future__ import annotations

from typing import Any

from app.domain.application_revision import ActiveFormalRevision, FormalRevisionBranch
from app.domain.application_lifecycle import utc_now
from app.services.workflow_quality_results import pending_quality_results
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    _application_lifecycle_lock,
    application_lifecycle_path,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.application_revision_lifecycle import (
    _build_active_formal_revision,
)


def design_revision_input(
    marker: Any, *, workspace: str, request: str, workflow_scope: str, resume_from: str,
) -> dict[str, Any]:
    """把首次批准凭据冻结为 Graph 输入；此阶段不消费 pending impact。"""
    if marker is None:
        return {}
    if (workflow_scope != 'application_planning' or resume_from != 'design_intent_analysis'
            or not isinstance(marker, dict) or set(marker) != {'changeId', 'interactionId'}):
        raise ValueError('设计修订凭据只能进入原规划线程的设计意图节点。')
    lifecycle = load_application_lifecycle(workspace)
    pending = lifecycle.pending_revision_impact if lifecycle is not None else None
    if (pending is None or pending.change_id != marker['changeId']
            or pending.interaction_id != marker['interactionId']
            or pending.impact.formal_branch is not FormalRevisionBranch.DESIGN_STAGE_REVISION
            or pending.request != request.strip()
            or pending.based_on_lifecycle_revision != lifecycle.revision):
        raise ValueError('设计修订凭据与当前待批准影响不匹配。')
    return {'design_revision_intent': dict(marker), 'change_id': pending.change_id,
            'change_target': pending.target.model_dump(mode='python', by_alias=False)}


def design_revision_matches(lifecycle: Any, intent: dict[str, Any], thread_id: str) -> bool:
    """证明冻结的批准凭据仍属于当前规划线程和原正式修订，不比较后续修改要求。"""
    if lifecycle is None or lifecycle.initialization.thread_id != thread_id:
        return False
    active = lifecycle.active_formal_revision
    pending = lifecycle.pending_revision_impact
    if active is not None:
        return (active.formal_branch is FormalRevisionBranch.DESIGN_STAGE_REVISION
                and active.change_id == intent.get('changeId')
                and active.impact_interaction_id == intent.get('interactionId')
                and active.status == 'design_planning')
    return bool(pending is not None
                and pending.impact.formal_branch is FormalRevisionBranch.DESIGN_STAGE_REVISION
                and pending.change_id == intent.get('changeId')
                and pending.interaction_id == intent.get('interactionId'))


def approve_design_revision_from_checkpoint(
    workspace: str, intent: dict[str, Any], thread_id: str,
) -> ActiveFormalRevision:
    """在持久化节点内幂等批准原修订，允许恢复 handoff 合法推进 lifecycle revision。"""
    with _application_lifecycle_lock(application_lifecycle_path(workspace)):
        current = load_application_lifecycle(workspace)
        if not design_revision_matches(current, intent, thread_id):
            raise ApplicationLifecycleConflictError('设计修订 durable 输入与当前修订归属不匹配。')
        active = current.active_formal_revision
        if active is None:
            # 首次输入已在协议边界验证；恢复只沿原凭据入场，不能重新提交旧卡片或改变请求。
            active = _build_active_formal_revision(current, current.pending_revision_impact)
            updated = current.model_copy(update={
                'updated_at': utc_now(), 'revision': current.revision + 1,
                'pending_revision_impact': None, 'active_formal_revision': active,
                'extensions': pending_quality_results(current.extensions),
            })
            write_application_lifecycle(workspace, updated, expected_revision=current.revision)
        return active
