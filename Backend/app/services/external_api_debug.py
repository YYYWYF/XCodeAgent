"""针对已登记外部接口执行一次真实请求。"""

import asyncio
import logging
import re
import ssl
from contextvars import ContextVar
from time import monotonic
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.services.data_sources import ExternalApiSourcePublic, public_catalog

LIMIT = 512 * 1024
_debugging = ContextVar("external_api_debugging", default=False)


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        """调试调用的 URL 和协议帧可能含有凭据，禁止进入 HTTP 库日志。"""
        return not _debugging.get()


for _name in ("httpx", "httpcore.http11", "httpcore.http2", "httpcore.connection",
              "httpcore.proxy", "httpcore.socks", "httpcore.connection_pool"):
    logging.getLogger(_name).addFilter(_PrivateRequestFilter())


class DebugInput(BaseModel):
    """校验请求配置，禁止自动回显校验失败的输入。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    sourceId: str = Field(min_length=1, max_length=256)
    operationId: str = Field(min_length=1, max_length=256)
    expectedBase: str
    expectedMethod: str
    expectedPath: str
    requestMethod: Literal['GET', 'POST', 'PUT', 'DELETE']
    requestPath: str = Field(min_length=1, max_length=2048)
    pathValues: dict[str, str] = Field(max_length=64)
    query: dict[str, str] = Field(max_length=100)
    headers: dict[str, str] = Field(max_length=100)
    body: str | None = Field(default=None, max_length=262144)
    timeoutMs: int = Field(ge=100, le=120000)


def _prepare(data: DebugInput) -> tuple[str, str, dict[str, str]]:
    """从目录重新确定目标；拒绝过期目标和可能改变请求目的地的覆盖项。"""
    catalog = public_catalog(data.workspaceRoot, source_id=data.sourceId, operation_id=data.operationId)
    source = next((s for s in catalog.sources if isinstance(s, ExternalApiSourcePublic) and s.id == data.sourceId), None)
    if source is None:
        raise ValueError("接口域已移除，请重新打开接口。")
    op = next((item for group in source.directories for item in group.operations if item.id == data.operationId), None)
    if not op or (source.base_url, op.method, op.path) != (data.expectedBase, data.expectedMethod, data.expectedPath):
        raise ValueError("接口地址或方法已变更，请重新打开接口。")
    base = urlsplit(source.base_url)
    if base.scheme not in {"http", "https"} or not base.hostname or base.username is not None or base.query or base.fragment:
        raise ValueError("接口域须使用有效的 HTTP 或 HTTPS 基础地址。")
    template = data.requestPath
    decoded = unquote(template)
    remainder = re.sub(r"\{[^{}\s/]+\}", "", template)
    if (not template.startswith('/') or decoded.startswith('//')
            or any(ch in decoded for ch in '?#\\') or any(ord(ch) < 32 for ch in decoded)
            or any(segment in {'.', '..'} for segment in decoded.split('/'))
            or any(ch in remainder for ch in '{}')):
        raise ValueError("调试路径必须是当前接口域下的有效相对路径。")

    def substitute(match: re.Match[str]) -> str:
        """编码单个路径段，确保参数不能改变路由层级。"""
        value = data.pathValues.get(match[1], "")
        if value in {"", ".", ".."}:
            raise ValueError("请填写有效的路径参数。")
        return quote(value, safe="")
    path = re.sub(r"\{([^{}\/]+)\}", substitute, template)
    # 前端发送启用后的完整 Header 集合，允许关闭来源中的共享 Header。
    try:
        headers = httpx.Headers(data.headers)
    except (UnicodeError, ValueError):
        raise ValueError("Header 名称或值编码无效。") from None
    if any(key in headers for key in ("host", "content-length", "transfer-encoding", "connection", "proxy-authorization", "upgrade")):
        raise ValueError("传输控制 Header 由请求客户端管理。")
    headers["accept-encoding"] = "identity"
    return data.requestMethod, source.base_url.rstrip("/") + "/" + path.lstrip("/"), dict(headers)


async def send_debug_request(data: DebugInput) -> dict:
    """执行有总时限的一次请求，限制响应读取量，支持流取消。"""
    method, url, headers = await asyncio.to_thread(_prepare, data)
    started = monotonic()
    marker = _debugging.set(True)
    try:
        async with asyncio.timeout(data.timeoutMs / 1000), httpx.AsyncClient(follow_redirects=False) as client:
            async with client.stream(method, url, params=data.query, headers=headers,
                                     content=data.body.encode() if data.body is not None else None,
                                     timeout=data.timeoutMs / 1000) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes(16384):
                    body.extend(chunk[:LIMIT + 1 - len(body)])
                    if len(body) > LIMIT:
                        break
                return {"statusCode": response.status_code, "elapsedMs": round((monotonic() - started) * 1000),
                        "bytes": min(len(body), LIMIT), "truncated": len(body) > LIMIT,
                        "headers": dict(response.headers),
                        "body": body[:LIMIT].decode(response.encoding or "utf-8", errors="replace")}
    except (httpx.TimeoutException, TimeoutError):
        raise ValueError("请求已超时。") from None
    except httpx.ConnectError as exc:
        if "CERTIFICATE" in str(exc).upper() or isinstance(exc.__cause__, ssl.SSLError):
            raise ValueError("TLS 证书校验失败。") from None
        raise ValueError("无法连接外部服务，请检查网络和目标地址。") from None
    except (httpx.HTTPError, UnicodeError, LookupError, ValueError):
        raise ValueError("外部请求或响应编码无效。") from None
    finally:
        _debugging.reset(marker)
