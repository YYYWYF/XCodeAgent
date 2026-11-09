"""升级退出准备后拒绝新业务；仅保留明确的预览清理入口。"""

from starlette.responses import StreamingResponse
from app.services.backend_runtime import runtime
from app.protocols.ag_ui_action_stream import build_ag_ui_action_stream


class BackendRuntimeAdmission:
    """ASGI 响应完整生命周期计数，订阅断开不覆盖后台 owner 自己的占用。"""
    def __init__(self, app):
        """保留原 ASGI 应用，不缓存请求正文或业务状态。"""
        self.app = app

    async def __call__(self, scope, receive, send):
        """退出栅栏检查与请求登记同步执行，忙碌时不产生退出窗口。"""
        path = scope.get("path", "")
        if scope["type"] != "http" or path == "/health":
            return await self.app(scope, receive, send)
        if path == "/backend-runtime/run":
            runtime.control_requests += 1
            try:
                return await self.app(scope, receive, send)
            finally:
                runtime.control_requests -= 1
        if not runtime.enter_request(preview_cleanup=path == "/preview-runtime/run"):
            async def rejected():
                """只报告服务退场，不创建产品 Run、确认门或恢复任务。"""
                raise ValueError("后台服务正在安全退出，请重新打开客户端。")
            response = StreamingResponse(build_ag_ui_action_stream(payload={}, event_name="backend-runtime",
                state_key="backendRuntime", run_id_prefix="backend-retiring", operation=rejected,
                error_message_prefix="暂时无法接受新任务", register_workspace_run=False), media_type="text/event-stream")
            return await response(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            runtime.leave_request()
