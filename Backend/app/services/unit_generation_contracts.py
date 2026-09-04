"""Unit generation 领域 DTO；只定义数据边界，不构建上下文或执行生成、校验及重试。"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import (
    AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, JsonValue,
    PlainSerializer, StringConstraints,
)

from app.domain.models import BuildUnitKind
from app.services.planning_issues import ValidationIssue


def _plain_json(value: Any) -> Any:
    """将只读对象和元组投影为独立 JSON 容器，用于验证及序列化。"""

    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _freeze_json(value: Any) -> Any:
    """递归复制并冻结 JSON 容器，避免调用方或并发 Attempt 改写业务快照。"""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _tuple_input(value: Any) -> Any:
    """接受 JSON 数组作为不可变序列输入，不转换字符串或任意迭代对象。"""

    return tuple(value) if isinstance(value, list) else value


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
_PositiveInt = Annotated[int, Field(gt=0)]
_PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_FrozenJsonObject = Annotated[
    Mapping[str, JsonValue], BeforeValidator(_plain_json), AfterValidator(_freeze_json),
    PlainSerializer(_plain_json, return_type=dict[str, JsonValue]),
]
_ReadLimits = Annotated[
    Mapping[_Identifier, _PositiveInt], BeforeValidator(_plain_json), AfterValidator(_freeze_json),
    PlainSerializer(_plain_json, return_type=dict[str, int]),
]


class _GenerationModel(BaseModel):
    """拒绝未知字段和隐式标量转换；字段更新必须构造并验证新的 DTO。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """复制时始终重建独立容器并校验，防止默认未验证 update 绕过字段边界。"""

        del deep  # 冻结快照始终使用独立 JSON 容器重建，不共享可变输入。
        return type(self).model_validate({**self.model_dump(mode="json"), **(update or {})})


class GenerationRequirement(_GenerationModel):
    """描述本 Unit 本轮需要新增的职责；不计算 ReuseFacts 或缺项。

    当前最小形状为显式 requirement_id、description 和只读 source_refs。
    source_refs 保存规则层提供的来源信息，不读取或推断正式合同。
    """

    requirement_id: _Identifier
    description: _Identifier
    source_refs: _FrozenJsonObject = Field(default_factory=dict, validate_default=True)


class UnitGenerationContext(_GenerationModel):
    """冻结的业务输入；所有业务区段必须显式提供，空 confirmed 基线用 None 表示。

    formal_contracts 可携带 inline_slices/frozen_catalog_refs，其他区段保存工作区、
    依赖和约束快照。仅冻结已有数据，不读取文件、不计算 fingerprint/digest。
    retry counter、timeout 和 token budget 均不是本模型字段。
    """

    planning_run_id: _Identifier
    unit_id: _Identifier
    unit_kind: BuildUnitKind
    build_execution_scope: _FrozenJsonObject
    input_fingerprint: _Identifier
    base_confirmed_plan_digest: _Identifier | None
    generation_requirements: Annotated[tuple[GenerationRequirement, ...], BeforeValidator(_tuple_input)]
    formal_contracts: _FrozenJsonObject
    workspace_context: _FrozenJsonObject
    dependency_context: _FrozenJsonObject
    constraints: _FrozenJsonObject


class UnitGenerationPolicy(_GenerationModel):
    """独立运行策略，时间单位为秒；保护参数由调用方显式提供，不读取 Settings。

    Local=3、SDK retry=0、token budget=4096 遵循设计基线。
    session timeout、turn limit、read limits 的生产默认值留待实现和压测确定。
    read limits 仅容纳具名正整数预算，不承载合同正文。
    """

    local_max_attempts: Annotated[int, Field(ge=3, le=3)] = 3
    model_max_retries: Annotated[int, Field(ge=0, le=0)] = 0
    model_max_tokens: _PositiveInt = 4096
    request_timeout: _PositiveSeconds
    unit_session_timeout: _PositiveSeconds
    model_turn_limit: _PositiveInt
    frozen_contract_read_limits: _ReadLimits


def _new_candidate_id() -> str:
    """在平台创建 Attempt 时分配独立身份，绝不从模型 Task ID 推导。"""

    return f"candidate-{uuid4().hex}"


class CandidateAttempt(_GenerationModel):
    """平台封装的候选记录，status 必须由调用方明确指定，不自动判定 valid。

    candidate_id 默认由平台生成；反序列化可恢复原 ID。后续模型响应适配器只能
    提交 tasks，不得把模型输出直接展开为本 DTO 的平台元数据。
    tasks 保留原始任务正文，包括非法或缺失 Task ID，供后续 Validator 报错。
    validation_issues 复用 T1.1 契约，不在此实现归因或状态转换。
    """

    candidate_id: Annotated[str, StringConstraints(pattern=r"^candidate-[0-9a-f]{32}$")] = Field(default_factory=_new_candidate_id)
    planning_run_id: _Identifier
    unit_id: _Identifier
    generation_round: _PositiveInt
    attempt_in_round: _PositiveInt
    input_fingerprint: _Identifier
    status: Literal["valid", "invalid", "superseded"]
    tasks: Annotated[tuple[_FrozenJsonObject, ...], BeforeValidator(_tuple_input)]
    validation_issues: Annotated[tuple[ValidationIssue, ...], BeforeValidator(_tuple_input)] = ()
    generation_metadata: _FrozenJsonObject = Field(default_factory=dict, validate_default=True)


class UnitGenerationAttemptResult(_GenerationModel):
    """单次生成的未判定结果，不携带 Candidate status，也不自动生成 Candidate ID。

    保留原始响应和解析出的任务、结构化问题及调用元数据；是否成为 valid Candidate
    由后续平台 Validator 决定。这里只定义 DTO，不实现解析器或 LLM 调用。
    """

    planning_run_id: _Identifier
    unit_id: _Identifier
    input_fingerprint: _Identifier
    raw_response: str
    tasks: Annotated[tuple[_FrozenJsonObject, ...], BeforeValidator(_tuple_input)]
    validation_issues: Annotated[tuple[ValidationIssue, ...], BeforeValidator(_tuple_input)] = ()
    generation_metadata: _FrozenJsonObject = Field(default_factory=dict, validate_default=True)
