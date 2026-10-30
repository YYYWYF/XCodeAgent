"""从本轮 Python 诊断提取业务源码范围，环境或模板基础设施失败不交给修复 Agent。"""

from pathlib import Path
import re

from app.services.runtime_business_paths import runtime_business_path_error


def runtime_repair_file(root: Path, relative: str) -> bool:
    """只接受当前工作区既有、无链接的业务 Python 文件，不开放配置和模板根。"""

    if runtime_business_path_error(relative) or not relative.startswith("agent-runtime/src/app/"):
        return False
    target = root
    for part in Path(relative).parts:
        target = target / part
        if target.is_symlink():
            return False
    return target.is_file() and target.resolve().is_relative_to(root)


def runtime_repair_candidates(root: Path, logs: str) -> list[str]:
    """仅在已知源码异常的最后诊断帧命中可写业务文件时开放一次人工确认修复。"""

    exceptions = re.findall(r"(?m)^\s*(SyntaxError|IndentationError|TabError|NameError|AttributeError|TypeError):[^\n]*$", logs)
    frames = re.findall(r'File ["\']([^"\']+)["\'], line \d+', logs)
    if not exceptions or not frames:
        return []
    # 末帧位于依赖、受保护入口或工程外时，不让模型从上层栈猜测业务修复权限。
    candidate = Path(frames[-1])
    absolute = candidate if candidate.is_absolute() else root / "agent-runtime" / candidate
    try:
        relative = absolute.relative_to(root).as_posix()
    except ValueError:
        return []
    return [relative] if runtime_repair_file(root, relative) else []


def runtime_failure_scope(workspace: str | Path, record: dict, logs: dict) -> list[str]:
    """从当前 Runtime 启动或进程失败读取证据，不改变 Java 的失败分类。"""

    if record.get("failedStage") not in {"agent_runtime_start", "agent_runtime_process"}:
        return []
    parts = logs.get("agent_runtime") or []
    text = "\n".join(str(item.get("content") or item.get("text") or "") for item in parts if isinstance(item, dict))
    return runtime_repair_candidates(Path(workspace).resolve(), text)
