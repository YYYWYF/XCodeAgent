"""在模型 HTTP 字节流边界观察暂停，包含 SDK 尚未交付给 Agent 的推理或心跳。"""

import httpx

from app.services.run_workers import current_run_id
from app.services.workspace_process_registry import workspace_process_registry


def _check_cancelled(run_id: str) -> None:
    """只停止当前请求所属 Run，不影响复用同一模型客户端的后续运行。"""
    if run_id and workspace_process_registry.is_run_cancelled(run_id):
        raise RuntimeError("当前运行已停止，已中止模型响应读取。")


class _CancellableStream(httpx.SyncByteStream):
    """在 SDK 过滤空内容与 SSE 心跳之前截断已取消的同步响应。"""

    def __init__(self, stream: httpx.SyncByteStream, run_id: str):
        """将响应绑定到发起请求时的精确运行身份。"""
        self.stream, self.run_id = stream, run_id
        self.closed = False

    def __iter__(self):
        """取消后不再向 SDK 交付下一块字节，并关闭真实响应。"""
        try:
            _check_cancelled(self.run_id)
            for chunk in self.stream:
                _check_cancelled(self.run_id)
                yield chunk
        finally:
            self.close()

    def close(self):
        """沿 HTTP 客户端既有关闭协议释放本次响应连接。"""
        if not self.closed:
            self.closed = True
            self.stream.close()


class _AsyncCancellableStream(httpx.AsyncByteStream):
    """为异步模型响应保留相同的精确取消边界。"""

    def __init__(self, stream: httpx.AsyncByteStream, run_id: str):
        """绑定本次响应与 Run。"""
        self.stream, self.run_id = stream, run_id
        self.closed = False

    async def __aiter__(self):
        """在每块原始响应到达时检查停止标记。"""
        try:
            _check_cancelled(self.run_id)
            async for chunk in self.stream:
                _check_cancelled(self.run_id)
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self):
        """关闭本次异步响应。"""
        if not self.closed:
            self.closed = True
            await self.stream.aclose()


def check_model_request(request: httpx.Request) -> None:
    """阻止 SDK 自动重试已暂停的模型请求。"""
    _check_cancelled(current_run_id())


def guard_model_response(response: httpx.Response) -> None:
    """在同步 HTTP 响应进入 SDK 前附加取消观察。"""
    run_id = current_run_id()
    if run_id:
        response.stream = _CancellableStream(response.stream, run_id)


async def check_async_model_request(request: httpx.Request) -> None:
    """让异步 SDK 重试使用同一停止标记。"""
    check_model_request(request)


async def guard_async_model_response(response: httpx.Response) -> None:
    """在异步 HTTP 响应进入 SDK 前附加取消观察。"""
    run_id = current_run_id()
    if run_id:
        response.stream = _AsyncCancellableStream(response.stream, run_id)
