"""通过既有 AG-UI 工作台代理正式 JSON 业务接口，凭据只保存在受管内存会话。"""

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.backend_process_registry import backend_launch_lock
from app.services.application_lifecycle import load_application_lifecycle
from app.services.python_runtime_checks import runtime_check_plan, runtime_source_fingerprint
from app.services.runtime_agent_contracts import agent_contract_index
from app.services.runtime_debug_endpoints import debug_endpoints

MAX_BYTES = 512 * 1024
RESERVED_HEADERS = {"authorization", "proxy-authorization", "cookie", "host", "content-length", "content-type", "accept", "connection", "transfer-encoding"}


class DebugRequest(BaseModel):
    """只接受正式接口身份和业务参数，禁止客户端提供地址或覆盖平台调试凭据。"""
    model_config = ConfigDict(extra="forbid")
    action: str = "request_debug"
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    agentId: str = Field(min_length=1, max_length=128)
    launchId: str = Field(min_length=1, max_length=128)
    apiContractId: str = Field(min_length=1, max_length=256)
    endpointId: str = Field(min_length=1, max_length=256)
    pathParameters: dict[str, str] = Field(default_factory=dict, max_length=100)
    query: dict[str, str] = Field(default_factory=dict, max_length=100)
    headers: dict[str, str] = Field(default_factory=dict, max_length=50)
    body: dict | list | str | int | float | bool | None = None

    @model_validator(mode="after")
    def validate_values(self):
        """在发送前拒绝凭据覆盖、Header 注入及超出当前面板预算的请求。"""
        if self.action != "request_debug":
            raise ValueError("不是接口调试请求。")
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > MAX_BYTES:
            raise ValueError("调试请求超过 512 KB。")
        for name, value in self.headers.items():
            if name.lower() in RESERVED_HEADERS or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or "\r" in value or "\n" in value:
                raise ValueError("请求头无效或试图覆盖平台管理的 Header。")
        return self


def execute_debug_request(request: DebugRequest, *, thread_id: str) -> dict:
    """一次性执行已确认的 JSON 调用入口；HTTP 失败原样展示，不重试业务写入。"""
    from app.services.agent_runtime_workbench import _guard, _sessions

    root = Path(request.workspaceRoot).expanduser().resolve()
    with backend_launch_lock(root), _guard:
        session = _sessions.get(str(root))
        if (session is None or session.owner != thread_id or session.launch_id != request.launchId
                or time.time() >= session.expires_at or request.agentId not in session.agent_ids):
            raise ValueError("调试会话已失效或不属于当前窗口，请重新同步状态。")
        if session.source_fingerprint != runtime_source_fingerprint(root):
            raise ValueError("代码或配置已变化，请停止并重新启动调试。")
        plan = runtime_check_plan(root, {})
        agent = agent_contract_index(plan).get(request.agentId) if plan else None
        lifecycle = load_application_lifecycle(root)
        targets = debug_endpoints(plan, agent, lifecycle.development_artifacts if lifecycle else None) if agent else []
        target = next((item for item in targets if (item["apiContractId"], item["endpointId"])
                       == (request.apiContractId, request.endpointId)), None)
        if not target or target["responseMode"] != "json":
            raise ValueError("目标不是当前智能体已完成开发且无变更的正式 JSON 调用接口。")
        origin, token = session.url, session.token
    path = target["path"]
    names = re.findall(r"\{([^}:]+)(?::[^}]+)?\}", path)
    if set(request.pathParameters) != set(names) or any(not request.pathParameters[name] for name in names):
        raise ValueError("请按接口契约填写全部路径参数。")
    for name in names:
        path = re.sub(r"\{" + re.escape(name) + r"(?::[^}]+)?\}", lambda _: quote(request.pathParameters[name], safe=""), path)
    query_names = {item["name"] for item in target["parameters"] if item.get("in") == "query"}
    if set(request.query) - query_names:
        raise ValueError("查询参数不在当前接口契约中。")
    if target["method"] in {"GET", "HEAD"} and request.body is not None:
        raise ValueError("GET/HEAD 调试请使用查询参数，不发送请求体。")
    started = time.monotonic()
    # 禁止重定向和系统代理，目标始终来自当前受管 Runtime 的正式路由。
    try:
        with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
            with client.stream(target["method"], origin.rstrip("/") + path, params=request.query,
                    headers={**request.headers, "Authorization": f"Bearer {token}", "Accept": "application/json"},
                    **({"json": request.body} if request.body is not None else {})) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("响应超过 512 KB，已停止接收；业务结果可能未知，请先核对，勿重复提交。")
                    chunks.append(chunk)
                content = b"".join(chunks).decode("utf-8", errors="replace")
                headers = {key: value for key, value in response.headers.items()
                           if key.lower() not in {"set-cookie", "authorization", "proxy-authenticate"}}
                return {"statusCode": response.status_code, "elapsedMs": round((time.monotonic() - started) * 1000),
                    "headers": headers, "body": content,
                    "protocolMismatch": "json" not in response.headers.get("content-type", "").lower()}
    except httpx.HTTPError as exc:
        raise ValueError("调试请求未收到完整响应，业务结果可能未知；请核对后再操作，不会自动重试。") from exc
