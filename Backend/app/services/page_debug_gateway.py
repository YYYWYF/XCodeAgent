"""页面调试专用的工作区回环入口，转发真实页面及应用请求。"""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress
from functools import cached_property
from pathlib import Path
from ssl import SSLContext
from threading import RLock, Thread
from time import time_ns
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from app.services.backend_runtime_jar import BACKEND_PROCESS_FILENAME, read_backend_process_record
from app.services.preview_runtime_guard import maintenance_owner
from app.services.preview_runtime_state import (
    PreviewLayer, read_record, runtime_root, service_attempt_id, service_in_maintenance,
)
from app.utils.process_liveness import pid_is_running


_lock = RLock()
_gateways: dict[Path, PageDebugGateway] = {}
_HOP_HEADERS = {
    b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
    b"te", b"trailer", b"transfer-encoding", b"upgrade",
}


def _origin(value: str) -> str:
    """仅接受启动器登记的本机 HTTP(S) 地址，禁止协议和主机来自页面请求。"""
    try:
        url = urlsplit(value)
        if (url.scheme in {"http", "https"} and url.hostname in {"127.0.0.1", "localhost", "::1"}
                and not (url.username or url.password) and url.port != 0):
            return f"{url.scheme}://{url.netloc}"
    except ValueError:
        pass
    return ""


def _headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """移除逐跳头，保留应用响应的重复 Set-Cookie 等端到端头字段。"""
    connection_tokens = {token.strip() for key, value in headers if key.lower() == b"connection"
                         for token in value.lower().split(b",")}
    blocked = _HOP_HEADERS | connection_tokens
    return [(key, value) for key, value in headers if key.lower() not in blocked]


def _request_path(scope: Scope) -> bytes:
    """保留请求路径和查询参数的原始编码，由固定的运行目标决定主机。"""
    path = scope.get("raw_path") or scope["path"].encode("utf-8")
    query = scope["query_string"]
    return path + b"?" + query if query else path


def _target(workspace: Path, layer: PreviewLayer) -> tuple[str, str]:
    """每次转发都核对当前工作区的就绪状态和受管 PID，避免命中其他应用。"""
    record = read_record(workspace)
    attempt = service_attempt_id(record, layer)
    service = record[layer]
    if service["status"] != "running" or service_in_maintenance(maintenance_owner(workspace), layer):
        return "", attempt
    root = runtime_root(workspace)
    pid = service.get("pid")
    if not isinstance(pid, int) or not pid_is_running(pid):
        return "", attempt
    if layer == "backend":
        backend = read_backend_process_record(root / BACKEND_PROCESS_FILENAME)
        port = service.get("port")
        if backend and backend.pid == pid and isinstance(port, int) and 0 < port <= 65535:
            return f"http://127.0.0.1:{port}", attempt
    elif int((root / "frontend.pid").read_text(encoding="utf-8")) == pid:
        return _origin(service.get("url") or ""), attempt
    return "", attempt


class PageDebugGateway:
    """一个工作区的页面调试入口，端口跟随工作区而非单次后端启动。"""

    def __init__(self, workspace: Path) -> None:
        """预绑定随机回环端口，再交给独立的 ASGI 事件循环。"""
        self.workspace = workspace
        self.blocked_request: tuple[str, str, int] | None = None
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.listener = listener.dup()
            self.host = f"127.0.0.1:{listener.getsockname()[1]}"
        self.origin = f"http://{self.host}"
        self.server = uvicorn.Server(uvicorn.Config(
            self, lifespan="off", loop="asyncio", log_config=None, access_log=False,
            proxy_headers=False, timeout_graceful_shutdown=1,
        ))
        self.thread = Thread(target=self._run, name="page-debug-gateway", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        """在线程结束时释放监听，不改动 Studio Backend 的事件循环及日志配置。"""
        with self.listener:
            self.server.run(sockets=[self.listener])

    def close(self) -> None:
        """让本地入口停止接收请求并结束热更新连接。"""
        self.server.should_exit = True
        self.thread.join(timeout=2)

    @cached_property
    def _tls_context(self) -> SSLContext:
        """本入口只加载一次受信证书，每个资源请求仍使用独立的客户端与 Cookie。"""
        return httpx.create_ssl_context(trust_env=False)

    def _unavailable(self, backend_attempt: str) -> Response:
        """仅对后端尚未启动时实际被拦截的请求记录时间，供页面短暂提示。"""
        record = read_record(self.workspace)
        if record["backend"]["status"] in {"stopped", "skipped"} and service_attempt_id(record, "backend") == backend_attempt:
            with _lock:
                self.blocked_request = (service_attempt_id(record, "frontend"), backend_attempt, time_ns() // 1_000_000)
        return JSONResponse({"code": "BACKEND_NOT_READY", "message": "后端服务尚未就绪，请先启动后端服务。"},
                            status_code=503, headers={"Cache-Control": "no-store"})

    def _request_headers(self, scope: Scope, upstream: str) -> list[tuple[bytes, bytes]]:
        """以真实上游域名发送同源请求，保留应用 Cookie 和业务请求头。"""
        headers = []
        for key, value in _headers(scope["headers"]):
            if key == b"host" or key == b"forwarded" or key.startswith(b"x-forwarded-"):
                continue
            if key in {b"origin", b"referer"} and (value == self.origin.encode() or value.startswith(self.origin.encode() + b"/")):
                value = upstream.encode() + value[len(self.origin):]
            headers.append((key, value))
        return headers

    async def _http(self, scope: Scope, receive: Receive, send: Send,
                    upstream: str, layer: PreviewLayer, attempt: str) -> None:
        """透明转发 HTTP，保留方法、请求体和应用真实响应，不自动重发。"""
        request = Request(scope, receive)
        url = httpx.URL(upstream).copy_with(raw_path=_request_path(scope))
        async with httpx.AsyncClient(verify=self._tls_context, trust_env=False, timeout=httpx.Timeout(60, connect=3)) as client:
            try:
                body = request.stream() if (request.method not in {"GET", "HEAD"} or
                                             "content-length" in request.headers or "transfer-encoding" in request.headers) else None
                upstream_request = client.build_request(request.method, url, headers=self._request_headers(scope, upstream), content=body)
                response = await client.send(upstream_request, stream=True)
            except ClientDisconnect:
                return
            except httpx.RequestError as exc:
                if layer == "backend" and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                    error = self._unavailable(attempt)
                else:
                    error = Response("调试入口无法读取服务响应。", status_code=504 if isinstance(exc, httpx.TimeoutException) else 502)
                await error(scope, receive, send)
                return
            try:
                if layer == "backend":
                    with _lock:
                        self.blocked_request = None
                forwarded = StreamingResponse(response.aiter_raw(), status_code=response.status_code)
                forwarded.raw_headers = [
                    (name.lower(), value[len(upstream):] if name.lower() == b"location" and value.startswith(upstream.encode() + b"/") else value)
                    for name, value in _headers(response.headers.raw)
                ]
                await forwarded(scope, receive, send)
            finally:
                await response.aclose()

    async def _websocket(self, socket: WebSocket, scope: Scope, upstream: str) -> None:
        """双向转发热更新连接，包括文本帧、二进制帧和子协议。"""
        url = ("ws" + upstream[4:]) + _request_path(scope).decode("ascii")
        extra = [(key.decode("ascii"), value.decode("latin1")) for key, value in self._request_headers(scope, upstream)
                 if not key.startswith(b"sec-websocket-")]
        accepted = False
        try:
            async with connect(url, additional_headers=extra, subprotocols=scope.get("subprotocols"),
                               proxy=None, open_timeout=3, close_timeout=1) as server:
                await socket.accept(subprotocol=server.subprotocol)
                accepted = True

                async def to_browser() -> None:
                    """将开发服务器发送的热更新帧转发给当前 iframe。"""
                    async for data in server:
                        await (socket.send_text(data) if isinstance(data, str) else socket.send_bytes(data))

                async def to_server() -> None:
                    """浏览器关闭后结束另一方向的转发。"""
                    while True:
                        frame = await socket.receive()
                        if frame["type"] == "websocket.disconnect":
                            return
                        await server.send(frame["text"] if frame.get("text") is not None else frame["bytes"])

                tasks = [asyncio.create_task(to_browser()), asyncio.create_task(to_server())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, EOFError, WebSocketDisconnect, WebSocketException, TimeoutError):
            pass
        with suppress(RuntimeError, OSError, WebSocketDisconnect):
            await socket.close(code=1000 if accepted else 1013)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """按同源 API 路径分流，页面静态资源和热更新仍由前端进程提供。"""
        path = scope["path"]
        layer: PreviewLayer = "backend" if path == "/api" or path.startswith("/api/") else "frontend"
        is_socket = scope["type"] == "websocket"
        request = WebSocket(scope, receive, send) if is_socket else Request(scope, receive)
        headers = request.headers
        # 页面导航允许从 Studio iframe 进入，API 和 HMR 限制为入口本身发起的请求。
        same_origin = headers.get("origin") in {None, self.origin} and headers.get("sec-fetch-site") not in {"same-site", "cross-site"}
        valid_path = path.startswith("/") and not path.startswith("//") and "\\" not in path and not ({".", ".."} & set(path.split("/")))
        if (headers.get("host") != self.host or not valid_path
                or ((is_socket or layer == "backend") and not same_origin)):
            if is_socket:
                await request.close(code=1008)
            else:
                await Response(status_code=403)(scope, receive, send)
            return
        try:
            upstream, attempt = await asyncio.to_thread(_target, self.workspace, layer)
        except (OSError, ValueError):
            upstream, attempt = "", ""
        if is_socket:
            if upstream:
                await self._websocket(request, scope, upstream)
            else:
                await request.close(code=1013)
        elif upstream:
            await self._http(scope, receive, send, upstream, layer, attempt)
        else:
            error = self._unavailable(attempt) if layer == "backend" else Response("前端服务尚未就绪。", status_code=503)
            await error(scope, receive, send)


def page_debug_projection(workspace: str, runtime: dict[str, Any]) -> dict[str, Any]:
    """为已就绪的前端轮次分配地址，仅投影本轮未启动后端的请求时间。"""
    root = Path(workspace).resolve()
    if runtime["frontend"]["status"] != "running" or service_in_maintenance(runtime.get("maintenance"), "frontend"):
        return {}
    frontend_attempt = service_attempt_id(runtime, "frontend")
    with _lock:
        current = read_record(root)
        if current["frontend"]["status"] != "running" or service_attempt_id(current, "frontend") != frontend_attempt:
            return {}
        entry = _gateways.get(root)
        if entry is None or not entry.thread.is_alive():
            entry = _gateways[root] = PageDebugGateway(root)
        result = {"origin": entry.origin, "frontendAttemptId": frontend_attempt}
        if entry.blocked_request:
            frontend_id, backend_id, blocked_at = entry.blocked_request
            if (frontend_id == frontend_attempt and backend_id == service_attempt_id(current, "backend")
                    and current["backend"]["status"] in {"stopped", "skipped"}):
                result["backendRequestBlockedAt"] = blocked_at
        return result


def close_page_debug_gateway(workspace: str | Path) -> None:
    """关闭退出工作台、删除项目或整体停服后的回环入口。"""
    with _lock:
        entry = _gateways.pop(Path(workspace).resolve(), None)
    if entry:
        entry.close()
