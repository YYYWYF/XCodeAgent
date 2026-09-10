"""本轮新增职责的冻结结果及结构化前置失败，不创建 Candidate 或运行状态。"""

from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, PlainSerializer, StringConstraints, model_validator

from app.services.planning_frozen import FrozenPlanningModel, freeze_json, plain_json, tuple_input
from app.services.planning_issues import IssueCategory, ValidationIssue
from app.services.unit_generation_contracts import GenerationRequirement


GenerationStrategy = Literal["structural_only", "prerequisite_only", "reuse_only", "deterministic", "model"]
_Id = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
_Requirements = Annotated[tuple[GenerationRequirement, ...], BeforeValidator(tuple_input)]
_RequirementsByUnit = Annotated[
    Mapping[_Id, _Requirements], BeforeValidator(plain_json), AfterValidator(freeze_json),
    PlainSerializer(plain_json, return_type=dict),
]
_StrategiesByUnit = Annotated[
    Mapping[_Id, GenerationStrategy], BeforeValidator(plain_json), AfterValidator(freeze_json),
    PlainSerializer(plain_json, return_type=dict),
]


class UnitGenerationRequirements(FrozenPlanningModel):
    """只表达 required Units 的新增职责和策略；无缺项的 Unit 不进入 planning。"""

    generation_requirements_by_unit: _RequirementsByUnit
    planning_unit_ids: Annotated[tuple[_Id, ...], BeforeValidator(tuple_input)]
    generation_strategy_by_unit: _StrategiesByUnit

    @model_validator(mode="after")
    def validate_planning_units(self) -> "UnitGenerationRequirements":
        """保证需求、策略和 planning 集合一致，禁止空任务或结构节点进入生成。"""

        if self.generation_requirements_by_unit.keys() != self.generation_strategy_by_unit.keys():
            raise ValueError("需求表与策略表必须覆盖同一组 required Units。")
        expected = []
        for unit, requirements in self.generation_requirements_by_unit.items():
            strategy = self.generation_strategy_by_unit[unit]
            if unit in {"application:root", "app:integration"} and strategy != "structural_only":
                raise ValueError("Structural Unit 必须为 structural_only。")
            if unit == "frontend:shell" and strategy != "prerequisite_only":
                raise ValueError("frontend:shell 必须为 prerequisite_only。")
            if requirements:
                if strategy not in {"model", "deterministic"}:
                    raise ValueError("只有 model/deterministic Unit 可以携带新增职责。")
                expected.append(unit)
            if len({item.requirement_id for item in requirements}) != len(requirements):
                raise ValueError("同一 Unit 不得含重复 requirement_id。")
        if self.planning_unit_ids != tuple(sorted(expected)):
            raise ValueError("planning_unit_ids 必须恰好包含有新增职责的生成 Unit，并按 ID 排序。")
        return self


class GenerationRequirementsError(ValueError):
    """携带前置输入或平台基线问题；失败时不返回可误用的部分 planning 结果。"""

    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        """保存经过契约校验的不可变问题集合，供后续 AG-UI 边界原样投影。"""

        self.issues = tuple(ValidationIssue.model_validate(issue) for issue in issues)
        super().__init__("；".join(issue.message for issue in self.issues))


def fail_requirement_input(
    code: str, message: str, *, unit_ids: Sequence[str] = (), category: IssueCategory = "input",
) -> None:
    """在确定性规则命中处直接产生 T1.1 问题，不把输入失败交给模型重试。"""

    raise GenerationRequirementsError([ValidationIssue(
        code=code, level="pre_generation", category=category, unit_ids=tuple(unit_ids),
        task_ids=(), retryable=False, retry_unit_ids=(), message=message,
    )])
