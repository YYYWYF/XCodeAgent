"""独立 Endpoint API 设计配置与详情的 AG-UI 协议适配。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import AgUiActionResult, ProgressReporter, build_ag_ui_action_stream
from app.domain.endpoint_design_assist import ReplaceMappingRequest, RefineRuleRequest, GenerateMappingRequest
from app.services.endpoint_design_assist import replace_mapping, refine_rule, generate_mapping
from app.domain.endpoint_design_external_assist import ExternalGenerateRequest, ExternalReplaceMappingRequest, ExternalRefineRequest
from app.services.endpoint_design_external_assist import generate_external_mapping, replace_external_mapping, refine_external_rule
from app.services.endpoint_design_assist_retry import MappingGenerationFailure
from app.services.binding_workspace import BindingDraftRequest, save_binding_draft, clear_binding_draft
from app.services.endpoint_design_detail import (
    EndpointDesignDetailRequest,
    EndpointDesignPrepareRequest,
    EndpointDesignSaveRequest,
    prepare_endpoint_design,
    read_endpoint_design_detail,
    save_endpoint_design,
)


ENDPOINT_DESIGNS_EVENT_NAME = "endpoint-designs"


def endpoint_designs_capabilities() -> dict[str, Any]:
    """发布 Endpoint 设计独立配置与查询的 AG-UI 能力。"""

    return {
        "name": "endpoint-designs",
        "endpoint": "/endpoint-designs/run",
        "transport": "ag-ui-sse",
        "actions": ["get", "prepare", "save", "save_draft", "discard_draft", "generate_mapping", "refine_rule", "replace_mapping"],
        "customEventName": ENDPOINT_DESIGNS_EVENT_NAME,
        "stateSnapshotKey": "endpointDesigns",
        "workflowIndependent": True,
        "readOnly": False,
        "mappingGenerationSteps": ["operation", "query", "write", "return", "description"],
        "externalMappingGenerationSteps": ["request", "return", "description"],
        "mappingAssistSourceTypes": ["database", "external_api"],
    }


def build_endpoint_designs_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """构造 Endpoint 设计独立配置与查询的完整 AG-UI 生命周期。"""

    request_input = _endpoint_designs_input(payload)

    async def operation() -> AgUiActionResult:
        """执行严格校验后的独立 Endpoint 配置动作，不触发主工作流。"""

        action = str(request_input.get("action") or "get")
        request_values = {key: value for key, value in request_input.items() if key != "action"}
        if action == "discard_draft":
            request = EndpointDesignDetailRequest.model_validate(request_values)
            clear_binding_draft(request.workspace_root, request.api_contract_id, request.endpoint_id)
            return AgUiActionResult(data={"action": action}, message="已放弃映射草稿，正式产物未变更。")
        if action == "save_draft":
            draft = save_binding_draft(BindingDraftRequest.model_validate(request_values))
            return AgUiActionResult(data={"action": action, "draft": draft}, message="映射草稿已保存，尚未确认。")
        if action == "get":
            request = EndpointDesignDetailRequest.model_validate(request_values)
            detail = read_endpoint_design_detail(request)
            return AgUiActionResult(data={"action": action, "detail": detail}, message="已读取 Endpoint API 映射结果。")
        if action == "prepare":
            request = EndpointDesignPrepareRequest.model_validate(request_values)
            preparation = prepare_endpoint_design(request)
            return AgUiActionResult(data={"action": action, "preparation": preparation}, message="已准备 Endpoint API 映射配置。")
        if action == "save":
            request = EndpointDesignSaveRequest.model_validate(request_values)
            saved = save_endpoint_design(request)
            return AgUiActionResult(data={"action": action, "saved": saved, "detail": saved.get("detail")}, message="Endpoint API 映射配置已保存。")
        raise ValueError("endpointDesigns.action 不属于当前支持的动作。")

    async def assist_operation(report: ProgressReporter) -> AgUiActionResult:
        """辅助动作复用标准生命周期与取消边界，建议和应用均不落盘。"""
        action = str(request_input.get("action"))
        values = {key: value for key, value in request_input.items() if key != "action"}
        thread_id = str(payload.get("threadId") or "")
        # 来源决定严格请求模型，未知来源仍由原数据库模型拒绝，不能混用生成结果。
        external = isinstance(values.get("selection"), dict) and values["selection"].get("sourceType") == "external_api"
        if action == "generate_mapping":
            result = await generate_external_mapping(ExternalGenerateRequest.model_validate(values), report, thread_id) if external else await generate_mapping(GenerateMappingRequest.model_validate(values), report, thread_id)
            message = "请先补充数据库操作的待明确信息，再继续生成。" if result["mapping"] is None else "完整映射已生成，请预览后替换当前草稿。"
            return AgUiActionResult(data={"action": action, "assistance": result}, message=message)
        if action == "refine_rule":
            result = await refine_external_rule(ExternalRefineRequest.model_validate(values), report, thread_id) if external else await refine_rule(RefineRuleRequest.model_validate(values), report, thread_id)
            return AgUiActionResult(data={"action": action, "refinedRule": result}, message="业务规则建议已生成，采用后仍需应用规则。")
        result = await asyncio.to_thread(replace_external_mapping, ExternalReplaceMappingRequest.model_validate(values)) if external else await asyncio.to_thread(replace_mapping, ReplaceMappingRequest.model_validate(values))
        return AgUiActionResult(data={"action": action, "applied": result}, message="完整映射已校验，请补全未配置项后保存并确认草稿。")

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=ENDPOINT_DESIGNS_EVENT_NAME,
        state_key="endpointDesigns",
        run_id_prefix="endpoint-designs",
        operation=operation if request_input.get("action") not in {"generate_mapping", "refine_rule", "replace_mapping"} else None,
        progress_operation=assist_operation if request_input.get("action") in {"generate_mapping", "refine_rule", "replace_mapping"} else None,
        error_message_prefix="Endpoint API 映射操作失败",
        error_data=lambda exc: {"action": str(request_input.get("action") or "get"),
                                **({"assistFailure": exc.failure} if isinstance(exc, MappingGenerationFailure) else {})},
        accept=accept,
    )


def _endpoint_designs_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 forwardedProps 中提取 Endpoint 设计查询输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    value = forwarded_props.get("endpointDesigns")
    return value if isinstance(value, dict) else {}
