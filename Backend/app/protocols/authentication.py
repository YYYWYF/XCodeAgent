"""独立桌面认证 AG-UI 协议，不进入开发工作流或持久化聊天。"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.protocols.ag_ui_action_stream import AgUiActionResult, AgUiActionProgress, build_ag_ui_action_stream
from app.services.auth_credentials import CredentialStore
from app.services.yunxiao_auth import AuthService


class CloudRequest(BaseModel):
    """限制桌面云端 JSON 请求的输入范围。"""
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=1_048_576)


class AuthenticationInput(BaseModel):
    """显式校验桌面环境和会话身份。"""
    model_config = ConfigDict(extra="forbid")
    action: Literal["initialize", "login", "status", "cancel", "logout", "shutdown", "request"]
    sessionId: str = Field(pattern=r"^[0-9a-f-]{36}$")
    workingDir: str
    request: CloudRequest | None = None


_service: AuthService | None = None


def authentication_service() -> AuthService:
    """延迟创建认证服务，避免模块导入时触碰用户文件。"""
    global _service
    if _service is None:
        _service = AuthService(CredentialStore())
    return _service


def build_authentication_stream(payload: dict, accept: str | None = None):
    """把成功、进度、取消及业务错误包装为完整 AG-UI 生命周期。"""
    async def operation(report):
        """通过后端唯一业务入口派发认证动作。"""
        value = AuthenticationInput.model_validate((payload.get("forwardedProps") or {}).get("authentication", {}))
        service = authentication_service()
        if value.workingDir != service.store.working_dir:
            raise RuntimeError("Electron 和 Python 的用户环境不一致，请检查 DEVAGENTSTUDIO_WORKING_DIR。")
        if value.action == "login":
            async def progress(stage, message, percent, **data):
                """只公开授权 URL 和进度，不投影 token。"""
                await report(AgUiActionProgress(stage=stage, message=message, percent=percent, data=data))
            result = await service.login(value.sessionId, progress)
        elif value.action == "status":
            result = service.status(value.sessionId)
        elif value.action == "request":
            if value.request is None:
                raise RuntimeError("云端请求缺少参数。")
            result = await service.cloud_request(value.sessionId, value.request.model_dump())
        else:
            result = await service.reset(value.sessionId, initialize=value.action == "initialize", shutdown=value.action == "shutdown")
        return AgUiActionResult(data=result, message="认证操作已完成。")
    return build_ag_ui_action_stream(payload=payload, event_name="authentication", state_key="authentication", run_id_prefix="authentication", progress_operation=operation, error_message_prefix="认证操作失败", accept=accept, emit_progress_text=False, register_workspace_run=False)
