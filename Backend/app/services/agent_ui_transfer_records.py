"""转入任务事实与安装证据衔接，只认可受控操作记录的精确目标快照。"""

from pathlib import Path
import re
from threading import Lock
from typing import Any

from app.services.agent_ui_dependency_install import read_installation, record_directory as installation_directory, save_record
from app.services.agent_ui_dependency_evidence import installed_evidence, stylesheet_evidence
from app.services.agent_ui_dependency_plan import _packages, formal_snapshot, read_object, safe_path, sha256, target_root, target_snapshot
from app.services.agent_ui_design_project import source_digest, tree_digest
from app.services.agent_ui_transfer_tool import tool_identity

_guard = Lock()
_active: set[tuple[str, str]] = set()


def mark_transfer(workspace: Path, plan_id: str, active: bool) -> None:
    """运行 owner 仅存在当前进程内，不把重启遗留日志误当正在执行。"""
    with _guard:
        key = (str(workspace), plan_id)
        if active:
            _active.add(key)
        else:
            _active.discard(key)


def transfer_directory(workspace: str | Path, plan_id: str) -> Path:
    """任务目录只接受服务端摘要，不接受用户路径。"""
    root, _ = target_root(workspace)
    if not re.fullmatch(r"[a-f0-9]{64}", plan_id):
        raise ValueError("转入计划身份无效。")
    return safe_path(root, f".devagentstudio/runtime/agent-ui-transfer/{plan_id}")


def read_transfer(workspace: str | Path, plan_id: str) -> dict[str, Any]:
    """读取任务的实际文件事实；重启遗留运行态为未知，不能自动重放。"""
    directory = transfer_directory(workspace, plan_id)
    path = safe_path(directory, "record.json")
    if not path.exists():
        return {}
    record = read_object(path)
    root, target = target_root(workspace)
    with _guard:
        active = (str(root), plan_id) in _active
    if record.get("state") == "running" and not active:
        return {**record, "state": "unknown", "message": "原转入中断，结果未知，请先核对备份和当前文件，不能自动重放。"}
    if record.get("state") in {"initialized", "verified"}:
        try:
            plan = record["plan"]
            source = safe_path(root, plan["source"]["projectRoot"])
            installation = read_object(safe_path(installation_directory(root, record["installationPlanId"]), "record.json"))
            if (target_snapshot(target) != record.get("after") or formal_snapshot(root) != plan["formal"]
                    or source_digest(source) != plan["source"]["sourceDigest"]
                    or tree_digest(source / "dist") != plan["source"]["buildDigest"]
                    or any(sha256(safe_path(source, name).read_bytes()) != value for name, value in plan["source"]["controls"].items())
                    or tool_identity() != plan["tool"]
                    or installed_evidence(target, installation["plan"]) != installation["installation"]
                    or stylesheet_evidence(target) != record.get("styles", installation["styles"])):
                raise ValueError("身份变化")
        except (OSError, ValueError, KeyError):
            return {**record, "state": "stale", "message": "设计、目标、安装或规划已变化，原验证已过期；已有文件保留。"}
    return record


def current_transfer(workspace: str | Path, installation_id: str) -> dict[str, Any]:
    """只使用当前指针且源码完全匹配的成功任务，失败/未知不能延续写权限。"""
    root, _ = target_root(workspace)
    path = safe_path(root, ".devagentstudio/runtime/agent-ui-transfer/current.json")
    if not path.is_file():
        return {}
    record = read_transfer(root, read_object(path).get("planId", ""))
    if (record.get("state") not in {"initialized", "verified"}
            or record.get("installationPlanId") != installation_id):
        return {}
    return record


def transfer_ownership(workspace: str | Path, source_root: str) -> dict[str, str]:
    """新规划/安装不抹去逐文件归属，但后续用户修改的文件必须作为冲突保留。"""
    root, target = target_root(workspace)
    pointer = safe_path(root, ".devagentstudio/runtime/agent-ui-transfer/current.json")
    if not pointer.is_file():
        return {}
    path = safe_path(transfer_directory(root, read_object(pointer).get("planId", "")), "record.json")
    record = read_object(path)
    if (record.get("state") not in {"initialized", "verified"}
            or record["plan"]["target"]["sourceRoot"] != source_root):
        return {}
    current = target_snapshot(target)
    return {name: value for name, value in record["owned"].items() if current.get(name) == value}


def transfer_installation(workspace: str | Path, installation_id: str) -> dict[str, Any]:
    """工具合法改变源码后，重新校验原包/锁/规划及当前样式，不放宽人工修改检查。"""
    record = read_installation(workspace, installation_id)
    if record.get("state") == "verified":
        return record
    transfer = current_transfer(workspace, installation_id)
    if not transfer:
        return record
    root, target = target_root(workspace)
    original = read_object(safe_path(installation_directory(root, installation_id), "record.json"))
    plan = original["plan"]
    if (original.get("state") != "verified" or formal_snapshot(root) != plan["formal"]
            or _packages(target, plan["source"]) != plan["packages"]
            or installed_evidence(target, plan) != original["installation"]
            or stylesheet_evidence(target) != transfer.get("styles", original["styles"])):
        raise ValueError("转入后的安装、规划或样式证据已变化，请重新验证。")
    return {**original, "state": "verified", "styles": transfer.get("styles", original["styles"])}


def publish_current(workspace: Path, record: dict[str, Any]) -> None:
    """成功记录先完整落盘，再发布当前指针；不改变正式设计或安装任务事实。"""
    directory = transfer_directory(workspace, record["planId"])
    save_record(directory, record)
    # save_record 的固定文件名用于任务；当前指针同样以原子写入发布。
    from app.services.agent_ui_dependency_install import _write
    import json
    _write(safe_path(directory.parent, "current.json"), json.dumps({"planId": record["planId"]}).encode())


def select_installation(workspace: str | Path) -> str:
    """从当前有效任务中选择最近安装凭据，界面无需用户抄写内部摘要。"""
    root, _ = target_root(workspace)
    directory = safe_path(root, ".devagentstudio/runtime/agent-ui-dependencies")
    candidates: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in directory.iterdir():
            if re.fullmatch(r"[a-f0-9]{64}", path.name):
                record = transfer_installation(root, path.name)
                if record.get("state") == "verified":
                    candidates.append(record)
    if not candidates:
        raise ValueError("请先完成目标工程的依赖与样式准备；当前没有有效安装凭据。")
    return max(candidates, key=lambda item: item.get("finishedAt", ""))["planId"]
