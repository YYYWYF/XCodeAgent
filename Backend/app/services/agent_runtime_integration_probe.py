"""验证前端能否通过注入地址直连 Agent Runtime 公开入口。

Direct 拓扑下 Agent Runtime 同时承担应用公开入口，前端不再经由 Java 后端或
网关访问 Agent。这条链路只有在前端真正发起一次 AG-UI 调用时才算被证明，因此
本模块按已确认 TechnicalPlan 声明的公开调用契约，用与前端构建期变量完全相同的
地址发起真实请求，并把结果作为预览就绪的必要证据。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

# AG-UI 运行生命周期首事件；收到它即证明公开入口已按 AG-UI 协议接受本次调用。
AG_UI_RUN_STARTED = "RUN_STARTED"
PROBE_TIMEOUT_SECONDS = 10.0
# 只读取事件流前半段即可判定协议是否成立，不等待模型把回复生成完。
_PROBE_EVENT_LINE_LIMIT = 40
# 认证终止点在 Runtime 侧生效时返回的拒绝状态；它同样证明路由可达。
_AUTH_REJECTION_STATUS = (401, 403)
# 公开入口没有该调用路径时返回的状态，说明调用契约与实际路由不一致。
_MISSING_ROUTE_STATUS = (404, 405)


def probe_direct_runtime_integration(
    *,
    runtime_url: str,
    frontend_origin: str | None,
    technical_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """按公开调用契约探测 Runtime 公开入口，返回可写入启动结果的证据。"""

    base_url = runtime_url.rstrip("/")
    paths = public_invocation_paths(technical_plan or {})
    evidence: dict[str, Any] = {
        "runtime_url": base_url,
        "frontend_origin": frontend_origin,
        "path": None,
        "cors": None,
        "protocol": None,
    }
    if not paths:
        return {
            **evidence,
            "passed": False,
            "message": "已确认 TechnicalPlan 未声明公开 AG-UI 调用路径，无法证明前端直连。",
        }
    path = paths[0]
    evidence["path"] = path
    target_url = f"{base_url}{path}"

    # 浏览器跨端口访问 Runtime 会先发 CORS 预检；预检不通过时前端必然失败，
    # 因此这里用真实前端 Origin 复现预检，而不是只探测 TCP 可达。
    cors = _probe_cors_preflight(target_url, frontend_origin)
    evidence["cors"] = cors
    if frontend_origin and not cors["passed"]:
        return {
            **evidence,
            "passed": False,
            "message": f"前端源 {frontend_origin} 的 CORS 预检未通过：{cors['message']}",
        }

    protocol = _probe_ag_ui_run(target_url, frontend_origin)
    evidence["protocol"] = protocol
    return {
        **evidence,
        "passed": protocol["passed"],
        "message": protocol["message"],
    }


def public_invocation_paths(plan: dict[str, Any]) -> tuple[str, ...]:
    """读取 TechnicalPlan 声明的公开 AG-UI 调用路径，保持声明顺序并去重。"""

    paths: list[str] = []
    for contract in plan.get("agent_contracts") or []:
        if not isinstance(contract, dict):
            continue
        invocation = contract.get("invocation")
        if not isinstance(invocation, dict) or invocation.get("exposure") != "public":
            continue
        path = str(invocation.get("path") or "").strip()
        if path and path not in paths:
            paths.append(path)
    return tuple(paths)


def _probe_cors_preflight(url: str, origin: str | None) -> dict[str, Any]:
    """用真实前端 Origin 复现浏览器预检，校验 Runtime 是否放行该源。"""

    if not origin:
        return {
            "passed": True,
            "skipped": True,
            "status": None,
            "allow_origin": None,
            "message": "未取得前端源，已跳过 CORS 预检。",
        }
    request = Request(url, method="OPTIONS")
    request.add_header("Origin", origin)
    request.add_header("Access-Control-Request-Method", "POST")
    request.add_header("Access-Control-Request-Headers", "content-type")
    try:
        with urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            allow_origin = response.headers.get("Access-Control-Allow-Origin")
            allowed = _origin_allowed(allow_origin, origin)
            return {
                "passed": allowed,
                "skipped": False,
                "status": int(getattr(response, "status", 0) or 0),
                "allow_origin": allow_origin,
                "message": (
                    f"预检通过（Access-Control-Allow-Origin: {allow_origin}）。"
                    if allowed
                    else "预检响应未回显当前前端源。"
                ),
            }
    except HTTPError as exc:
        allow_origin = exc.headers.get("Access-Control-Allow-Origin") if exc.headers else None
        allowed = _origin_allowed(allow_origin, origin)
        return {
            "passed": allowed,
            "skipped": False,
            "status": int(exc.code),
            "allow_origin": allow_origin,
            "message": (
                f"预检返回 {exc.code}，但已回显当前前端源。"
                if allowed
                else f"预检返回 {exc.code}。"
            ),
        }
    except (URLError, OSError, TimeoutError) as exc:
        return {
            "passed": False,
            "skipped": False,
            "status": None,
            "allow_origin": None,
            "message": f"预检请求失败：{exc}",
        }


def _probe_ag_ui_run(url: str, origin: str | None) -> dict[str, Any]:
    """发起真实 AG-UI 调用，校验公开入口按协议接受并开始本次运行。"""

    body = json.dumps(_run_input_payload()).encode("utf-8")
    request = Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "text/event-stream")
    if origin:
        request.add_header("Origin", origin)
    try:
        with urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 0) or 0)
            content_type = str(response.headers.get("Content-Type") or "")
            events = _read_sse_event_names(response)
    except HTTPError as exc:
        content_type = str(exc.headers.get("Content-Type") or "") if exc.headers else ""
        if exc.code in _AUTH_REJECTION_STATUS:
            return {
                "passed": True,
                "status": int(exc.code),
                "authenticated": True,
                "content_type": content_type,
                "events": [],
                "message": f"公开入口返回 {exc.code}，认证终止点已生效且调用路由可达。",
            }
        return {
            "passed": False,
            "status": int(exc.code),
            "authenticated": False,
            "content_type": content_type,
            "events": [],
            "message": (
                f"公开入口没有该调用路径（HTTP {exc.code}），调用契约与实际路由不一致。"
                if exc.code in _MISSING_ROUTE_STATUS
                else f"公开入口返回 HTTP {exc.code}。"
            ),
        }
    except (URLError, OSError, TimeoutError) as exc:
        return {
            "passed": False,
            "status": None,
            "authenticated": False,
            "content_type": "",
            "events": [],
            "message": f"公开入口请求失败：{exc}",
        }

    base = {
        "status": status,
        "authenticated": False,
        "content_type": content_type,
        "events": events,
    }
    if status != 200:
        return {**base, "passed": False, "message": f"公开入口返回 HTTP {status}。"}
    if "text/event-stream" not in content_type.lower():
        return {
            **base,
            "passed": False,
            "message": f"公开入口未按 AG-UI 返回事件流（Content-Type: {content_type or '未知'}）。",
        }
    if AG_UI_RUN_STARTED not in events:
        return {
            **base,
            "passed": False,
            "message": "公开入口返回了事件流，但未出现 AG-UI 运行开始事件。",
        }
    return {
        **base,
        "passed": True,
        "message": "公开入口已接受 AG-UI 调用并开始本次运行。",
    }


def _read_sse_event_names(response: Any) -> list[str]:
    """读取事件流前半段收集 AG-UI 事件名，收到运行开始事件即停止。"""

    names: list[str] = []
    for _ in range(_PROBE_EVENT_LINE_LIMIT):
        try:
            raw = response.readline()
        except (OSError, TimeoutError):
            break
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            names.append(line[len("event:") :].strip())
            continue
        if not line.startswith("data:"):
            continue
        payload_text = line[len("data:") :].strip()
        if not payload_text or payload_text == "[DONE]":
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        name = str(payload.get("type") or "").strip()
        if not name:
            continue
        names.append(name)
        if name == AG_UI_RUN_STARTED:
            break
    return names


def _run_input_payload() -> dict[str, Any]:
    """构造最小的 AG-UI RunAgentInput，只为证明协议入口可用。"""

    token = uuid4().hex
    return {
        "threadId": f"probe-{token}",
        "runId": f"probe-{token}",
        "state": {},
        "messages": [
            {"id": f"probe-{token}", "role": "user", "content": "integration probe"}
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def _origin_allowed(allow_origin: str | None, origin: str) -> bool:
    """判断预检响应头是否放行给定前端源。"""

    if not allow_origin:
        return False
    return allow_origin.strip() in {"*", origin}


__all__ = [
    "AG_UI_RUN_STARTED",
    "PROBE_TIMEOUT_SECONDS",
    "probe_direct_runtime_integration",
    "public_invocation_paths",
]
