"""Runtime 工作台只读正式合同及独立调试进程，不接管 Java 或正式规划确认。"""

from dataclasses import dataclass
from pathlib import Path
import json
import secrets
import time
from threading import RLock
from urllib.parse import urlparse

from app.services.agent_runtime_health import runtime_health
from app.services.agent_runtime_launcher import _launch_runtime_locked
from app.services.agent_runtime_process import stop_runtime
from app.services.backend_process_registry import backend_launch_lock
from app.services.python_runtime_checks import runtime_check_plan
from app.services.runtime_agent_contracts import agent_contract_index, agent_fact_digest


@dataclass(frozen=True)
class DebugSession:
    """仅在服务端内存保存非敏感会话身份；凭据只注入进程并一次交给发起者。"""

    owner: str
    launch_id: str
    url: str
    expires_at: int
    database_required: bool
    agent_ids: list[str]
    allowed_agent_ids: list[str]


_sessions: dict[str, DebugSession] = {}
_guard = RLock()


def runtime_agents(workspace: str) -> dict:
    """从当前磁盘已确认的 TechnicalPlan 投影七模块，不读取配置副本或推测拓扑。"""

    root = Path(workspace).expanduser().resolve()
    plan = runtime_check_plan(root, {})
    if plan is None:
        return {"agents": [], "runtimeEnabled": False}
    from app.services.runtime_agent_development import agent_development_payload

    return {"runtimeEnabled": True, "agents": [
        {**contract, "contractHash": agent_fact_digest(contract),
         "development": agent_development_payload(root, plan, contract["agentId"])}
        for contract in agent_contract_index(plan).values()
    ]}


def debug_origin(origin: str) -> str:
    """仅允许本机 Electron Renderer Origin，拒绝远端网页扩展调试 CORS。"""

    if origin == "null":
        return origin
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} \
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("调试入口必须来自本机 Electron Renderer。")
    return origin


def debug_snapshot(workspace: str) -> dict:
    """返回不含凭据的真实会话状态，过期后回收调试进程。"""

    root = Path(workspace).expanduser().resolve()
    with backend_launch_lock(root), _guard:
        current = _sessions.get(str(root))
        if current is None:
            # 平台重启后不恢复旧调试凭据；清理仍有受管 PID 的孤儿进程，要求显式重开。
            result = stop_runtime(root, profile="debug")
            if result.get("status") == "failed":
                raise ValueError(result["message"])
            return {"active": False}
        ready, _ = runtime_health(current.url, current.launch_id, database_required=current.database_required,
                                  expected_agent_ids=current.agent_ids, allowed_agent_ids=current.allowed_agent_ids,
                                  profile="debug")
        if not ready or time.time() >= current.expires_at:
            stop_debug(str(root), current.owner)
            return {"active": False}
        return {"active": True, "launchId": current.launch_id, "expiresAt": current.expires_at}


def start_debug(workspace: str, thread_id: str, origin: str, agent_id: str) -> tuple[dict, dict]:
    """显式操作才创建十分钟 Debug Profile，独立 PID/端口/Principal/checkpoint，凭据不落盘。"""

    root = Path(workspace).expanduser().resolve()
    origin = debug_origin(origin)
    with backend_launch_lock(root), _guard:
        contracts = {contract["agentId"] for contract in runtime_agents(str(root))["agents"]}
        if agent_id not in contracts:
            raise ValueError("只能调试当前已确认的业务 Agent。")
        existing = _sessions.get(str(root))
        if existing and existing.owner != thread_id and time.time() < existing.expires_at:
            raise ValueError("当前应用已有其他会话正在调试，请先结束原调试。")
        token, expires_at = secrets.token_urlsafe(48), int(time.time()) + 600
        result = _launch_runtime_locked(root, on_progress=None, settings=None, skip_install=True,
                                        debug={"token": token, "expiresAt": expires_at, "origins": json.dumps([origin])},
                                        target_agent_id=agent_id)
        if result.get("status") != "running":
            _sessions.pop(str(root), None)
            raise ValueError(str(result.get("message") or "调试 Runtime 启动失败。"))
        session = DebugSession(thread_id, result["launch_id"], result["preview_url"], expires_at,
                               result["database_required"], [agent_id], result["agent_ids"])
        _sessions[str(root)] = session
        # token 是随机能力凭据；绑定关系只存在当前进程，停止/重启后旧端口凭据无法复用。
        return ({"active": True, "launchId": session.launch_id, "expiresAt": expires_at},
                {"launchId": session.launch_id, "url": session.url, "token": token, "expiresAt": expires_at})


def stop_debug(workspace: str, thread_id: str) -> dict:
    """停止当前拥有者的调试进程，普通预览和 Java 不受影响。"""

    root = Path(workspace).expanduser().resolve()
    with backend_launch_lock(root), _guard:
        current = _sessions.get(str(root))
        if current is not None and current.owner != thread_id:
            raise ValueError("不能停止其他会话拥有的调试进程。")
        result = stop_runtime(root, profile="debug")
        if result.get("status") == "failed":
            raise ValueError(result["message"])
        _sessions.pop(str(root), None)
        return {"active": False}
