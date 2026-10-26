"""埋点提交的 AG-UI 协议适配，不承担业务触发判断。"""

from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.observability.telemetry import emit_telemetry_event, emit_telemetry_trace
from app.observability.telemetry_models import ExecutionTraceRecord, TelemetryEventRecord
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream


class TelemetryInput(BaseModel):
    """校验提交动作和记录容器；记录内部所有字段仍可省略。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["emit_event", "emit_trace"]
    record: dict[str, Any]


def telemetry_capabilities() -> dict[str, Any]:
    """发布埋点入口及尽力提交、不确认第三方送达的响应语义。"""

    return {
        "name": "telemetry",
        "endpoint": "/telemetry/run",
        "transport": "ag-ui-sse",
        "actionField": "forwardedProps.telemetry",
        "actions": ["emit_event", "emit_trace"],
        "customEventName": "telemetry",
        "stateSnapshotKey": "telemetry",
        "workflowIndependent": True,
        "deliveryPolicy": "best_effort",
        "deliveryAcknowledged": False,
    }


def build_telemetry_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """将提交成功和输入失败都编码为完整 AG-UI 动作生命周期。"""

    async def operation() -> AgUiActionResult:
        """校验外部输入后调用现有发送入口，实际 POST 留在专用线程池。"""

        forwarded = payload.get("forwardedProps")
        raw_input = forwarded.get("telemetry") if isinstance(forwarded, dict) else None
        try:
            request = TelemetryInput.model_validate(raw_input)
            if request.action == "emit_event":
                record = TelemetryEventRecord.model_validate(request.record)
            else:
                record = ExecutionTraceRecord.model_validate(request.record)
        except ValidationError:
            # 不把 Pydantic 错误中的原始埋点内容回显到响应或日志。
            raise ValueError("telemetry.action 或 record 不符合埋点字段定义。") from None

        if request.action == "emit_event":
            emit_telemetry_event(record)
        else:
            emit_telemetry_trace(record)
        # 原发送入口无返回值，满载或未配置地址时可能丢弃，不能声称已入队或送达。
        return AgUiActionResult(
            data={},
            message="埋点提交调用已完成，不代表第三方已收到。",
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name="telemetry",
        state_key="telemetry",
        run_id_prefix="telemetry",
        operation=operation,
        error_message_prefix="埋点提交失败",
        accept=accept,
        register_workspace_run=False,
    )
