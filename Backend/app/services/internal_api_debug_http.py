"""内部接口调试的单次 HTTP 执行与输入边界。"""

import asyncio
import logging
import re
import ssl
from contextvars import ContextVar
from time import monotonic
from urllib.parse import quote, unquote

import httpx
from pydantic import BaseModel, ConfigDict, Field


RESPONSE_LIMIT = 512 * 1024
PATH_PARAMETER = re.compile(r"\{([^{}\s/]+)\}")
_debugging = ContextVar("internal_api_debugging", default=False)


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        """调试调用的协议帧可能含有凭据，禁止写进 HTTP 库日志。"""
        return not _debugging.get()


for _name in ("httpx", "httpcore.http11", "httpcore.http2", "httpcore.connection",
              "httpcore.proxy", "httpcore.socks", "httpcore.connection_pool"):
    logging.getLogger(_name).addFilter(_PrivateRequestFilter())


class HttpDebugRequest(BaseModel):
    """内部调试使用的参数、正文大小和超时约束。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    pathValues: dict[str, str] = Field(max_length=64)
    query: dict[str, str] = Field(max_length=100)
    headers: dict[str, str] = Field(max_length=100)
    body: str | None = Field(default=None, max_length=262144)
    timeoutMs: int = Field(ge=100, le=120000)


def expand_debug_path(template: str, values: dict[str, str]) -> str:
    """生成相对路径，将占位符的值限制为对应路径段。"""
    decoded = unquote(template)
    remainder = PATH_PARAMETER.sub("", template)
    if (not template.startswith("/") or decoded.startswith("//")
            or any(ch in decoded for ch in "?#\\") or any(ord(ch) < 32 for ch in decoded)
            or any(segment in {".", ".."} for segment in decoded.split("/"))
            or any(ch in remainder for ch in "{}")):
        raise ValueError("调试路径必须是当前接口域下的有效相对路径。")

    def substitute(match: re.Match[str]) -> str:
        """将路径参数编码为一个路径段，内部路由沿用单段参数约束。"""
        value = values.get(match[1], "")
        if value in {"", ".", ".."} or any(ch in value for ch in "/\\"):
            raise ValueError("请填写有效的路径参数。")
        return quote(value, safe="")

    return PATH_PARAMETER.sub(substitute, template)


def debug_headers(values: dict[str, str]) -> dict[str, str]:
    """构造 HTTP Header，传输与服务定位由调试客户端控制。"""
    try:
        headers = httpx.Headers(values)
    except (UnicodeError, ValueError):
        raise ValueError("Header 名称或值编码无效。") from None
    if any(key in headers for key in ("host", "content-length", "transfer-encoding", "connection", "proxy-authorization", "upgrade")):
        raise ValueError("传输控制 Header 由请求客户端管理。")
    headers["accept-encoding"] = "identity"
    return dict(headers)


async def execute_debug_http(method: str, url: str, request: HttpDebugRequest) -> dict:
    """实际发送一次请求，保留 HTTP 业务状态并限制响应读取量。"""
    headers = debug_headers(request.headers)
    started = monotonic()
    marker = _debugging.set(True)
    try:
        async with asyncio.timeout(request.timeoutMs / 1000), httpx.AsyncClient(
            follow_redirects=False, trust_env=False,
        ) as client:
            async with client.stream(method, url, params=request.query, headers=headers,
                                     content=request.body.encode() if request.body is not None else None,
                                     timeout=request.timeoutMs / 1000) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes(16384):
                    body.extend(chunk[:RESPONSE_LIMIT + 1 - len(body)])
                    if len(body) > RESPONSE_LIMIT:
                        break
                return {"statusCode": response.status_code, "elapsedMs": round((monotonic() - started) * 1000),
                        "bytes": min(len(body), RESPONSE_LIMIT), "truncated": len(body) > RESPONSE_LIMIT,
                        "headers": dict(response.headers),
                        "body": body[:RESPONSE_LIMIT].decode(response.encoding or "utf-8", errors="replace")}
    except (httpx.TimeoutException, TimeoutError):
        raise ValueError("请求已超时。") from None
    except httpx.ConnectError as exc:
        if "CERTIFICATE" in str(exc).upper() or isinstance(exc.__cause__, ssl.SSLError):
            raise ValueError("TLS 证书校验失败。") from None
        raise ValueError("无法连接当前应用后端，请检查服务状态。") from None
    except (httpx.HTTPError, UnicodeError, LookupError, ValueError):
        raise ValueError("请求或响应编码无效。") from None
    finally:
        _debugging.reset(marker)
