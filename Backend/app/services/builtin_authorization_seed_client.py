"""校验 SaaS Seed 受理回执、原操作详情及响应分类。"""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.services.builtin_authorization_seed import MAX_ADMINS, MAX_RESOURCES
from app.services.managed_database_bee import unwrap_bee_envelope

SeedStatus = Literal["RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN"]


class SeedRemoteError(ValueError):
    """用受控类别表示 SaaS 或传输失败，丢弃外部正文。"""

    def __init__(self, category: str):
        """仅保留安全错误类别。"""

        super().__init__("SaaS 权限 Seed 操作结果未确认。")
        self.category = category


class SeedReceipt(BaseModel):
    """严格的受理回执，只用于保存原操作编号。"""

    model_config = ConfigDict(strict=True, extra="forbid")
    operationId: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    purpose: Literal["AUTHORIZATION_SEED_V1"]
    status: SeedStatus


class SeedDetail(SeedReceipt):
    """原操作的安全摘要，禁止使用回执代替成功核验。"""

    datasourceKey: str = Field(pattern=r"^[0-9a-f]{12}$")
    requestFingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tableNames: list[str] = Field(min_length=4, max_length=4)
    createdCount: int = Field(ge=0, le=MAX_RESOURCES + MAX_ADMINS + 2)
    existingCount: int = Field(ge=0, le=MAX_RESOURCES + MAX_ADMINS + 2)
    verified: bool
    errorCode: str | None = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")


def decode_seed_response(response: httpx.Response, *, submitting: bool, allow_missing: bool) -> dict | None:
    """解析 Seed 的 Bee 包装及精确的 409/404 结果。"""

    submit_conflict = submitting and response.status_code == 409
    if response.status_code != 200 and not (submit_conflict or allow_missing and response.status_code == 404):
        raise SeedRemoteError(f"seed_http_{response.status_code}")
    invalid_envelope = "seed_http_409" if submit_conflict else "seed_invalid_response"
    try:
        envelope = response.json()
    except ValueError:
        raise SeedRemoteError(invalid_envelope) from None
    if (not isinstance(envelope, dict) or set(envelope) != {"returnCode", "errorMsg", "body"}
            or not isinstance(envelope["returnCode"], str)
            or envelope["errorMsg"] is not None and not isinstance(envelope["errorMsg"], str)):
        raise SeedRemoteError(invalid_envelope)
    if submit_conflict:
        # 资源占用可退避，幂等键冲突必须停止；其他 409 保留受控 HTTP 分类。
        categories = {"RESOURCE_BUSY": "seed_resource_busy", "IDEMPOTENCY_CONFLICT": "seed_idempotency_conflict"}
        raise SeedRemoteError(categories.get(envelope["returnCode"], "seed_http_409"))
    if response.status_code == 404:
        # 仅识别 SaaS 明确的记录不存在；路由错误和未知错误体不能授权提交。
        if envelope["returnCode"] == "RESOURCE_NOT_FOUND" and (envelope["body"] is None or envelope["body"] == {}):
            return None
        raise SeedRemoteError("seed_http_404")
    try:
        body = unwrap_bee_envelope(envelope)
    except ValueError:
        raise SeedRemoteError("seed_invalid_response") from None
    if not isinstance(body, dict):
        raise SeedRemoteError("seed_invalid_response")
    return body
