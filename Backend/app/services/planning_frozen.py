"""Planning DTO 共用的递归只读 JSON 与验证复制边界。"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Self

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, JsonValue, PlainSerializer


def plain_json(value: Any) -> Any:
    """将只读对象和元组投影为独立 JSON 容器，用于验证及序列化。"""

    if isinstance(value, Mapping):
        return {key: plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_json(item) for item in value]
    return value


def freeze_json(value: Any) -> Any:
    """递归复制并冻结 JSON 容器，防止外部输入修改快照。"""

    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    return value


def tuple_input(value: Any) -> Any:
    """接受 JSON 数组作为不可变序列输入，不转换其他类型。"""

    return tuple(value) if isinstance(value, list) else value


FrozenJsonObject = Annotated[
    Mapping[str, JsonValue], BeforeValidator(plain_json), AfterValidator(freeze_json),
    PlainSerializer(plain_json, return_type=dict[str, JsonValue]),
]


class FrozenPlanningModel(BaseModel):
    """拒绝未知字段及隐式标量转换；复制更新也必须重新验证。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """始终重建独立容器并校验，防止未验证 update 绕过契约。"""

        del deep
        return type(self).model_validate({**self.model_dump(mode="json"), **(update or {})})
