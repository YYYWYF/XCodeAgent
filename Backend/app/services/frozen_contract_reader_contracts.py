"""Frozen Contract Fragment Reader 的冻结输出、策略与错误契约。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from app.services.planning_frozen import FrozenJsonObject, FrozenPlanningModel


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_PositiveInt = Annotated[int, Field(gt=0)]
ContractReadErrorCode = Literal[
    "FROZEN_CONTRACT_READER_INPUT_INVALID",
    "FROZEN_CONTRACT_REF_UNAUTHORIZED",
    "FROZEN_CONTRACT_SELECTOR_INVALID",
    "FROZEN_CONTRACT_SELECTOR_UNKNOWN",
    "FROZEN_CONTRACT_CURSOR_INVALID",
    "FROZEN_CONTRACT_READ_COUNT_EXCEEDED",
    "FROZEN_CONTRACT_ACCUMULATED_SIZE_EXCEEDED",
]


class FrozenContractReadPolicy(FrozenPlanningModel):
    """由调用方显式提供的读取次数、累计字节和单页字节保护预算。"""

    max_reads: _PositiveInt
    max_total_bytes: _PositiveInt
    max_bytes_per_read: _PositiveInt

    @model_validator(mode="after")
    def validate_page_budget(self) -> "FrozenContractReadPolicy":
        """单次页大小不得大于会话累计上限，避免首次读取必然失败。"""

        if self.max_bytes_per_read > self.max_total_bytes:
            raise ValueError("max_bytes_per_read 不能大于 max_total_bytes。")
        return self


class FrozenContractFragment(FrozenPlanningModel):
    """一次受限读取返回的 JSON 文本页及其继续读取游标。"""

    source_ref: _Identifier = Field(serialization_alias="sourceRef")
    selector: _Identifier
    content: str
    cursor: _Identifier | None
    next_cursor: _Identifier | None = Field(serialization_alias="nextCursor")
    complete: bool


class FrozenContractReadFailure(FrozenPlanningModel):
    """可安全投影给上层协议的结构化读取失败。"""

    code: ContractReadErrorCode
    message: _Identifier
    details: FrozenJsonObject


class FrozenContractReadError(ValueError):
    """携带稳定错误码和冻结详情的 Fragment Reader 失败。"""

    def __init__(
        self,
        code: ContractReadErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """构造结构化失败，并让异常文本保持简洁可诊断。"""

        self.failure = FrozenContractReadFailure(
            code=code,
            message=message,
            details=details or {},
        )
        super().__init__(message)

    @property
    def code(self) -> ContractReadErrorCode:
        """直接暴露稳定错误码，避免调用方解析异常文本。"""

        return self.failure.code

    @property
    def details(self) -> FrozenJsonObject:
        """返回递归只读的结构化错误详情。"""

        return self.failure.details

    def as_dict(self) -> dict[str, Any]:
        """生成适合协议层投影的独立 JSON 错误对象。"""

        return self.failure.model_dump(mode="json")


def read_error(
    code: ContractReadErrorCode,
    message: str,
    **details: Any,
) -> FrozenContractReadError:
    """统一创建 Reader 结构化异常，避免各分支使用自由文本契约。"""

    return FrozenContractReadError(code, message, details=details)
