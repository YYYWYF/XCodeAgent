"""接口参数、固定值及声明式业务规则的统一取值契约。"""

from typing import Annotated, Any, Literal
from pydantic import Field, model_validator
from app.domain.api_design_fields import ApiDesignModel, EndpointField


class EndpointQueryRight(ApiDesignModel):
    """引用当前 Endpoint 的请求参数作为查询右值。"""

    kind: Literal["endpoint"] = "endpoint"
    endpoint_field: EndpointField = Field(alias="endpointField")

    @model_validator(mode="after")
    def validate_request_side(self) -> "EndpointQueryRight":
        """拒绝把响应字段用作查询参数。"""

        if self.endpoint_field.side != "request":
            raise ValueError("数据库右值只能引用当前 Endpoint 的请求参数。")
        return self


class FixedQueryRight(ApiDesignModel):
    """保存查询条件的参数化固定值。"""

    kind: Literal["fixed"] = "fixed"
    value: Any


class BusinessQueryRight(ApiDesignModel):
    """保存依赖清单和待代码生成实现的业务取值规则，不执行表达式。"""

    kind: Literal["business"] = "business"
    origin: Literal["endpoint", "builtin", "business"] = "business"
    endpoint_fields: list[EndpointField] = Field(default_factory=list, alias="endpointFields", max_length=100)
    builtin_fields: list[Literal["current_user_id", "current_time"]] = Field(default_factory=list, alias="builtinFields", max_length=2)
    business_description: str = Field(alias="businessDescription", min_length=1, max_length=2000)
    missing_behavior: Literal["error", "omit", "default"] = Field(default="error", alias="missingBehavior")
    default_value: Any = Field(default=None, alias="defaultValue")

    @model_validator(mode="after")
    def validate_rule(self) -> "BusinessQueryRight":
        """拒绝未说明规则、响应依赖、重复依赖和缺失默认值。"""
        if not self.business_description.strip():
            raise ValueError("业务处理内容不能为空。")
        if any(field.side != "request" for field in self.endpoint_fields):
            raise ValueError("取值规则只能引用调用前可用的接口请求参数。")
        identities = [(field.location, field.path) for field in self.endpoint_fields]
        if len(set(identities)) != len(identities) or len(set(self.builtin_fields)) != len(self.builtin_fields):
            raise ValueError("业务规则不能包含重复依赖。")
        if self.origin == "endpoint" and not self.endpoint_fields:
            raise ValueError("接口参数业务处理必须选择依赖参数。")
        if self.origin == "builtin" and not self.builtin_fields:
            raise ValueError("内置参数业务处理必须选择内置依赖。")
        if self.missing_behavior == "default" and self.default_value is None:
            raise ValueError("缺值行为为默认值时必须填写默认值。")
        if self.missing_behavior != "default" and self.default_value is not None:
            raise ValueError("非默认值策略不能携带默认值。")
        return self


QueryRight = Annotated[EndpointQueryRight | FixedQueryRight | BusinessQueryRight, Field(discriminator="kind")]


