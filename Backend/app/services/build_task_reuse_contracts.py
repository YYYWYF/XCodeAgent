"""确定性 Planning reuse 事实契约，不表达 Unit 生成决策或执行成功状态。"""

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, PlainSerializer, StringConstraints

from app.services.planning_frozen import (
    FrozenJsonObject, FrozenPlanningModel, freeze_json, plain_json, tuple_input,
)
from app.services.planning_issues import ValidationIssue


_Identity = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Identities = Annotated[tuple[_Identity, ...], BeforeValidator(tuple_input)]
_TaskIndex = Annotated[
    Mapping[_Identity, _Identities], BeforeValidator(plain_json), AfterValidator(freeze_json),
    PlainSerializer(plain_json, return_type=dict),
]
_CapabilityIndex = Annotated[
    Mapping[_Identity, _TaskIndex], BeforeValidator(plain_json), AfterValidator(freeze_json),
    PlainSerializer(plain_json, return_type=dict),
]


class RetainedEndpointOwner(FrozenPlanningModel):
    """记录一个正式 Endpoint 的前端实现职责，不推断路径或整个 Unit 的复用策略。"""

    api_contract_id: _Identity
    endpoint_id: _Identity
    owner_task_id: _Identity
    owner_unit_id: _Identity


class ExternalCapability(FrozenPlanningModel):
    """记录已由平台只读检查证明的 workspace 能力；没有 provider Task 依赖。"""

    unit_id: _Identity
    capability_id: _Identity
    source: Literal["template_state", "authorization_resource_catalog"]
    workspace_revision: _Identity
    source_refs: FrozenJsonObject


class ReuseFacts(FrozenPlanningModel):
    """保存全部 confirmed Task 及精确职责事实；有 issues 时调用方必须先阻断规划。

    reusable_capabilities_by_unit 的形状为 Unit -> capability ID -> provider Task IDs。
    它不包含 external capabilities，也不表示任何 Unit 已完全满足当前需求。
    冲突 Endpoint 的所有 owner 均保留供诊断，不能从中任取一个作为依赖。
    """

    retained_task_ids_by_unit: _TaskIndex
    reusable_capabilities_by_unit: _CapabilityIndex
    retained_endpoint_owners: Annotated[tuple[RetainedEndpointOwner, ...], BeforeValidator(tuple_input)]
    external_capabilities: Annotated[tuple[ExternalCapability, ...], BeforeValidator(tuple_input)]
    issues: Annotated[tuple[ValidationIssue, ...], BeforeValidator(tuple_input)] = Field(default=())

    def retained_subset(self, task_ids: tuple[str, ...]) -> "ReuseFacts":
        """按已组装的保留集合投影全部来源事实，不改写原始正式基线。"""

        selected = set(task_ids)
        known = {task for tasks in self.retained_task_ids_by_unit.values() for task in tasks}
        if not selected <= known:
            raise ValueError("保留集合包含不属于正式基线的任务。")
        return ReuseFacts(
            retained_task_ids_by_unit={
                unit: [task for task in tasks if task in selected]
                for unit, tasks in self.retained_task_ids_by_unit.items()
            },
            reusable_capabilities_by_unit={
                unit: {capability: [task for task in tasks if task in selected]
                       for capability, tasks in capabilities.items() if selected.intersection(tasks)}
                for unit, capabilities in self.reusable_capabilities_by_unit.items()
            },
            retained_endpoint_owners=tuple(owner for owner in self.retained_endpoint_owners
                                           if owner.owner_task_id in selected),
            external_capabilities=self.external_capabilities,
            issues=self.issues,
        )
