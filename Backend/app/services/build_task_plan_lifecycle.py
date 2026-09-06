"""BuildTaskPlan 草稿生命周期模型；不处理确认、放弃或提升动作。"""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

from app.services.planning_frozen import FrozenJsonObject, FrozenPlanningModel


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DraftIdentity(FrozenPlanningModel):
    """绑定用户眼前 PendingPlan 的服务端身份与稳定内容摘要。"""

    planning_run_id: _Identifier
    draft_digest: _Sha256
    base_confirmed_plan_digest: _Sha256 | None
    input_fingerprint: _Sha256
    build_execution_scope: FrozenJsonObject
    created_at: _Identifier
