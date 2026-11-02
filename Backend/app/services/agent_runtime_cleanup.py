"""受控 Runtime 发出不可收口标记后只回收一次，不自动重新启动。"""

from collections.abc import Callable
from pathlib import Path
from threading import Lock

from app.services.agent_runtime_process import terminate_runtime_process


def runtime_cleanup_observer(root: Path, process, stderr_path: Path) -> Callable[[str], None]:
    """为本轮进程绑定故障观察器，不从日志接收 PID 或工作区等可伪造参数。"""

    guard = Lock()
    triggered = False

    def observe(line: str) -> None:
        """消费固定的非敏感故障标记，记录单次回收成功或具体失败。"""

        nonlocal triggered
        marker = "AGENT_RUNTIME_CLEANUP_EXHAUSTED"
        if line.strip() not in {marker, "CRITICAL:app.server.execution:" + marker}:
            return
        with guard:
            if triggered:
                return
            triggered = True
        try:
            terminate_runtime_process(root, process)
            message = "Runtime 清理超时，已回收当前进程；未自动重启。"
        except Exception as exc:
            # 进程回收异常不携带模型/数据库输入；不重复尝试或重新启动。
            message = f"Runtime 清理超时且进程回收失败：{type(exc).__name__}: {exc}"
        with stderr_path.open("a", encoding="utf-8") as output:
            output.write(message + "\n")

    return observe
