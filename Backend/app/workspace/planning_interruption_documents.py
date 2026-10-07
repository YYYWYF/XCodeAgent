"""按执行身份保存 DAG 中断候选；仅作复用证据，不恢复 Controller 或规划权威。"""

from __future__ import annotations

from collections.abc import Mapping
import hmac
import logging
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.services.planning_frozen import FrozenPlanningModel, FrozenJsonObject, plain_json
from app.services.planning_recovery_contracts import (
    _CandidateMap, _Digest, _Identifier, planning_recovery_snapshot_digest,
)
from app.services.planning_run_contracts import PlanningRun
from app.workspace.json_documents import write_json_atomic
from app.workspace.planning_recovery_documents import planning_recovery_path


_LOGGER = logging.getLogger(__name__)


class PlanningInterruptionSnapshot(FrozenPlanningModel):
    """记录当前已提交 Run 的候选指针，不伪造 failed 状态或失败证据。"""

    schema_version: Literal["planning-interruption.v1"] = "planning-interruption.v1"
    source_workflow_run_id: _Identifier
    source_planning_run_id: _Identifier
    owner_session_id: _Identifier
    thread_id: _Identifier
    source_revision: int = Field(ge=0)
    input_fingerprint: _Identifier
    base_confirmed_plan_digest: _Identifier | None
    build_execution_scope: FrozenJsonObject
    candidates_by_unit: _CandidateMap
    snapshot_digest: _Digest

    @model_validator(mode="after")
    def validate_candidates(self) -> "PlanningInterruptionSnapshot":
        """核对候选来源和内容摘要，拒绝混入别的 Run 或未经局部验证的结果。"""

        for unit_id, candidate in self.candidates_by_unit.items():
            if (
                candidate.identity.unit_id != unit_id
                or candidate.identity.planning_run_id != self.source_planning_run_id
                or candidate.input_fingerprint != self.input_fingerprint
                or candidate.status != "valid"
                or candidate.validation_issues
                or not candidate.tasks
            ):
                raise ValueError("中断候选与当前 PlanningRun 身份或验证结果不一致。")
        if not hmac.compare_digest(
            self.snapshot_digest, planning_recovery_snapshot_digest(self.model_dump(mode="json"))
        ):
            raise ValueError("中断候选快照摘要不匹配。")
        return self


def planning_interruption_path(state: dict, source_workflow_run_id: str) -> Path:
    """复用执行 ID 的路径校验，把中断证据放在独立目录。"""

    checked = planning_recovery_path(state, source_workflow_run_id)
    return checked.parent.parent / "planning-interruption" / checked.name


def persist_planning_interruption(state: Mapping, run: PlanningRun) -> None:
    """串行提交后尽力保存当前候选；全局修复失效的旧指针不能再次复用。"""

    owner = str(state.get("owner_session_id") or "").strip()
    if not owner:
        return
    try:
        candidates = {
            key: run.candidates[unit.latest_candidate_id].model_dump(mode="json")
            for key, unit in run.unit_states.items()
            if key in run.planning_unit_ids
            and unit.generation_status == "candidate_ready"
            and unit.latest_candidate_id is not None
        } if run.status == "active" else {}
        payload = {
            "schema_version": "planning-interruption.v1",
            "source_workflow_run_id": run.workflow_run_id,
            "source_planning_run_id": run.planning_run_id,
            "owner_session_id": owner,
            "thread_id": run.thread_id,
            "source_revision": run.revision,
            "input_fingerprint": run.input_fingerprint,
            "base_confirmed_plan_digest": run.base_confirmed_plan_digest,
            "build_execution_scope": plain_json(run.build_execution_scope),
            "candidates_by_unit": candidates,
        }
        payload["snapshot_digest"] = planning_recovery_snapshot_digest(payload)
        snapshot = PlanningInterruptionSnapshot.model_validate(payload)
        write_json_atomic(planning_interruption_path(dict(state), run.workflow_run_id),
                          snapshot.model_dump(mode="json"))
    except Exception:
        # 复用优化写入失败不能改变正常生成、Local Retry 或 Global Repair 的原始结果。
        _LOGGER.warning("Failed to persist DAG interruption candidates", exc_info=True)


def load_planning_interruption(
    state: Mapping, source_workflow_run_id: str,
) -> PlanningInterruptionSnapshot | None:
    """只读取后端指定来源，并校验会话、线程及最近提交版本；异常放弃复用。"""

    from app.workspace.planning_run_documents import load_planning_run

    try:
        path = planning_interruption_path(dict(state), source_workflow_run_id)
        snapshot = PlanningInterruptionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        projection = load_planning_run(dict(state))
        if (
            snapshot.source_workflow_run_id != source_workflow_run_id
            or snapshot.owner_session_id != state.get("owner_session_id")
            or snapshot.thread_id != state.get("active_thread_id")
            or not projection
            or projection["workflow_run_id"] != source_workflow_run_id
            or projection["planning_run_id"] != snapshot.source_planning_run_id
            or projection["revision"] != snapshot.source_revision
            or projection["status"] != "active"
        ):
            return None
        return snapshot
    except FileNotFoundError:
        return None
    except Exception:
        _LOGGER.warning("Ignoring invalid DAG interruption candidates", exc_info=True)
        return None
