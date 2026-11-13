"""受控执行已确认的目标依赖计划，保留可核对的部分完成与恢复资料。"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from threading import Lock
from typing import Any, Callable

from app.services.agent_ui_dependency_plan import (
    _packages, formal_snapshot, prepare_dependency_plan, read_object, safe_path, sha256, target_root, target_snapshot,
)
from app.services.agent_ui_dependency_evidence import installed_evidence, stylesheet_evidence
from app.services.agent_ui_design_project import command_environment
from app.services.agent_ui_tool_environment import _javascript_entry, resolve_design_tools, resolve_node_executable
from app.services.preview_runtime_state import redact
from app.services.workspace_process_registry import workspace_process_registry

_guard = Lock()
_target_locks: dict[str, Lock] = {}


def process_run_id(workspace: str | Path, run_id: str) -> str:
    """命令身份限定当前产品与工作区，不能取消其他产品复用的外部 runId。"""
    root, _ = target_root(workspace)
    return "agent-ui-dependencies:" + sha256((str(root) + "\0" + run_id).encode())


def target_lock(workspace: str | Path) -> Lock:
    """仅串行同一 frontend 依赖写入，不给普通会话增加应用级锁。"""
    root, _ = target_root(workspace)
    with _guard:
        return _target_locks.setdefault(str(root / "frontend/package.json"), Lock())


def record_directory(workspace: str | Path, plan_id: str) -> Path:
    """按服务端计划摘要定位恢复资料，拒绝链接与任意文件名。"""
    root, _ = target_root(workspace)
    if not re.fullmatch(r"[a-f0-9]{64}", plan_id):
        raise ValueError("安装计划身份无效。")
    return safe_path(root, f".devagentstudio/runtime/agent-ui-dependencies/{plan_id}")


def _write(path: Path, content: bytes) -> None:
    """以同目录临时文件原子发布当前资料，不跟随目标链接。"""
    if path.is_symlink():
        raise ValueError("写入目标不能为链接。")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_record(directory: Path, record: dict[str, Any]) -> None:
    """原子持久化运行事实，不更新应用配置或正式设计确认。"""
    _write(safe_path(directory, "record.json"), json.dumps(record, ensure_ascii=False, indent=2).encode())


def read_installation(workspace: str | Path, plan_id: str) -> dict[str, Any]:
    """读取原任务事实；孤儿 running 返回未知，绝不自动重放安装。"""
    directory = record_directory(workspace, plan_id)
    path = safe_path(directory, "record.json")
    if not path.exists():
        return {}
    record = read_object(path)
    if record.get("state") == "running" and not target_lock(workspace).locked():
        return {**record, "state": "unknown", "message": "原执行已中断，请核对恢复资料并重新制定计划，不能自动重放。"}
    if record.get("state") == "verified":
        try:
            root, target = target_root(workspace)
            plan = record["plan"]
            if (target_snapshot(target) != record.get("after") or formal_snapshot(root) != plan["formal"]
                    or _packages(target, plan["source"]) != plan["packages"]
                    or installed_evidence(target, plan) != record.get("installation")
                    or stylesheet_evidence(target, output_dir=plan["outputDir"]) != record.get("styles")):
                raise ValueError("身份变化")
        except (OSError, ValueError, KeyError):
            return {**record, "state": "stale", "message": "目标源码、规划、安装或样式证据已变化，请重新计划和验证。"}
    return record


def _tools(target: Path, plan: dict[str, Any], run_id: str) -> list[str]:
    """解析真实 Node/管理器 JS 入口；Windows 不通过 shell 调用 cmd。"""
    if plan["manager"] == "pnpm" and plan["managerVersion"]:
        node, cli = resolve_design_tools(Path(plan["workspace"]), target / "package.json", run_id)
        return [str(node), str(cli)]
    node = resolve_node_executable()
    manager = plan["manager"]
    cli = _javascript_entry(shutil.which(manager), manager, "bin/npm-cli.js" if manager == "npm" else "bin/pnpm.cjs")
    if cli is None:
        raise ValueError("未找到目标管理器的 Node/JavaScript 入口。")
    result = workspace_process_registry.run([str(node), str(cli), "--version"], workspace=plan["workspace"],
        run_id=run_id, cwd=target, env=command_environment(), capture_output=True, text=True, timeout=10)
    if result.returncode or (plan["managerVersion"] and result.stdout.strip() != plan["managerVersion"]):
        raise ValueError("实际包管理器版本与目标声明不一致。")
    return [str(node), str(cli)]


def _command(argv: list[str], *, target: Path, plan: dict[str, Any], run_id: str,
             directory: Path, stage: str) -> dict[str, Any]:
    """无 shell 执行已审阅命令，日志有界脱敏并沿原进程 owner 响应取消。"""
    code: int | None = None
    error = ""
    started_ns = time.time_ns()
    environment = {**command_environment(), "COREPACK_ENABLE_AUTO_PIN": "0"}
    with tempfile.TemporaryFile() as output:
        try:
            result = workspace_process_registry.run(argv, workspace=plan["workspace"], run_id=run_id,
                cwd=target, env=environment, stdout=output, stderr=subprocess.STDOUT, timeout=240, check=False)
            code = result.returncode
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            error = type(exc).__name__
        output.seek(0, 2)
        size = output.tell()
        output.seek(max(0, size - 65536))
        log = redact(output.read().decode("utf-8", errors="replace"))
    _write(safe_path(directory, f"{stage}.log"), log.encode())
    return {"stage": stage, "argv": argv, "returncode": code, "error": error,
            "log": f"{stage}.log", "truncated": size > 65536,
            "peerWarnings": any(marker in log.lower() for marker in ("unmet peer", "issues with peer dependencies found")),
            "startedNs": started_ns}


def _require_unchanged(target: Path, expected: dict[str, str], lockfile: str) -> dict[str, str]:
    """安装只能改已批准文件及本管理器锁，其他变化保留并报告冲突。"""
    current = target_snapshot(target)
    if {key: value for key, value in current.items() if key != lockfile} != {
            key: value for key, value in expected.items() if key != lockfile}:
        raise ValueError("执行期间目标源码或保护配置变化，已有修改保留，请重新计划。")
    return current


def execute_dependency_plan(workspace: str | Path, *, plan_id: str, source: str, entry: str,
                            thread_id: str, run_id: str,
                            review_id: str = "",
                            progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """复算批准摘要后安装并验证；重复/未知请求只返回事实，失败不伪造回滚。"""
    root, target = target_root(workspace)
    command_run_id = process_run_id(root, run_id)
    directory = record_directory(root, plan_id)
    lock = target_lock(root)
    if not lock.acquire(blocking=False):
        raise ValueError("当前目标依赖任务仍在执行，请读取原任务状态。")
    record: dict[str, Any] = {}
    try:
        previous = read_installation(root, plan_id)
        if previous:
            if previous.get("state") == "running":
                return {**previous, "state": "unknown", "message": "原任务结果未知，请先对账，不能重复执行。"}
            return previous
        plan = prepare_dependency_plan(root, source=source, entry=entry, review_id=review_id)
        if plan["planId"] != plan_id:
            raise ValueError("安装计划过期：源码、包、规划或保护摘要已变化，请重新审阅。")
        if workspace_process_registry.is_run_cancelled(command_run_id):
            raise ValueError("当前执行已请求停止。")
        record = {"planId": plan_id, "threadId": thread_id, "runId": run_id, "state": "running",
                  "startedAt": datetime.now(UTC).isoformat(), "plan": plan, "applied": [], "commands": [],
                  "recovery": plan["recovery"]}
        directory.mkdir(parents=True, exist_ok=True)
        save_record(directory, record)
        tools = _tools(target, plan, command_run_id)
        record["tools"] = {"nodeSha256": sha256(Path(tools[0]).read_bytes()),
                           "managerSha256": sha256(Path(tools[1]).read_bytes())}
        # 工具准备可能耗时，提交前再次复验全部源码及发布物，而不是仅复验 package.json。
        if prepare_dependency_plan(root, source=source, entry=entry, review_id=review_id)["planId"] != plan_id:
            raise ValueError("工具准备期间计划已变化，请重新审阅。")
        lock_path = safe_path(target, plan["lockfile"])
        if lock_path.is_file():
            _write(safe_path(directory, "before/" + plan["lockfile"]), lock_path.read_bytes())
        for item in plan["changes"]:
            path = safe_path(target, item["path"])
            actual = sha256(path.read_bytes()) if path.is_file() else None
            if actual != item["beforeSha256"]:
                raise ValueError("写入前目标发生冲突，请重新计划。")
            if path.is_file():
                _write(safe_path(directory, "before/" + item["path"]), path.read_bytes())
            # 先写意图日志，中断后可按 before/after hash 对账，不依赖最后一句成功回执。
            record["writing"] = item["path"]
            save_record(directory, record)
            if workspace_process_registry.is_run_cancelled(command_run_id):
                raise ValueError("当前执行已请求停止。")
            _write(path, item["after"].encode())
            record["applied"].append(item["path"])
            record.pop("writing", None)
            save_record(directory, record)
        expected = {**plan["before"], **{item["path"]: item["afterSha256"] for item in plan["changes"]}}
        if target_snapshot(target) != expected:
            raise ValueError("安装前源码或锁文件变化，请重新审阅。")
        for stage in ("install", *plan["checks"]):
            if workspace_process_registry.is_run_cancelled(command_run_id):
                raise ValueError("当前执行已请求停止。")
            if progress:
                progress(stage)
            record["stage"] = stage
            save_record(directory, record)
            if formal_snapshot(root) != plan["formal"]:
                raise ValueError("正式规划已变化，请重新审阅安装计划。")
            args = ["install", "--no-frozen-lockfile"] if plan["manager"] == "pnpm" else ["install", "--no-audit", "--no-fund"]
            argv = [*tools, *(args if stage == "install" else ["run", stage])]
            result = _command(argv, target=target, plan=plan, run_id=command_run_id, directory=directory, stage=stage)
            record["commands"].append(result)
            save_record(directory, record)
            if workspace_process_registry.is_run_cancelled(command_run_id):
                raise ValueError("当前执行已请求停止。")
            if result["returncode"] != 0:
                raise ValueError(f"目标 {stage} 失败，已保留命令日志和实际文件，请重新制定计划。")
            _require_unchanged(target, expected, plan["lockfile"])
            if stage == "install":
                record["installation"] = installed_evidence(target, plan)
        build = next(item for item in record["commands"] if item["stage"] == "build")
        record["styles"] = stylesheet_evidence(target, output_dir=plan["outputDir"],
                                               built_after_ns=build.get("startedNs", 0))
        record["after"] = _require_unchanged(target, expected, plan["lockfile"])
        if formal_snapshot(root) != plan["formal"] or _packages(target, source) != plan["packages"]:
            raise ValueError("验证期间正式规划变化，请重新计划。")
        record["state"] = "verified"
        record["message"] = "依赖、锁文件、类型及样式构建已验证；设计转入与真实业务连接尚待后续任务。"
        if any(command.get("peerWarnings") for command in record["commands"]):
            record["message"] += "包管理器报告 peer 兼容警告，详见安装日志；本结果不声明完整运行兼容性。"
    except Exception as exc:
        if not record:
            raise
        record.update(state="stopped" if workspace_process_registry.is_run_cancelled(command_run_id) else "failed",
                      message=redact(str(exc)))
        try:
            record["after"] = target_snapshot(target)
        except (OSError, ValueError):
            record["snapshotError"] = "目标存在路径冲突，请保留现场并人工对账。"
    finally:
        try:
            if record:
                record["finishedAt"] = datetime.now(UTC).isoformat()
                save_record(directory, record)
        finally:
            lock.release()
    return record
