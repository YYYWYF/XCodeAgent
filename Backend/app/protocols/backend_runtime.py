"""桌面后台退场使用独立 AG-UI 生命周期，不进入产品工作流或消费业务确认。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.backend_instance import current_backend_instance
from app.services.backend_runtime import runtime


class BackendRuntimeAction(BaseModel):
    """严格校验实例控制凭据、客户端身份及一次性退出票据。"""
    model_config = ConfigDict(extra="forbid")
    action: Literal["attach", "detach", "prepare_exit", "commit_exit", "cancel_exit"]
    backendInstanceId: str = Field(min_length=1, max_length=128)
    controlToken: str = Field(min_length=32, max_length=256, repr=False)
    clientId: str = Field(min_length=1, max_length=128)
    ticket: str = Field(default="", max_length=128)


def build_backend_runtime_stream(payload: dict, accept: str | None = None):
    """控制结果、忙碌及失败均沿官方 AG-UI action stream 返回完整生命周期。"""
    async def operation() -> AgUiActionResult:
        """核对当前实例后原子执行控制，退出提交不会强杀仍在收口的任务。"""
        request = BackendRuntimeAction.model_validate((payload.get("forwardedProps") or {}).get("backendRuntime") or {})
        runtime.authorize(request.controlToken)
        if request.backendInstanceId != current_backend_instance().instance_id:
            raise ValueError("后台服务实例已变化，请重新打开客户端。")
        result = runtime.act(request.action, request.clientId, request.ticket)
        return AgUiActionResult(data=result, message="后台仍有任务或客户端，已保留原任务。" if result["state"] == "busy" else "已同步后台服务状态。")
    return build_ag_ui_action_stream(payload=payload, event_name="backend-runtime", state_key="backendRuntime",
        run_id_prefix="backend-runtime", operation=operation, accept=accept,
        error_message_prefix="后台服务交接失败", register_workspace_run=False)
