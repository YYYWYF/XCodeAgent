"""只探测当前受管 Runtime 的健康状态，不发送业务或模型请求。"""

import socket
import time
import subprocess
import hashlib
from pathlib import Path

import httpx

from app.services.workspace_process_registry import workspace_process_registry
from app.utils.process_liveness import pid_is_running


def allocate_runtime_port() -> int:
    """申请 loopback 临时端口，绑定竞争由启动失败明确返回，不自动换端口重试。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def runtime_health(url: str, launch_id: str, *, database_required: bool, health_path: str = "/health",
                   expected_agent_ids: list[str] | None = None, profile: str = "preview",
                   allowed_agent_ids: list[str] | None = None, expected_application: Path | None = None,
                   expected_namespace: str | None = None) -> tuple[bool, str]:
    """校验本轮启动身份和模板健康合同，拒绝其他服务占用端口时误判成功。"""

    if not launch_id:
        return False, "Runtime 缺少当前启动身份，请重新启动服务。"
    try:
        with httpx.Client(timeout=1.5, trust_env=False, follow_redirects=False) as client:
            response = client.get(url.rstrip("/") + health_path)
        if response.status_code != 200:
            return False, f"Runtime /health 返回 HTTP {response.status_code}。"
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False, "Runtime /health 尚未返回有效响应。"
    if not isinstance(payload, dict) or payload.get("service") != "agent-runtime" or payload.get("launchId") != launch_id:
        return False, "Runtime 健康响应缺少当前 launchId 或服务身份，请同步模板启动合同。"
    checks = payload.get("checks")
    if payload.get("profile") != profile:
        return False, "Runtime 健康响应的运行 Profile 与本次启动不一致。"
    deployment = payload.get("deployment")
    if (not isinstance(deployment, dict) or type(deployment.get("workers")) is not int
            or deployment["workers"] != 1 or deployment.get("processLockHeld") is not True
            or deployment.get("acceptingRuns") is not True or deployment.get("profile") != profile
            or type(deployment.get("processId")) is not int or deployment["processId"] <= 0):
        return False, "Runtime 缺少有效的单 worker、进程锁或执行就绪事实。"
    for key, source in (("applicationSha256", str(expected_application.resolve()) if expected_application is not None else None),
                        ("namespaceSha256", expected_namespace)):
        value = deployment.get(key)
        if (not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                or source is not None and value != hashlib.sha256(source.encode()).hexdigest()):
            return False, "Runtime 启动事实的应用或命名空间与本次启动不一致。"
    if expected_agent_ids is not None:
        actual = payload.get("agentIds")
        if not isinstance(actual, list) or any(not isinstance(item, str) for item in actual) or len(set(actual)) != len(actual):
            return False, "Runtime 健康响应缺少有效的 Agent 注册集合。"
        if (allowed_agent_ids is None and set(actual) != set(expected_agent_ids)
                or allowed_agent_ids is not None and (not set(expected_agent_ids).issubset(actual) or set(actual) - set(allowed_agent_ids))):
            return False, "Runtime 实际注册的 Agent 与当前正式合同不一致。"
    if not isinstance(checks, dict):
        return False, "Runtime 健康响应缺少 checks。"
    expected = {"businessApi": "ready", "agUi": "ready", "deployment": "ready", "execution": "ready",
                "database": "ready" if database_required else "not_required"}
    # Debug 使用独立短期会话；普通预览和 Probe 仍必须证明匿名会话已就绪。
    if profile == "debug":
        expected.update(anonymousSession="not_required", debugSession="ready")
    else:
        expected["anonymousSession"] = "ready"
    failed = [key for key, value in expected.items() if checks.get(key) != value]
    if payload.get("status") != "ok" or failed:
        return False, "Runtime 就绪检查未通过：" + "、".join(failed or ["status"]) + "，请查看启动日志。"
    session_label = "调试会话" if profile == "debug" else "匿名会话"
    return True, f"Runtime 健康、业务路由、AG-UI、{session_label}及数据库检查通过。"


def wait_runtime_proxy_ready(url: str, launch_id: str, *, database_required: bool,
                             frontend_pid: int, runtime_pid: int, expected_agent_ids: list[str],
                             timeout_seconds: float = 20) -> tuple[bool, str]:
    """等待本轮前端代理就绪；只做有界健康探测，进程退出或取消立即停止，不重启服务。"""
    from app.services.run_workers import current_run_id

    deadline = time.monotonic() + timeout_seconds
    message = "前端代理尚未就绪。"
    while time.monotonic() < deadline:
        if workspace_process_registry.is_run_cancelled(current_run_id()):
            return False, "本次代理就绪检查已取消。"
        if not pid_is_running(frontend_pid):
            return False, "前端进程已退出，请查看前端启动日志。"
        if not pid_is_running(runtime_pid):
            return False, "Runtime 进程已退出，请查看 Runtime 启动日志。"
        ready, message = runtime_health(url, launch_id, database_required=database_required,
                                        health_path="/__runtime/health", expected_agent_ids=expected_agent_ids)
        if ready:
            return True, message
        time.sleep(0.3)
    return False, f"等待前端到 Runtime 的代理就绪超时（{timeout_seconds:g} 秒）：{message}"


def wait_runtime_ready(process: subprocess.Popen, url: str, launch_id: str, *, database_required: bool,
                       expected_agent_ids: list[str] | None = None, profile: str = "preview",
                       allowed_agent_ids: list[str] | None = None, expected_application: Path | None = None,
                       expected_namespace: str | None = None) -> tuple[bool, str, bool]:
    """在固定时间内等本轮进程稳定就绪，取消或提前退出立即返回，不重新启动。"""

    from app.services.run_workers import current_run_id

    deadline, stable_since = time.monotonic() + 60, None
    message = "Runtime 尚未就绪。"
    while time.monotonic() < deadline:
        if workspace_process_registry.is_run_cancelled(current_run_id()):
            return False, "本次 Runtime 启动已取消。", False
        if process.poll() is not None:
            return False, f"Runtime 提前退出，退出码 {process.returncode}，请查看完整启动日志。", False
        ready, message = runtime_health(url, launch_id, database_required=database_required,
                                       expected_agent_ids=expected_agent_ids, profile=profile, allowed_agent_ids=allowed_agent_ids,
                                       expected_application=expected_application, expected_namespace=expected_namespace)
        now = time.monotonic()
        stable_since = (stable_since if stable_since is not None else now) if ready else None
        if stable_since is not None and now - stable_since >= 2:
            return True, message, False
        time.sleep(0.3)
    return False, "Runtime 启动超时（60 秒）：" + message, True
