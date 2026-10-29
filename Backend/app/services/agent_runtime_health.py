"""只探测当前受管 Runtime 的健康状态，不发送业务或模型请求。"""

import socket
import time
import subprocess

import httpx

from app.services.workspace_process_registry import workspace_process_registry


def allocate_runtime_port() -> int:
    """申请 loopback 临时端口，绑定竞争由启动失败明确返回，不自动换端口重试。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def runtime_health(url: str, launch_id: str, *, database_required: bool, health_path: str = "/health") -> tuple[bool, str]:
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
    if not isinstance(checks, dict):
        return False, "Runtime 健康响应缺少 checks。"
    expected = {"businessApi": "ready", "agUi": "ready", "anonymousSession": "ready",
                "database": "ready" if database_required else "not_required"}
    failed = [key for key, value in expected.items() if checks.get(key) != value]
    if payload.get("status") != "ok" or failed:
        return False, "Runtime 就绪检查未通过：" + "、".join(failed or ["status"]) + "，请查看启动日志。"
    return True, "Runtime 健康、业务路由、AG-UI、匿名会话及数据库检查通过。"


def wait_runtime_ready(process: subprocess.Popen, url: str, launch_id: str, *, database_required: bool) -> tuple[bool, str, bool]:
    """在固定时间内等本轮进程稳定就绪，取消或提前退出立即返回，不重新启动。"""

    from app.services.run_workers import current_run_id

    deadline, stable_since = time.monotonic() + 60, None
    message = "Runtime 尚未就绪。"
    while time.monotonic() < deadline:
        if workspace_process_registry.is_run_cancelled(current_run_id()):
            return False, "本次 Runtime 启动已取消。", False
        if process.poll() is not None:
            return False, f"Runtime 提前退出，退出码 {process.returncode}，请查看完整启动日志。", False
        ready, message = runtime_health(url, launch_id, database_required=database_required)
        now = time.monotonic()
        stable_since = (stable_since if stable_since is not None else now) if ready else None
        if stable_since is not None and now - stable_since >= 2:
            return True, message, False
        time.sleep(0.3)
    return False, "Runtime 启动超时（60 秒）：" + message, True
