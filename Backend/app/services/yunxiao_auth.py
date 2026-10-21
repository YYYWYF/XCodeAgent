"""云效桌面认证业务；使用异步 HTTP，不缓存用户 token。"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlencode, urlsplit, unquote
import httpx
from pydantic import BaseModel, Field, ValidationError
from app.services.auth_credentials import AuthCredentials, CredentialStore


class StateData(BaseModel):
    """校验 state 响应。"""
    state: str = Field(min_length=1, max_length=2048)


class TokenData(BaseModel):
    """沿 demo 的协议解释有效期为 Unix 秒级时间戳。"""
    token: str = Field(min_length=1)
    expiresAt: int = Field(gt=0)
    refreshToken: str = ""
    refreshExpiresAt: int = 0
    tokenType: Literal["Bearer", "bearer"] = "Bearer"
    ystIdToken: str = ""
    ystRefreshToken: str = ""


class UserData(BaseModel):
    """校验认证用户的公开标识和名称。"""
    id: str = Field(min_length=1)
    nickname: str = Field(min_length=1)


class AuthService:
    """串行管理登录任务；内存只保留任务身份和取消信号。"""
    def __init__(self, store: CredentialStore, transport: httpx.AsyncBaseTransport | None = None):
        """初始化文件存储和可注入的 HTTP 传输。"""
        self.store = store
        self.transport = transport
        self.lock = asyncio.Lock()
        self.attempt: asyncio.Event | None = None

    async def reset(self, session_id: str, *, initialize: bool = False, shutdown: bool = False) -> dict:
        """取消并隔离旧登录任务，再初始化、清理或结束桌面会话。"""
        async with self.lock:
            if not initialize:
                self.store.require_session(session_id)
            if self.attempt:
                self.attempt.set()
                self.attempt = None
            if initialize:
                self.store.initialize(session_id)
            elif shutdown:
                self.store.invalidate(session_id)
            else:
                self.store.clear()
            return {"authenticated": False, "workingDir": self.store.working_dir}

    def status(self, session_id: str) -> dict:
        """公开登录状态，不把 token 暴露到 AG-UI。"""
        credentials = self.store.read(session_id)
        result: dict[str, Any] = {"authenticated": credentials is not None, "workingDir": self.store.working_dir}
        if credentials:
            result.update(user={"id": credentials.user_id, "name": credentials.user_name}, expiresAt=credentials.expires_at_ms)
        return result

    def _endpoint(self) -> str:
        """沿 demo 使用配置的 HTTP 或 HTTPS 认证地址。"""
        endpoint = os.getenv("YUNXIAO_ENDPOINT", "").strip().rstrip("/")
        parsed = urlsplit(endpoint)
        if not endpoint:
            raise RuntimeError("请在 Backend/.env 配置 YUNXIAO_ENDPOINT 后重启 Python 后端。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise RuntimeError("YUNXIAO_ENDPOINT 必须是无凭据、查询参数和片段的服务地址。")
        if parsed.scheme not in {"http", "https"}:
            raise RuntimeError("YUNXIAO_ENDPOINT 必须使用 HTTP 或 HTTPS。")
        return endpoint

    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        """限制每次请求耗时，并把网络异常转成不含 token 或 state 的错误。"""
        try:
            return await client.get(url, **kwargs)
        except httpx.RequestError:
            raise RuntimeError("云效认证服务连接失败或请求超时，请检查网络后重试。") from None

    def _data(self, response: httpx.Response, stage: str) -> dict:
        """校验 HTTP 和业务信封，不向日志或界面回显上游原始载荷。"""
        if response.status_code != 200:
            raise RuntimeError(f"{stage}失败：HTTP {response.status_code}。")
        try:
            payload = response.json()
            if not isinstance(payload, dict) or type(payload.get("code")) is not int:
                raise ValueError()
            if payload["code"] != 0:
                raise RuntimeError(f"{stage}失败：业务码 {payload['code']}。")
            if not isinstance(payload.get("data"), dict):
                raise ValueError()
            return payload["data"]
        except (ValueError, TypeError):
            raise RuntimeError(f"{stage}返回了无效响应。") from None

    def _check_attempt(self, session_id: str, attempt: asyncio.Event) -> None:
        """在请求间及落盘前检查取消和会话栅栏。"""
        if attempt.is_set() or self.attempt is not attempt:
            raise RuntimeError("登录已取消，请重新发起登录。")
        self.store.require_session(session_id)

    async def login(self, session_id: str, report: Callable[..., Awaitable[None]]) -> dict:
        """完成 state、浏览器授权等待、token 和用户信息的认证闭环。"""
        async with self.lock:
            self.store.require_session(session_id)
            if self.attempt is not None:
                raise RuntimeError("已有登录正在进行，请等待或取消后重试。")
            attempt = asyncio.Event()
            self.attempt = attempt
        try:
            endpoint = self._endpoint()
            timeout = float(os.getenv("YUNXIAO_LOGIN_TIMEOUT_SECONDS", "180"))
            interval = float(os.getenv("YUNXIAO_POLL_INTERVAL_SECONDS", "1"))
            if not 1 <= timeout <= 900 or not 0.1 <= interval <= 10:
                raise RuntimeError("云效登录超时或轮询间隔配置超出允许范围。")
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                    await report("requesting_state", "正在获取云效授权标识", 10)
                    state = StateData.model_validate(self._data(await self._get(client, f"{endpoint}/auth/state", headers={"X-User-Agent": "Cli"}), "获取授权标识")).state
                    self._check_attempt(session_id, attempt)
                    await report("awaiting_authorization", "请在浏览器中完成云效授权", 25, authorizationUrl=f"{endpoint}/auth/authorize?{urlencode({'state': state})}")
                    pending_codes = {int(code.strip()) for code in os.getenv("YUNXIAO_PENDING_CODES", "").split(",") if code.strip()}
                    while True:
                        self._check_attempt(session_id, attempt)
                        response = await self._get(client, f"{endpoint}/auth/token", params={"state": state})
                        pending = response.status_code == 202
                        if response.status_code == 200:
                            try:
                                body = response.json()
                            except ValueError:
                                raise RuntimeError("获取 token 返回了无效响应。") from None
                            pending = isinstance(body, dict) and type(body.get("code")) is int and (body["code"] in pending_codes if pending_codes else body["code"] != 0)
                        if not pending:
                            token = TokenData.model_validate(self._data(response, "获取 token"))
                            break
                        try:
                            await asyncio.wait_for(attempt.wait(), timeout=interval)
                        except TimeoutError:
                            pass
                    self._check_attempt(session_id, attempt)
                    await report("loading_user", "正在读取云效用户信息", 65)
                    user = UserData.model_validate(self._data(await self._get(client, f"{endpoint}/auth/userinfo", headers={"Authorization": f"Bearer {token.token}"}), "获取用户信息"))
                    credentials = AuthCredentials(access_token=token.token, refresh_token=token.refreshToken, token_type="Bearer", expires_at_ms=token.expiresAt * 1000, refresh_expires_at_ms=token.refreshExpiresAt * 1000, user_id=user.id, user_name=user.nickname, yst_id_token=token.ystIdToken, yst_refresh_token=token.ystRefreshToken)
                    if credentials.expires_at_ms <= int(time.time() * 1000) + 30_000:
                        raise RuntimeError("云效 token 已过期或即将过期，请重新登录。")
                    await report("saving_credentials", "正在安全保存本次登录凭据", 90)
                    async with self.lock:
                        self._check_attempt(session_id, attempt)
                        self.store.save(session_id, credentials)
                    return self.status(session_id)
        except TimeoutError:
            raise RuntimeError("等待云效授权超时，请点击登录重试。") from None
        except ValidationError:
            raise RuntimeError("云效认证响应字段不符合 demo 协议。") from None
        finally:
            async with self.lock:
                if self.attempt is attempt:
                    self.attempt = None

    async def cloud_request(self, session_id: str, request: dict) -> dict:
        """在 Python 内读取文件并添加认证头，凭据不经过渲染器。"""
        credentials = self.store.read(session_id)
        if not credentials:
            return {"httpStatus": 401, "body": {"detail": "登录已失效，请重新登录。"}}
        base = urlsplit(os.getenv("YUNXIAO_API_BASE_URL", "").strip() or self._endpoint())
        if base.scheme not in {"http", "https"} or not base.hostname:
            raise RuntimeError("云端业务接口必须配置为 HTTP 或 HTTPS 服务地址。")
        target = urlsplit(request["url"])
        if (target.scheme, target.hostname, target.port) != (base.scheme, base.hostname, base.port) or target.username or target.password or target.fragment:
            raise RuntimeError("云端请求地址不属于已配置的可信服务。")
        if "auth" in unquote(unquote(target.path)).lower().split("/"):
            raise RuntimeError("云端业务请求不能访问认证接口。")
        allowed_headers = {k: v for k, v in request.get("headers", {}).items() if k.lower() in {"content-type", "accept"}}
        allowed_headers["Authorization"] = f"Bearer {credentials.access_token}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, transport=self.transport) as client:
            try:
                response = await client.request(request["method"], request["url"], headers=allowed_headers, content=request.get("body"))
            except httpx.RequestError:
                raise RuntimeError("云端业务接口连接失败。") from None
        if response.status_code == 401:
            # 只清理该请求使用的凭据，不能删除并发重新登录保存的新 token。
            current = self.store.read(session_id)
            if current and current.access_token == credentials.access_token:
                self.store.clear()
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = {"detail": "云端接口未返回 JSON。"}
        return {"httpStatus": response.status_code, "body": body}
