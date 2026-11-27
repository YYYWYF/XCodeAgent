"""从当前正式接口和受管预览实例解析内部 API 调试目标。"""

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.application_lifecycle import ApplicationLifecycleStage
from app.services.api_design import ApiDesignError, endpoint_api_fields, find_endpoint
from app.services.api_design_schema import resolve_mapping_schema
from app.services.api_schema_refs import normalize_local_schema_ref
from app.services.application_lifecycle import read_application_lifecycle_snapshot
from app.services.development_artifacts import read_confirmed_technical_plan
from app.services.internal_api_debug_http import PATH_PARAMETER, HttpDebugRequest, execute_debug_http, expand_debug_path
from app.services.preview_runtime_state import runtime_snapshot, service_attempt_id, service_in_maintenance


class InternalDebugTarget(BaseModel):
    """按当前迭代和正式接口复合标识定位调试对象。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    iterationId: str = Field(min_length=1, max_length=512)
    apiContractId: str = Field(min_length=1, max_length=256)
    endpointId: str = Field(min_length=1, max_length=256)


class DescribeInternalDebug(InternalDebugTarget):
    """读取当前接口的请求定义。"""

    action: Literal["describe"]


class SendInternalDebug(InternalDebugTarget, HttpDebugRequest):
    """提交请求值，并核对用户看到的接口版本和运行实例。"""

    action: Literal["send"]
    attemptId: str = Field(min_length=1, max_length=512)
    expectedMethod: str = Field(min_length=1, max_length=12)
    expectedPath: str = Field(min_length=1, max_length=2048)


def _target(data: InternalDebugTarget) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """只接受已完成且与当前正式契约一致的接口。"""
    root = Path(data.workspaceRoot).expanduser().resolve()
    lifecycle = read_application_lifecycle_snapshot(root)
    if (lifecycle is None or lifecycle.initialization.stage != ApplicationLifecycleStage.READY_FOR_WORKBENCH
            or lifecycle.initialization.iteration_id != data.iterationId):
        raise ValueError("当前开发迭代已变化，请重新打开接口调试。")
    progress = lifecycle.development_artifacts.endpoints.get(data.apiContractId, {}).get(data.endpointId)
    if progress is None or progress.initial_development_status != "completed" or progress.change_status != "unchanged":
        raise ValueError("接口尚未按当前契约完成开发，请完成后再调试。")
    try:
        contract, endpoint = find_endpoint(read_confirmed_technical_plan(root), data.apiContractId, data.endpointId)
    except (ApiDesignError, FileNotFoundError):
        raise ValueError("已确认的接口定义未找到，请重新选择接口。") from None
    return root, contract, endpoint


def _address(endpoint: dict[str, Any]) -> tuple[str, str]:
    """从正式接口定义读取请求方式和路径。"""
    method = str(endpoint.get("method") or "").upper()
    path = endpoint.get("path")
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} or not isinstance(path, str):
        raise ValueError("当前接口的请求方式或路径无效。")
    return method, path


def _sample(schema: dict[str, Any]) -> Any:
    """用契约样例或按声明的类型生成可编辑的初始请求体。"""
    if "example" in schema:
        return schema["example"]
    if schema.get("type") == "object" or isinstance(schema.get("properties"), dict):
        return {name: _sample(child) for name, child in schema.get("properties", {}).items() if isinstance(child, dict)}
    kind = schema.get("type")
    return {"array": [], "string": "", "integer": 0, "number": 0, "boolean": False}.get(kind) if isinstance(kind, str) else None


def describe_internal_debug(data: DescribeInternalDebug) -> dict[str, Any]:
    """从已确认契约提取调试所需的请求参数与正文。"""
    _, contract, endpoint = _target(data)
    method, path = _address(endpoint)
    names = PATH_PARAMETER.findall(path)
    expand_debug_path(path, dict.fromkeys(names, "value"))
    fields = endpoint_api_fields(contract, endpoint)
    parameters = [
        {"location": field["location"], "name": field["path"], "type": field["type"], "required": field["required"]}
        for field in fields if field["side"] == "request" and field["location"] in {"path", "query", "header"}
    ]
    schemas = contract.get("schemas") or {}
    reference = normalize_local_schema_ref(endpoint.get("request_schema_ref") or "", contract_id=data.apiContractId)
    schema = schemas.get(reference)
    body = ""
    if isinstance(schema, dict):
        sample = json.dumps(_sample(resolve_mapping_schema(schema, schemas, frozenset())), ensure_ascii=False, indent=2)
        if len(sample) <= 262144:
            body = sample
    return {"method": method, "path": path, "pathNames": names, "parameters": parameters, "body": body}


def _resolve_runtime_target(data: SendInternalDebug) -> tuple[str, str]:
    """校验当前运行轮次和后端就绪状态，并绑定受管回环端口。"""
    root, _, endpoint = _target(data)
    method, path = _address(endpoint)
    if (method, path) != (data.expectedMethod, data.expectedPath):
        raise ValueError("接口定义已变化，请重新选择接口。")
    runtime = runtime_snapshot(root, logs=False)
    if service_attempt_id(runtime, "backend") != data.attemptId:
        raise ValueError("后端运行实例已变化，请等待刷新后再发送。")
    backend = runtime["backend"]
    port = backend.get("port")
    if (backend.get("status") != "running" or not isinstance(port, int) or not 0 < port <= 65535
            or service_in_maintenance(runtime.get("maintenance"), "backend")):
        raise ValueError("后端服务尚未就绪，请查看服务状态。")
    route = expand_debug_path(path, data.pathValues)
    return method, f"http://127.0.0.1:{port}{route}"


async def send_internal_debug(data: SendInternalDebug) -> dict[str, Any]:
    """仅向本轮受管的应用后端发出一次已登记的请求。"""
    method, url = await asyncio.to_thread(_resolve_runtime_target, data)
    return await execute_debug_http(method, url, data)
