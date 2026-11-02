"""在现有规划 AG-UI 端点只读编译需求阶段的角色与权限资源视图。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.application_config import read_application_config
from app.services.authorization_manifest import (
    authorization_enabled_from_application_config, compile_authorization_resources,
)
from app.services.product_plan import validate_product_plan


class RequirementAuthorizationRequest(BaseModel):
    """验证只读预览输入；客户端不能提交能力开关、资源清单或角色授权结果。"""

    model_config = ConfigDict(extra="forbid")
    action: Literal["preview"]
    workspaceRoot: str = Field(min_length=1)
    requirementSpec: dict[str, Any]
    productPlan: dict[str, Any]


def build_requirement_authorization_stream(*, payload: dict[str, Any], raw: Any, accept: str | None = None):
    """编译正在审阅的上游草稿，不写文件、不调用模型、不改变原 Graph 确认状态。"""

    async def preview() -> AgUiActionResult:
        """配置始终读取当前工作区，完整产品草稿才可产生正式身份的资源预览。"""

        request = RequirementAuthorizationRequest.model_validate(raw)
        config = read_application_config(request.workspaceRoot)
        enabled = authorization_enabled_from_application_config(config)
        if enabled and not request.productPlan.get("pages"):
            return AgUiActionResult(data={"enabled": True, "ready": False}, message="页面与操作生成后展示权限资源。")
        if enabled:
            errors = validate_product_plan(request.productPlan, request.requirementSpec)
            if errors:
                raise ValueError("；".join(errors))
        manifest = compile_authorization_resources(
            request.requirementSpec, request.productPlan, application_config=config,
        )
        return AgUiActionResult(
            data={"enabled": enabled, "ready": True, "manifest": manifest},
            message="角色与权限资源已按当前需求文档编译，仍须联合确认。",
        )

    return build_ag_ui_action_stream(
        payload=payload, event_name="requirement-authorization", state_key="requirementAuthorization",
        run_id_prefix="requirement-authorization", operation=preview, accept=accept,
        error_message_prefix="角色与权限读取失败", register_workspace_run=False,
    )
