"""执行已审阅的正式设计转入；只写服务端重算的应用层差异并保留失败现场。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from app.services.agent_ui_dependency_install import _command, _tools, _write, save_record, target_lock
from app.services.agent_ui_dependency_plan import formal_snapshot, read_object, safe_path, sha256, target_root, target_snapshot
from app.services.agent_ui_dependency_evidence import stylesheet_evidence
from app.services.agent_ui_design_transfer_plan import _controls, _formal_state, prepare_transfer_plan
from app.services.agent_ui_design_project import source_digest, tree_digest
from app.services.agent_ui_transfer_records import mark_transfer, publish_current, read_transfer, transfer_directory
from app.services.agent_ui_transfer_tool import run_public_tool, tool_identity
from app.services.preview_runtime_state import redact
from app.services.workspace_process_registry import workspace_process_registry
from app.services.agent_ui_dependency_layout import validation_stages


def process_run_id(workspace: str | Path, run_id: str) -> str:
    """取消身份限定当前工作区与转入流程，避免停止无关命令。"""
    root, _ = target_root(workspace)
    return "agent-ui-transfer:" + sha256((str(root) + "\0" + run_id).encode())


def require_current_source(root: Path, plan: dict[str, Any]) -> None:
    """确认来源和正式文档在执行期间仍为当前版本，不能发布混合版本证据。"""
    _formal_state(root)
    source = safe_path(root, plan["source"]["projectRoot"])
    if (source_digest(source) != plan["source"]["sourceDigest"]
            or tree_digest(source / "dist") != plan["source"]["buildDigest"]
            or _controls(source) != plan["source"]["controls"] or tool_identity() != plan["tool"]):
        raise ValueError("转入期间设计来源、构建或公开工具发生变化，请重新确认并审阅。")


def validate_target(target: Path, plan: dict[str, Any], directory: Path, run_id: str) -> dict[str, Any]:
    """运行目标原管理器声明的静态检查；缺少类型或构建入口明确未验收。"""
    package = read_object(target / "package.json")
    scripts = package.get("scripts", {})
    stages = validation_stages(package)
    installation = read_object(safe_path(directory.parents[1], f"agent-ui-dependencies/{plan['installation']['planId']}/record.json"))
    command_plan = installation["plan"]
    tools = _tools(target, command_plan, run_id)
    commands = []
    before = target_snapshot(target)
    for stage in stages:
        if workspace_process_registry.is_run_cancelled(run_id):
            raise ValueError("当前转入已请求停止。")
        command = _command([*tools, "run", stage], target=target, plan=command_plan, run_id=run_id,
            directory=directory, stage=stage)
        commands.append(command)
        if command["returncode"] != 0:
            raise ValueError(f"目标 {stage} 失败；已转入但未验收，请核对 {stage}.log。")
        if target_snapshot(target) != before:
            raise ValueError("验证期间目标源码发生变化，不能发布转入成功。")
    return {"static": "passed", "commands": commands,
        "skipped": [{"stage": "test", "reason": "单元测试由开发流程的独立确认门管理"},
                    *[{"stage": name, "reason": "目标未声明此脚本"} for name in ("lint",) if name not in scripts]],
        "runtime": "not_run", "host": plan['hostIntegration']['status'],
        'connection': plan['hostIntegration'].get('connection', 'unavailable')}


def execute_transfer(workspace: str | Path, *, installation_plan_id: str, plan_id: str,
                     target_source_root: str, action: Literal["initialize", "confirm"],
                     thread_id: str, run_id: str, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """复算摘要、记录写入意图并逐文件 CAS；重复请求仅返回原记录，不重提任务。"""
    root, target = target_root(workspace)
    directory = transfer_directory(root, plan_id)
    lock = target_lock(root)
    if not lock.acquire(blocking=False):
        raise ValueError("目标已有依赖或转入任务执行，请读取当前任务。")
    record: dict[str, Any] = {}
    command_id = process_run_id(root, run_id)
    try:
        previous = read_transfer(root, plan_id)
        if previous:
            if previous.get("action") != action:
                raise ValueError("当前计划已用于另一动作，请重新审阅初始化后的差异。")
            return {**previous, **({"state": "unknown"} if previous.get("state") == "running" else {})}
        plan = prepare_transfer_plan(root, installation_plan_id=installation_plan_id, target_source_root=target_source_root)
        if plan["planId"] != plan_id:
            raise ValueError("转入计划过期，请重新审阅当前差异。")
        if plan["status"] != "reviewable":
            raise ValueError("转入存在冲突或阻断，保留目标定制，不能执行。")
        if (action == "initialize") != plan["initialization"]["required"]:
            raise ValueError("新目标必须先明确初始化，再重新审阅应用层差异；已有目标不能重初始化。")
        if workspace_process_registry.is_run_cancelled(command_id):
            raise ValueError("当前转入已请求停止。")
        record = {"planId": plan_id, "installationPlanId": installation_plan_id, "threadId": thread_id,
            "runId": run_id, "action": action, "state": "running", "plan": plan,
            "startedAt": datetime.now(UTC).isoformat(), "applied": [], "owned": {}, "mapping": [],
            "recordPath": f".devagentstudio/runtime/agent-ui-transfer/{plan_id}/record.json",
            "recovery": "before/ 保留覆盖前字节；按 plan 中 before/after hash 对账，仅恢复仍匹配本次 after 的文件。已有修改和依赖副作用不自动回滚。"}
        save_record(directory, record)
        mark_transfer(root, plan_id, True)
        require_current_source(root, plan)
        if progress:
            progress("initialize" if action == "initialize" else "copy")
        if action == "initialize":
            record["tool"] = run_public_tool("initialize", target, target_source_root,
                workspace=root, run_id=command_id, directory=directory, expected_identity=plan["tool"])
            after = target_snapshot(target)
            if any(after.get(name) != value for name, value in plan["protected"].items()):
                raise ValueError("初始化改变了既有文件，保留现场，不能声明初始化成功。")
            if any(name not in plan["protected"] and not name.startswith((target_source_root + "/", ".agent-ui/")) for name in after):
                raise ValueError("初始化创建了未授权目录中的文件，请保留现场对账。")
            record["owned"] = {name: value for name, value in after.items() if name not in plan["protected"]}
            record["state"] = "initialized"
            record["message"] = "公开工具初始化完成；请重新审阅默认应用层与已确认设计的差异，再确认转入。"
        else:
            expected = dict(plan["protected"])
            for item in plan["changes"]:
                if target_snapshot(target) != expected or formal_snapshot(root) != plan["formal"]:
                    raise ValueError("写入期间目标或正式规划变化，已有修改保留。")
                if workspace_process_registry.is_run_cancelled(command_id):
                    raise ValueError("当前转入已请求停止。")
                name = item["targetPath"]
                path = safe_path(target, name)
                if item["operation"] == "unchanged":
                    continue
                if path.is_file():
                    _write(safe_path(directory, "before/" + name), path.read_bytes())
                record["writing"] = name
                save_record(directory, record)
                _write(path, item["after"].encode())
                expected[name] = item["afterSha256"]
                record["applied"].append(name)
                record.pop("writing", None)
                save_record(directory, record)
            if progress:
                progress("composition")
            record["tool"] = run_public_tool("verify", target, target_source_root,
                workspace=root, run_id=command_id, directory=directory, expected_identity=plan["tool"])
            after = target_snapshot(target)
            allowed = {f"{target_source_root}/plugins/registry.generated.ts", f"{target_source_root}/composition-revision.generated.json"}
            if any(after.get(name) != value for name, value in expected.items() if name not in allowed):
                raise ValueError("公开工具改变了非生成文件，保留现场并拒绝成功。")
            if any(name not in expected and name not in allowed for name in after):
                raise ValueError("公开工具产生了未审阅文件，保留现场并拒绝成功。")
            if progress:
                progress("validation")
            record["validation"] = validate_target(target, plan, directory, command_id)
            if target_snapshot(target) != after:
                raise ValueError("验证期间目标发生变化，不能发布旧验证。")
            record["styles"] = stylesheet_evidence(target)
            record["owned"] = {item["targetPath"]: item["afterSha256"] for item in plan["changes"]}
            record["mapping"] = [{key: item[key] for key in ("sourcePath", "targetPath", "sourceSha256", "afterSha256")}
                for item in plan["changes"]]
            record["state"] = "verified"
            record["message"] = ("已转入确认应用层并通过组合与静态检查；"
                + ("已接入同源真实纯文本对话；完整业务连接与运行验收仍待完成。" if plan['hostIntegration'].get('connection') == 'plain_text'
                   else "Host 扩展已接线，真实 Runtime 映射待 T27，发送保持禁用。" if plan['hostIntegration']['status'] == 'reviewable'
                   else "目标缺少模板 Host 扩展入口，正式接线与运行验收仍未完成。"))
        if workspace_process_registry.is_run_cancelled(command_id) or formal_snapshot(root) != plan["formal"]:
            raise ValueError("已请求停止或正式规划变化，不能发布成功。")
        require_current_source(root, plan)
        record["after"] = target_snapshot(target)
        publish_current(root, record)
    except Exception as exc:
        if not record:
            raise
        record.update(state="stopped" if workspace_process_registry.is_run_cancelled(command_id) else "failed",
            message=redact(str(exc)))
        try:
            record["after"] = target_snapshot(target)
        except (OSError, ValueError):
            record["snapshotError"] = "目标路径冲突，请保留现场人工对账。"
    finally:
        try:
            if record:
                record["finishedAt"] = datetime.now(UTC).isoformat()
                save_record(directory, record)
        finally:
            mark_transfer(root, plan_id, False)
            lock.release()
    return record
