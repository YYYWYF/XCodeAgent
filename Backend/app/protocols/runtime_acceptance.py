"""独立 AG-UI 桌面桥与分层人工核验，不公开启动任意 URL 或调用模型的能力。"""
import asyncio
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.runtime_acceptance_bridge import claim_desktop_acceptance, complete_desktop_acceptance
from app.domain.agent_ui_acceptance import AcceptanceReviewAction
from app.services.agent_ui_acceptance import acceptance_matrix, record_acceptance


class AcceptanceEvidence(BaseModel):
    """限定可存储证据，不接受正文、请求头、Cookie 或供应商配置。"""
    model_config = ConfigDict(extra="forbid")
    passed: bool
    agentId: str = Field(max_length=128)
    threadId: str = Field(max_length=128)
    runId: str = Field(max_length=128)
    lifecycle: bool
    textSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    textBytes: int = Field(ge=0, le=131072)
    pageObserved: bool
    reason: str = Field(default="", max_length=500)


class AcceptanceAction(BaseModel):
    """严格验证桥动作及一次性令牌，禁止通过领取接口签发新任务。"""
    model_config = ConfigDict(extra="forbid")
    action: Literal["claim", "complete"]
    token: str = Field(default="", max_length=128)
    evidence: AcceptanceEvidence | None = None
    verifierSha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def acceptance_capabilities() -> dict:
    """公开桌面桥的现行 AG-UI 元数据。"""
    return {"endpoint": "/agent-runtime-acceptance/run", "transport": "ag-ui-sse",
            "actions": ["claim", "complete", "get", "record"], "customEventName": "runtime-acceptance",
            "stateSnapshotKey": "runtimeAcceptance", "workflowIndependent": True,
            "actionField": "forwardedProps.runtimeAcceptance", "layers": ["code", "visual", "connection", "business"],
            "evidenceMethod": "user_observed", "automaticExecution": False, "automaticReplay": False}


def build_acceptance_stream(*, payload: dict, accept: str | None):
    """为领取、提交和业务失败均发出完整的官方动作生命周期。"""
    async def operation():
        """把阻塞领取放入线程，保持 Studio 事件循环与正在进行的 Build 独立。"""
        raw = (payload.get("forwardedProps") or {}).get("runtimeAcceptance")
        if isinstance(raw, dict) and raw.get('action') in {'get', 'record'}:
            try:
                review = AcceptanceReviewAction.model_validate(raw)
            except ValidationError:
                raise ValueError('验收输入不符合当前合同，不能包含正文、凭据或任意业务数据。') from None
            thread_id, run_id = payload.get('threadId'), payload.get('runId')
            if not isinstance(thread_id, str) or not thread_id or not isinstance(run_id, str) or not run_id:
                raise ValueError('验收记录必须绑定当前会话与运行。')
            if review.action == 'get':
                if review.report is not None:
                    raise ValueError('读取不能夹带核验记录。')
                matrix = await asyncio.to_thread(acceptance_matrix, review.workspace)
            else:
                if review.report is None:
                    raise ValueError('登记缺少当前用例核验项。')
                matrix = await asyncio.to_thread(record_acceptance, review.workspace, review.report.model_dump(),
                                                 thread_id=thread_id, run_id=run_id)
            return AgUiActionResult(data={'matrix': matrix}, message='已读取当前分层验收；登记不触发测试或确认版本。')
        request = AcceptanceAction.model_validate(raw)
        if request.action == "claim":
            if request.token or request.evidence is not None or not request.verifierSha256:
                raise ValueError("领取动作不能夹带结果。")
            data = await asyncio.to_thread(claim_desktop_acceptance, request.verifierSha256)
        else:
            if not request.token or request.evidence is None or request.verifierSha256 is not None:
                raise ValueError("提交动作缺少一次性身份或证据。")
            data = complete_desktop_acceptance(request.token, request.evidence.model_dump())
        return AgUiActionResult(data=data, message="桌面验收桥动作已完成。")
    return build_ag_ui_action_stream(payload=payload, event_name="runtime-acceptance", state_key="runtimeAcceptance",
        run_id_prefix="runtime-acceptance", operation=operation, error_message_prefix="桌面验收失败", accept=accept)
