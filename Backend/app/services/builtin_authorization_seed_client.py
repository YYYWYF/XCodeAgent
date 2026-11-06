"""适配独立 SaaS Seed 接口，保留原建表客户端合同。"""

import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.builtin_authorization_seed import MAX_ADMINS, MAX_RESOURCES, canonical_json
from app.services.managed_database_client import ManagedDatabaseClient

SeedStatus = Literal["RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN"]


class SeedRemoteError(ValueError):
    """用受控类别表示 SaaS 或传输失败，丢弃外部正文。"""

    def __init__(self, category: str):
        """仅保留安全错误类别。"""

        super().__init__("SaaS 权限 Seed 操作结果未确认。")
        self.category = category


class SeedReceipt(BaseModel):
    """严格的 202 回执，只用于保存原操作编号。"""

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


class ManagedAuthorizationSeedClient(ManagedDatabaseClient):
    """复用受控地址、Bearer 和超时，单次请求且禁止跟随重定向。"""

    def submit(self, key: str, payload: dict) -> SeedReceipt:
        """只发送一次 POST，202 仅是受理事实。"""

        body = self._request(key, "batch", payload=payload)
        try:
            return SeedReceipt.model_validate(body)
        except ValidationError:
            raise SeedRemoteError("seed_invalid_response") from None

    def query(self, key: str, *, operation_id: str | None = None, idempotency_key: str) -> SeedDetail | None:
        """按唯一原 ID 或原幂等键 GET 一次，仅明确的按键 404 返回空。"""

        if not re.fullmatch(r"[0-9a-f]{64}", idempotency_key):
            raise SeedRemoteError("seed_invalid_identity")
        if operation_id is not None and not re.fullmatch(r"[1-9][0-9]{0,18}", operation_id):
            raise SeedRemoteError("seed_invalid_identity")
        body = self._request(key, operation_id or f"by-key/{idempotency_key}", allow_missing=operation_id is None)
        if body is None:
            return None
        try:
            return SeedDetail.model_validate(body)
        except ValidationError:
            raise SeedRemoteError("seed_invalid_response") from None

    def _request(self, key: str, path: str, *, payload: dict | None = None, allow_missing: bool = False) -> dict | None:
        """解析 SaaS JSON 包装并脱敏网络、HTTP 和解析错误。"""

        if not re.fullmatch(r"[0-9a-f]{12}", key):
            raise SeedRemoteError("seed_invalid_identity")
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout,
                              follow_redirects=False, trust_env=False) as client:
                response = client.request("GET" if payload is None else "POST",
                                          f"{self._url}/{key}/data-operations/{path}", headers=headers,
                                          content=canonical_json(payload) if payload is not None else None)
        except httpx.TimeoutException:
            raise SeedRemoteError("seed_timeout") from None
        except httpx.RequestError:
            raise SeedRemoteError("seed_network_error") from None
        expected = 200 if payload is None else 202
        if response.status_code != expected and not (allow_missing and response.status_code == 404):
            raise SeedRemoteError(f"seed_http_{response.status_code}")
        try:
            envelope = response.json()
        except ValueError:
            raise SeedRemoteError("seed_invalid_response") from None
        if (not isinstance(envelope, dict) or set(envelope) != {"returnCode", "errorMsg", "body"}
                or envelope["errorMsg"] is not None and not isinstance(envelope["errorMsg"], str)):
            raise SeedRemoteError("seed_invalid_response")
        if response.status_code == 404:
            if envelope["returnCode"] == "RESOURCE_NOT_FOUND" and envelope["body"] == {}:
                return None
            raise SeedRemoteError("seed_http_404")
        if envelope["returnCode"] != "SUC0000" or envelope["errorMsg"] is not None or not isinstance(envelope["body"], dict):
            raise SeedRemoteError("seed_invalid_response")
        return envelope["body"]
