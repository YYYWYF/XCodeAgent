"""逐次模型调用的业务上下文；由调用方绑定阶段和操作，不填产品模式。"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import inspect

from langchain_core.runnables import RunnableConfig

_CONTEXT = ContextVar("model_business_context", default=None)
_ACTION_RUN_ID: ContextVar[str] = ContextVar("model_action_run_id", default="")
_STAGES = {
    "requirements": "requirements", "product_planning": "requirements",
    "ui_confirmation": "ui_design", "technical_planning_generate": "technical_planning",
    "technical_planning": "technical_planning", "project_planning": "technical_planning",
    "prepare_build_tasks": "planning", "authorization_bootstrap": "build", "build": "build",
    "execute_frontend": "conversation", "execute_backend": "conversation",
    "classify_intent": "conversation", "scan_workspace_code": "conversation",
    "scan_change_impact_code": "conversation", "respond_conversation": "conversation",
    "answer_workspace": "conversation", "direct_modification_repair": "conversation",
    "unit_test": "unit_test", "unit_test_repair": "unit_test",
    "integration_test": "integration_test", "small_task_repair": "integration_test",
    "code_review": "code_review", "acceptance": "acceptance",
}

_DESIGN_STAGES = {
    "requirements": "requirements", "product_planning": "requirements",
    "requirement_document": "requirements", "requirements_review": "requirements",
    "requirement_document_review": "requirements", "ui_confirmation": "ui_design",
    "ui_confirmation_review": "ui_design", "planning_stage_entry": "technical_planning",
    "technical_planning": "technical_planning", "technical_planning_begin": "technical_planning",
    "technical_planning_generate": "technical_planning", "technical_planning_commit": "technical_planning",
    "technical_planning_confirm": "technical_planning", "technical_planning_review": "technical_planning",
}


def design_business_stage(state):
    """按调用前的审阅入口归属设计分类，绝不根据模型预测的目标倒填阶段。"""
    if state.get("product_stage_conversation"):
        return "requirements"
    origin = state.get("design_interaction_origin")
    if origin:
        return _DESIGN_STAGES.get(origin)
    return _DESIGN_STAGES.get(state.get("phase"))


@contextmanager
def model_business_context(value):
    """绑定本次执行上下文，异常、取消和正常结束均恢复原值。"""
    token = _CONTEXT.set(deepcopy(value))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


@contextmanager
def bind_model_action_run(run_id: str):
    """在独立 AG-UI 动作执行期间传递业务 Run ID。"""
    token = _ACTION_RUN_ID.set(run_id)
    try:
        yield
    finally:
        _ACTION_RUN_ID.reset(token)


def current_model_action_run_id() -> str:
    """读取当前独立 AG-UI 动作的业务 Run ID。"""
    return _ACTION_RUN_ID.get()


def current_model_context(metadata):
    """合并调用方显式字段与当前执行事实，始终省略 mode。"""
    value = deepcopy(_CONTEXT.get() or {})
    extensions = metadata.get("extensions")
    explicit = extensions.get("context") if isinstance(extensions, dict) else None
    if isinstance(explicit, dict):
        value.update(deepcopy(explicit))
    node = metadata.get("langgraph_node")
    if isinstance(node, str) and node:
        value["node_name"] = node
    value.pop("mode", None)
    return value


def _target(state):
    """优先读取当前执行范围，再读取明确选择的开发目标。"""
    revision = state.get("change_target") or {}
    if revision.get("type") in {"page", "endpoint"}:
        value = {"type": revision["type"]}
        id_key = "pageId" if revision["type"] == "page" else "endpointId"
        if revision.get(id_key):
            value["id"] = revision[id_key]
        if revision["type"] == "endpoint" and revision.get("apiContractId"):
            value["api_contract_id"] = revision["apiContractId"]
        return value
    scope = state.get("build_execution_scope") or {}
    kind = scope.get("type") or state.get("detail_target_type")
    ids = {"page": "selectedPageId", "endpoint": "selected_endpoint_id", "entity": "selected_entity_id"}
    if kind in ids:
        value = {"type": kind}
        identifier = scope.get("targetId") or state.get(ids[kind])
        if identifier:
            value["id"] = identifier
        if kind == "endpoint":
            contract = scope.get("apiContractId") or state.get("selected_api_contract_id")
            if contract:
                value["api_contract_id"] = contract
        return value
    if state.get("project_id"):
        return {"type": "application", "id": state["project_id"]}
    return None


def observe_model_node(name, action, *, business_stage=None):
    """为普通节点绑定当前 State 的业务事实，保留子图原有执行接口。"""
    if not inspect.isfunction(action):
        return action
    accepts_config = "config" in inspect.signature(action).parameters

    def context_for(state):
        """每次进入节点重新读取目标；不把上一个节点的 phase 当作当前阶段。"""
        value = deepcopy(_CONTEXT.get() or {})
        value["node_name"] = name
        stage = (design_business_stage(state) if name == "design_intent_analysis"
                 else _STAGES.get(name, business_stage))
        if stage is not None:
            value["business_stage"] = stage
        target = _target(state)
        if target is not None:
            value["target"] = target
        else:
            value.pop("target", None)
        return value

    async def async_node(state, config: RunnableConfig):
        """异步节点的上下文隔离到当前任务，并随取消释放。"""
        with model_business_context(context_for(state)):
            return await action(state, **({"config": config} if accepts_config else {}))

    def sync_node(state, config: RunnableConfig):
        """同步节点保持原返回值及异常，嵌套模型调用继承当前上下文。"""
        with model_business_context(context_for(state)):
            return action(state, **({"config": config} if accepts_config else {}))

    return async_node if inspect.iscoroutinefunction(action) else sync_node
