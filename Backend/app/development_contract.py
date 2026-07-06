from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


TargetType = Literal["frontend", "backend", "fullstack"]
ContractStatus = Literal["draft", "ready", "executing", "verifying", "done", "blocked"]
TaskType = Literal["inspect", "shared", "frontend", "backend", "fullstack", "feature", "test", "verify"]
TaskExecutionMode = Literal["main-integrated", "subagent-plan-only", "subagent-direct-write"]
TaskStatus = Literal["pending", "running", "done", "failed", "blocked"]


class DataModelContract(BaseModel):
    name: str
    description: str = ""


class ApiContract(BaseModel):
    name: str = ""
    method: str = "GET"
    path: str = ""
    purpose: str = ""
    feature_id: Optional[str] = Field(default=None, alias="featureId")


class ContractSpec(BaseModel):
    goal: str = ""
    users: List[str] = Field(default_factory=list)
    scope_in: List[str] = Field(default_factory=list, alias="scopeIn")
    scope_out: List[str] = Field(default_factory=list, alias="scopeOut")
    acceptance_criteria: List[str] = Field(default_factory=list, alias="acceptanceCriteria")


class ContractDesign(BaseModel):
    features: List[str] = Field(default_factory=list)
    shared_capabilities: List[str] = Field(default_factory=list, alias="sharedCapabilities")
    api_conventions: List[str] = Field(default_factory=list, alias="apiConventions")
    data_models: List[DataModelContract] = Field(default_factory=list, alias="dataModels")
    permissions: List[str] = Field(default_factory=list)
    error_handling: List[str] = Field(default_factory=list, alias="errorHandling")


class ContractSdd(BaseModel):
    spec: ContractSpec = Field(default_factory=ContractSpec)
    design: ContractDesign = Field(default_factory=ContractDesign)


class FeatureUiContract(BaseModel):
    pages: List[str] = Field(default_factory=list)
    states: List[str] = Field(default_factory=list)
    interactions: List[str] = Field(default_factory=list)


class FeatureContract(BaseModel):
    id: str
    name: str
    user_goal: str = Field(default="", alias="userGoal")
    ui: FeatureUiContract = Field(default_factory=FeatureUiContract)
    apis: List[ApiContract] = Field(default_factory=list)
    data_models: List[str] = Field(default_factory=list, alias="dataModels")
    dependencies: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list, alias="acceptanceCriteria")
    verification: List[str] = Field(default_factory=list)


class AgentTask(BaseModel):
    id: str
    title: str
    type: TaskType = "feature"
    feature_id: Optional[str] = Field(default=None, alias="featureId")
    assigned_agent: str = Field(default="main-agent", alias="assignedAgent")
    depends_on: List[str] = Field(default_factory=list, alias="dependsOn")
    target_files: List[str] = Field(default_factory=list, alias="targetFiles")
    can_run_in_parallel: bool = Field(default=False, alias="canRunInParallel")
    execution_mode: TaskExecutionMode = Field(default="subagent-plan-only", alias="executionMode")
    status: TaskStatus = "pending"
    acceptance_criteria: List[str] = Field(default_factory=list, alias="acceptanceCriteria")
    verification_commands: List[str] = Field(default_factory=list, alias="verificationCommands")
    direct_write_reason: str = Field(default="", alias="directWriteReason")


class TaskGraph(BaseModel):
    tasks: List[AgentTask] = Field(default_factory=list)
    parallelism_rules: List[str] = Field(default_factory=list, alias="parallelismRules")


class VerificationPlan(BaseModel):
    commands: List[str] = Field(default_factory=list)
    checks: List[str] = Field(default_factory=list)


class DevelopmentContract(BaseModel):
    id: str = Field(default_factory=lambda: f"contract-{uuid4().hex[:12]}")
    requirement: str = ""
    title: str = "应用开发计划"
    target_type: TargetType = Field(default="fullstack", alias="targetType")
    status: ContractStatus = "ready"
    summary: str = ""
    sdd: ContractSdd = Field(default_factory=ContractSdd)
    features: List[FeatureContract] = Field(default_factory=list)
    api_contracts: List[ApiContract] = Field(default_factory=list, alias="apiContracts")
    data_models: List[DataModelContract] = Field(default_factory=list, alias="dataModels")
    task_graph: TaskGraph = Field(default_factory=TaskGraph, alias="taskGraph")
    verification_plan: VerificationPlan = Field(default_factory=VerificationPlan, alias="verificationPlan")
    risks: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list, alias="openQuestions")
    next_actions: List[str] = Field(default_factory=list, alias="nextActions")


def normalize_contract(
    value: Any,
    *,
    requirement: str = "",
    task_graph: Optional[Dict[str, Any]] = None,
    verification_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = value if isinstance(value, dict) else {}
    sdd = _dict(plan.get("sdd"))
    features = [_normalize_feature(item, index) for index, item in enumerate(_list(plan.get("features")))]
    task_graph_value = task_graph or _dict(plan.get("taskGraph"))
    verification_value = verification_plan or _dict(plan.get("verificationPlan"))
    data_models = _data_models_from_design(sdd)
    api_contracts = _api_contracts_from_features(features)

    contract = DevelopmentContract(
        id=str(plan.get("id") or f"contract-{uuid4().hex[:12]}"),
        requirement=str(plan.get("requirement") or requirement),
        title=str(plan.get("title") or "应用开发计划"),
        targetType=_target_type(plan.get("targetType")),
        status=_contract_status(plan.get("status")),
        summary=str(plan.get("summary") or ""),
        sdd=_normalize_sdd(sdd, requirement=requirement),
        features=features,
        apiContracts=api_contracts,
        dataModels=data_models,
        taskGraph=task_graph_value,
        verificationPlan=verification_value,
        risks=_string_list(plan.get("risks")),
        openQuestions=_string_list(plan.get("openQuestions")),
        nextActions=_string_list(plan.get("nextActions")),
    )
    return _dump(contract)


def contract_to_plan(contract: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(contract)
    plan["taskGraph"] = _dict(contract.get("taskGraph"))
    plan["verificationPlan"] = _dict(contract.get("verificationPlan"))
    return plan


def _normalize_feature(value: Any, index: int) -> Dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    feature_id = str(item.get("id") or f"feature-{index + 1}")
    return _dump(
        FeatureContract(
            id=feature_id,
            name=str(item.get("name") or feature_id),
            userGoal=str(item.get("userGoal") or item.get("user_goal") or ""),
            ui=_dict(item.get("ui")),
            apis=[_normalize_api(api, feature_id=feature_id, index=api_index) for api_index, api in enumerate(_list(item.get("apis")))],
            dataModels=_string_list(item.get("dataModels")),
            dependencies=_string_list(item.get("dependencies")),
            acceptanceCriteria=_string_list(item.get("acceptanceCriteria")),
            verification=_string_list(item.get("verification")),
        )
    )


def _normalize_api(value: Any, *, feature_id: str, index: int) -> Dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    path = str(item.get("path") or "")
    method = str(item.get("method") or "GET").upper()
    return _dump(
        ApiContract(
            name=str(item.get("name") or f"{method} {path}".strip() or f"api-{index + 1}"),
            method=method,
            path=path,
            purpose=str(item.get("purpose") or ""),
            featureId=feature_id,
        )
    )


def _normalize_sdd(value: Dict[str, Any], *, requirement: str) -> Dict[str, Any]:
    spec = _dict(value.get("spec"))
    design = _dict(value.get("design"))
    return _dump(
        ContractSdd(
            spec={
                "goal": str(spec.get("goal") or requirement),
                "users": _string_list(spec.get("users")),
                "scopeIn": _string_list(spec.get("scopeIn")),
                "scopeOut": _string_list(spec.get("scopeOut")),
                "acceptanceCriteria": _string_list(spec.get("acceptanceCriteria")),
            },
            design={
                "features": _string_list(design.get("features")),
                "sharedCapabilities": _string_list(design.get("sharedCapabilities")),
                "apiConventions": _string_list(design.get("apiConventions")),
                "dataModels": [_normalize_data_model(item, index) for index, item in enumerate(_list(design.get("dataModels")))],
                "permissions": _string_list(design.get("permissions")),
                "errorHandling": _string_list(design.get("errorHandling")),
            },
        )
    )


def _normalize_data_model(value: Any, index: int) -> Dict[str, str]:
    if isinstance(value, dict):
        return {"name": str(value.get("name") or f"Model{index + 1}"), "description": str(value.get("description") or "")}
    return {"name": str(value or f"Model{index + 1}"), "description": ""}


def _data_models_from_design(sdd: Dict[str, Any]) -> List[Dict[str, str]]:
    design = _dict(sdd.get("design"))
    return [_normalize_data_model(item, index) for index, item in enumerate(_list(design.get("dataModels")))]


def _api_contracts_from_features(features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    contracts: List[Dict[str, Any]] = []
    for feature in features:
        for api in _list(feature.get("apis")):
            if isinstance(api, dict):
                contracts.append(dict(api))
    return contracts


def _target_type(value: Any) -> TargetType:
    raw = str(value or "").strip().lower()
    if raw in {"frontend", "backend", "fullstack"}:
        return raw  # type: ignore[return-value]
    return "fullstack"


def _contract_status(value: Any) -> ContractStatus:
    raw = str(value or "").strip()
    if raw in {"draft", "ready", "executing", "verifying", "done", "blocked"}:
        return raw  # type: ignore[return-value]
    return "ready"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> List[str]:
    return [str(item) for item in _list(value) if item is not None and str(item).strip()]


def _dump(value: BaseModel) -> Dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True)
    return value.dict(by_alias=True)
