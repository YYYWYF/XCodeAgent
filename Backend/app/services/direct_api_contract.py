"""Direct Endpoint 的模型草稿、显式确认及 Build 只读投影。"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.api_contract_validation import validate_api_contract_definitions
from app.services.api_schema_refs import normalize_local_schema_ref
from app.services.artifact_invalidation import canonical_sha256
from app.services.direct_entity_design import _publish_text
from app.topologies.queries import serves_agent_runtime_public_edge
from app.utils.model_output import extract_json_object


_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class DirectEndpointRequest(BaseModel):
    """约束 Direct API 操作只能选中一个正式 Endpoint。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=128)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=128)


class DirectEndpointSaveRequest(DirectEndpointRequest):
    """保存用户编辑后的请求体和响应体 Schema。"""

    technical_plan_sha256: str = Field(alias="technicalPlanSha256", pattern=r"^[0-9a-f]{64}$")
    request_schema: dict[str, Any] | None = Field(alias="requestSchema")
    response_schema: dict[str, Any] = Field(alias="responseSchema")


def _source(request: DirectEndpointRequest) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """读取当前已确认 Direct 计划并唯一定位接口，不依赖实体完成状态。"""

    root = Path(request.workspace_root).expanduser().resolve()
    plan_path = root / ".xcodeagent/plans/technical-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("confirmation_status") != "confirmed":
        raise ValueError("TechnicalPlan 尚未确认。")
    if not serves_agent_runtime_public_edge(plan):
        raise ValueError("当前应用不是 agent_runtime_direct 拓扑。")
    if not _SAFE_ID.fullmatch(request.api_contract_id) or not _SAFE_ID.fullmatch(request.endpoint_id):
        raise ValueError("API Contract 或 Endpoint ID 不符合文件身份规则。")
    contracts = [item for item in plan.get("api_contracts", []) if isinstance(item, dict) and item.get("id") == request.api_contract_id]
    if len(contracts) != 1:
        raise ValueError("TechnicalPlan 无法唯一定位 API Contract。")
    endpoints = [item for item in contracts[0].get("endpoints", []) if isinstance(item, dict) and item.get("id") == request.endpoint_id]
    if len(endpoints) != 1:
        raise ValueError("TechnicalPlan 无法唯一定位 Endpoint。")
    return root, plan, contracts[0], endpoints[0], canonical_sha256(plan_path)


def _path(root: Path, contract_id: str, endpoint_id: str) -> Path:
    """按正式双重身份定位 Endpoint 契约，不将模型内容写入路径。"""

    return root / ".xcodeagent/plans/direct/endpoints" / contract_id / f"{endpoint_id}.json"


def _schema(value: Any, label: str, *, optional: bool = False) -> dict[str, Any] | None:
    """验证用户或模型给出的 JSON Schema 根形状。"""

    if value is None and optional:
        return None
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} 必须是非空 JSON Schema 对象。")
    root_type = value.get("type")
    if root_type not in ("object", "array") and not isinstance(value.get("$ref"), str):
        raise ValueError(f"{label} 根节点必须是 object、array 或本地 $ref。")
    return value


def _validate_schema_fields(value: Any, schemas: dict[str, Any], contract_id: str, label: str) -> None:
    """递归校验字段类型、容器形状及本契约 Schema 引用。"""

    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON Schema 对象。")
    allowed_types = {"object", "array", "string", "integer", "number", "boolean", "null"}
    field_type = value.get("type")
    types = field_type if isinstance(field_type, list) else [field_type]
    if field_type is not None and (
        not types or any(not isinstance(item, str) or item not in allowed_types for item in types)
    ):
        raise ValueError(f"{label} 的字段类型不受支持。")
    ref = value.get("$ref")
    if field_type is None and ref is None and not any(
        value.get(keyword) for keyword in ("allOf", "anyOf", "oneOf")
    ):
        raise ValueError(f"{label} 缺少字段类型或本契约 Schema 引用。")
    if ref is not None:
        if not isinstance(ref, str) or normalize_local_schema_ref(ref, contract_id=contract_id) not in schemas:
            raise ValueError(f"{label} 引用了不存在的本契约 Schema：{ref}。")
    properties = value.get("properties")
    if properties is not None:
        if "object" not in types or not isinstance(properties, dict):
            raise ValueError(f"{label}.properties 只能用于 object 且必须是对象。")
        for name, child in properties.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"{label} 存在空字段名。")
            _validate_schema_fields(child, schemas, contract_id, f"{label}.{name}")
    required = value.get("required")
    if required is not None:
        if "object" not in types or not isinstance(required, list) or any(
            not isinstance(name, str) or name not in (properties or {}) for name in required
        ) or len(required) != len(set(required)):
            raise ValueError(f"{label}.required 必须列出不重复的已定义字段。")
    items = value.get("items")
    if "array" in types:
        if items is None:
            raise ValueError(f"{label} 的数组字段缺少 items。")
        _validate_schema_fields(items, schemas, contract_id, f"{label}[]")
    elif items is not None:
        raise ValueError(f"{label}.items 只能用于 array。")
    additional = value.get("additionalProperties")
    if isinstance(additional, dict):
        if "object" not in types:
            raise ValueError(f"{label}.additionalProperties 只能用于 object。")
        _validate_schema_fields(additional, schemas, contract_id, f"{label}.additionalProperties")
    elif additional is not None and not isinstance(additional, bool):
        raise ValueError(f"{label}.additionalProperties 必须是布尔值或 Schema 对象。")
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = value.get(keyword)
        if branches is None:
            continue
        if not isinstance(branches, list) or not branches:
            raise ValueError(f"{label}.{keyword} 必须是非空 Schema 数组。")
        for index, branch in enumerate(branches):
            _validate_schema_fields(branch, schemas, contract_id, f"{label}.{keyword}[{index}]")


def _draft(contract: dict[str, Any], endpoint: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """用 TechnicalPlan 初始 Schema 预填，不把初始草稿误认作用户已确认。"""

    schemas = contract.get("schemas") if isinstance(contract.get("schemas"), dict) else {}
    request_ref = normalize_local_schema_ref(endpoint.get("request_schema_ref"), contract_id=str(contract.get("id") or ""))
    response_ref = normalize_local_schema_ref(endpoint.get("response_schema_ref"), contract_id=str(contract.get("id") or ""))
    request_schema = deepcopy(schemas.get(request_ref)) if request_ref else None
    response_schema = deepcopy(schemas.get(response_ref)) if response_ref else None
    if request_schema is not None and not isinstance(request_schema, dict):
        request_schema = None
    if not isinstance(response_schema, dict):
        response_schema = {"type": "object", "properties": {}}
    return request_schema, response_schema


def read_direct_endpoint_contract(request: DirectEndpointRequest) -> dict[str, Any]:
    """读取当前 Endpoint 草稿或有效确认版本；过期版本只作为待重审。"""

    root, _plan, contract, endpoint, plan_sha = _source(request)
    request_schema, response_schema = _draft(contract, endpoint)
    saved_path = _path(root, request.api_contract_id, request.endpoint_id)
    saved: dict[str, Any] | None = None
    if saved_path.is_file():
        value = json.loads(saved_path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            saved = value
    confirmed = bool(
        saved and saved.get("status") == "confirmed"
        and saved.get("basedOn") == {"technicalPlanSha256": plan_sha}
        and saved.get("apiContractId") == request.api_contract_id
        and saved.get("endpointId") == request.endpoint_id
        and isinstance(saved.get("responseSchema"), dict)
        and saved_path.with_suffix(".md").is_file()
    )
    if confirmed:
        request_schema = saved.get("requestSchema")
        response_schema = saved["responseSchema"]
    return {
        "apiContractId": request.api_contract_id,
        "endpointId": request.endpoint_id,
        "method": endpoint.get("method"),
        "path": endpoint.get("path"),
        "summary": endpoint.get("summary"),
        "entityIds": contract.get("entity_ids") or [],
        "requestSchema": request_schema,
        "responseSchema": response_schema,
        "technicalPlanSha256": plan_sha,
        "status": "confirmed" if confirmed else "stale" if saved else "pending",
    }


def generate_direct_endpoint_draft(request: DirectEndpointRequest) -> dict[str, Any]:
    """仅向模型发送当前 Endpoint 和正式 Entity 字段，生成可编辑但未确认的草稿。"""

    root, plan, contract, endpoint, _plan_sha = _source(request)
    del root
    related = [item for item in plan.get("entities", []) if isinstance(item, dict) and item.get("id") in (contract.get("entity_ids") or [])]
    previous = read_direct_endpoint_contract(request)
    prompt = (
        "你是 agent_runtime_direct 的 API 契约设计器。根据正式 Endpoint 语义和 Entity 字段，"
        "只返回一个 JSON 对象，包含 requestSchema（无请求体时为 null）和 responseSchema（非空 JSON Schema）。"
        "这两个 Schema 分别描述 HTTP 请求体与响应体；路径参数不放入请求体。"
        "不得要求绑定数据源，不得编造 Entity 字段；现有 Schema 仅作草稿参考。"
        "不要输出 Markdown。\n"
        f"Endpoint: {json.dumps(endpoint, ensure_ascii=False)}\n"
        f"Entities: {json.dumps(related, ensure_ascii=False)}\n"
        f"Existing draft: {json.dumps({'requestSchema': previous['requestSchema'], 'responseSchema': previous['responseSchema']}, ensure_ascii=False)}"
    )
    result = create_chat_model(Settings.from_env()).invoke(prompt)
    parsed = extract_json_object(_coerce_content_text(getattr(result, "content", result)))
    if not isinstance(parsed, dict):
        raise ValueError("模型没有返回可解析的 API 契约对象。")
    request_schema = _schema(parsed.get("requestSchema"), "请求体", optional=True)
    response_schema = _schema(parsed.get("responseSchema"), "响应体")
    return {**previous, "requestSchema": request_schema, "responseSchema": response_schema, "status": "pending"}


def _overlay_one(plan: dict[str, Any], saved: dict[str, Any]) -> None:
    """把确认契约投射为运行时 Schema，不改写正式 TechnicalPlan。"""

    contract_id = saved["apiContractId"]
    endpoint_id = saved["endpointId"]
    contract = next(item for item in plan["api_contracts"] if item.get("id") == contract_id)
    endpoint = next(item for item in contract["endpoints"] if item.get("id") == endpoint_id)
    schemas = contract.setdefault("schemas", {})
    safe_identity = re.sub(r"[^A-Za-z0-9_]", "_", f"{contract_id}_{endpoint_id}")
    request_schema = saved.get("requestSchema")
    if request_schema is None:
        endpoint["request_schema_ref"] = None
    else:
        request_name = f"Direct_{safe_identity}_Request"
        schemas[request_name] = request_schema
        endpoint["request_schema_ref"] = request_name
    response_name = f"Direct_{safe_identity}_Response"
    schemas[response_name] = saved["responseSchema"]
    endpoint["response_schema_ref"] = response_name


def save_direct_endpoint_contract(request: DirectEndpointSaveRequest) -> dict[str, Any]:
    """校验用户提交的当前版本，并保存为独立于实体 SQL 的确认契约。"""

    root, plan, contract, _endpoint, plan_sha = _source(request)
    if request.technical_plan_sha256 != plan_sha:
        raise ValueError("TechnicalPlan 已变化，请重新读取接口后保存。")
    request_schema = _schema(request.request_schema, "请求体", optional=True)
    response_schema = _schema(request.response_schema, "响应体")
    schemas = contract.get("schemas") if isinstance(contract.get("schemas"), dict) else {}
    if request_schema is not None:
        _validate_schema_fields(request_schema, schemas, request.api_contract_id, "请求体")
    _validate_schema_fields(response_schema, schemas, request.api_contract_id, "响应体")
    saved = {
        "artifactType": "direct-endpoint-contract",
        "status": "confirmed",
        "apiContractId": request.api_contract_id,
        "endpointId": request.endpoint_id,
        "basedOn": {"technicalPlanSha256": plan_sha},
        "requestSchema": request_schema,
        "responseSchema": response_schema,
    }
    projected = deepcopy(plan)
    _overlay_one(projected, saved)
    errors = validate_api_contract_definitions(projected)
    if errors:
        raise ValueError("API 契约校验失败：" + "；".join(errors))
    path = _path(root, request.api_contract_id, request.endpoint_id)
    markdown = (
        f"# API 契约 {request.api_contract_id}/{request.endpoint_id}\n\n"
        f"- TechnicalPlan SHA-256：`{plan_sha}`\n\n"
        f"## 请求体\n\n```json\n{json.dumps(request_schema, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 响应体\n\n```json\n{json.dumps(response_schema, ensure_ascii=False, indent=2)}\n```\n"
    )
    _publish_text(path.with_suffix(".md"), markdown)
    _publish_text(path, json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
    return read_direct_endpoint_contract(request)


def project_confirmed_direct_contracts(workspace: str | Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Build 仅装载与当前 TechnicalPlan 哈希一致的确认版本。"""

    if not serves_agent_runtime_public_edge(plan):
        return plan
    root = Path(workspace).expanduser().resolve()
    plan_sha = canonical_sha256(root / ".xcodeagent/plans/technical-plan.json")
    projected = deepcopy(plan)
    hashes: dict[str, str] = {}
    for contract in plan.get("api_contracts", []):
        for endpoint in contract.get("endpoints", []):
            path = _path(root, str(contract.get("id") or ""), str(endpoint.get("id") or ""))
            if not path.is_file() or not path.with_suffix(".md").is_file():
                continue
            saved = json.loads(path.read_text(encoding="utf-8"))
            if (
                saved.get("status") != "confirmed"
                or saved.get("basedOn") != {"technicalPlanSha256": plan_sha}
                or saved.get("apiContractId") != contract.get("id")
                or saved.get("endpointId") != endpoint.get("id")
                or not isinstance(saved.get("responseSchema"), dict)
            ):
                continue
            _overlay_one(projected, saved)
            hashes[f"{contract['id']}/{endpoint['id']}"] = sha256(path.read_bytes()).hexdigest()
    projected["directConfirmedApiContracts"] = hashes
    return projected


def direct_build_prerequisite_errors(
    workspace: str | Path, plan: dict[str, Any], scope: dict[str, str],
) -> list[str]:
    """Direct Build 只检查目标所需已确认契约及实体 SQL；不检查数据源绑定。"""

    if not serves_agent_runtime_public_edge(plan):
        return []
    target_type = str(scope.get("type") or "")
    target_id = str(scope.get("targetId") or "")
    contract_id = str(scope.get("apiContractId") or scope.get("api_contract_id") or "")
    contracts = [item for item in plan.get("api_contracts", []) if isinstance(item, dict)]
    needed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if target_type == "endpoint":
        needed = [
            (contract, endpoint) for contract in contracts
            for endpoint in contract.get("endpoints", [])
            if endpoint.get("id") == target_id and (not contract_id or contract.get("id") == contract_id)
        ]
    elif target_type == "application":
        needed = [
            (contract, endpoint) for contract in contracts
            for endpoint in contract.get("endpoints", [])
        ]
    elif target_type == "page":
        page = next((item for item in plan.get("page_implementation_contracts", []) if item.get("pageId") == target_id), {})
        endpoint_ids = set(page.get("requiredEndpointIds") or [])
        needed = [
            (contract, endpoint) for contract in contracts
            for endpoint in contract.get("endpoints", [])
            if endpoint.get("id") in endpoint_ids
        ]
    elif target_type == "agent":
        agent = next((item for item in plan.get("agent_contracts", []) if item.get("agentId") == target_id), {})
        settings = agent.get("agentSettings") if isinstance(agent.get("agentSettings"), dict) else {}
        tools = settings.get("tools") if isinstance(settings.get("tools"), dict) else {}
        bindings = tools.get("bindings") if isinstance(tools.get("bindings"), list) else []
        referenced: set[str] = set()
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
            source = binding.get("source") if isinstance(binding.get("source"), dict) else {}
            referenced.update(filter(None, [
                str(endpoint.get("apiContractId") or ""), str(source.get("serviceId") or ""),
            ]))
        needed = [
            (contract, endpoint) for contract in contracts if contract.get("id") in referenced
            for endpoint in contract.get("endpoints", [])
        ]
    errors: list[str] = []
    for contract, endpoint in needed:
        key = f"{contract['id']}/{endpoint['id']}"
        if key not in plan.get("directConfirmedApiContracts", {}):
            errors.append(f"Direct API 契约 {key} 尚未保存并确认；请到开发产物的接口详情页完成。")
    entity_ids = {
        str(entity_id) for contract, _endpoint in needed
        for entity_id in contract.get("entity_ids", []) if str(entity_id).strip()
    }
    from app.services.direct_entity_design import DirectEntityRequest, read_direct_entity_design

    for entity_id in sorted(entity_ids):
        try:
            confirmed = read_direct_entity_design(DirectEntityRequest(
                workspaceRoot=str(workspace), entityId=entity_id,
            ))["status"] == "confirmed"
        except (OSError, UnicodeError, ValueError):
            confirmed = False
        if not confirmed:
            errors.append(f"Direct 实体 {entity_id} 的 SQL 尚未在开发产物页确认。")
    return errors
