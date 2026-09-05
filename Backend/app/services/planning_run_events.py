"""Controller 内部冻结事件；仅传递转换参数，不携带任意状态补丁或写入能力。"""

from typing import TypeAlias

from app.services.planning_frozen import FrozenPlanningModel
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import Id, Issues
from app.services.unit_generation_contracts import AttemptIdentity, CandidateAttempt


class _Event(FrozenPlanningModel):
    """事件时间由提交方显式提供，revision 只能由 Controller 内的状态转换产生。"""

    at: Id


class GenerationStarted(_Event):
    """请求从 preparing 进入生成阶段。"""


class UnitAttemptStarted(_Event):
    """登记平台已分配的本次 Unit Attempt 身份。"""

    identity: AttemptIdentity


class UnitValidationStarted(_Event):
    """请求校验当前预期 Attempt 的生成结果。"""

    identity: AttemptIdentity


class CandidateInvalid(_Event):
    """提交当前 Attempt 的无效候选及结构化内容问题。"""

    candidate: CandidateAttempt


class CandidateReady(_Event):
    """提交已经通过 Local Validation 的当前候选。"""

    candidate: CandidateAttempt


class RoundExhausted(_Event):
    """标记指定 Unit 本轮模型尝试耗尽。"""

    unit_id: Id


class GlobalCheckStarted(_Event):
    """请求通过本轮 Barrier 并进入全局完整性检查。"""


class GlobalRepairStarted(_Event):
    """提交全部 Global Issues，按显式目标原子重开相关 Unit。"""

    issues: Issues


class AssemblyStarted(_Event):
    """请求开始组装齐全的当前 Candidate。"""


class GlobalValidationStarted(_Event):
    """标记组装完成并进入全局校验阶段。"""


class PendingPersistenceStarted(_Event):
    """标记全局校验通过；事件本身不写 PendingPlan。"""


class RunFailed(_Event):
    """以明确不可重试的问题终止当前 Run。"""

    issue: ValidationIssue


class RunCancelled(_Event):
    """请求取消仍处于 active 的 Run。"""


PlanningRunEvent: TypeAlias = (
    GenerationStarted | UnitAttemptStarted | UnitValidationStarted | CandidateInvalid
    | CandidateReady | RoundExhausted | GlobalCheckStarted | GlobalRepairStarted
    | AssemblyStarted | GlobalValidationStarted | PendingPersistenceStarted | RunFailed | RunCancelled
)
