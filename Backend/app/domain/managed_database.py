"""工作区保存的 SaaS 注册事实，不包含服务密钥和数据库连接信息。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManagedDatabaseRegistration(BaseModel):
    """区分注册成功、在途、明确失败与未知，阻止未知请求被再次创建。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)

    status: Literal["REGISTERING", "REGISTERED", "FAILED", "UNKNOWN"]
    datasource_key: str | None = Field(default=None, alias="datasourceKey", pattern=r"^[0-9a-f]{12}$")
    datasource_id: int | None = Field(default=None, alias="datasourceId", gt=0, le=9007199254740991)

    @model_validator(mode="after")
    def validate_binding(self) -> "ManagedDatabaseRegistration":
        """成功状态必须包含服务端完整绑定，其余状态不得伪造部分身份。"""

        has_binding = self.datasource_key is not None or self.datasource_id is not None
        if self.status == "REGISTERED":
            if self.datasource_key is None or self.datasource_id is None:
                raise ValueError("注册成功必须保存 datasourceKey 和 datasourceId。")
        elif has_binding:
            raise ValueError("未确认注册成功时不能保存应用绑定。")
        return self
