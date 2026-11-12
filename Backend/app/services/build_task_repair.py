"""单任务修复草稿生命周期：只读诊断、绑定证据、待用户明确确认。"""

import asyncio
from copy import deepcopy
from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.execution_recovery import DurableExecutionStatus
from app.services.build_task_retry import load_task_retry_source, _locks
from app.services.build_task_repair_contract import digest, source_files, task_view, compile_repaired_plan, related_inventory
from app.workspace.task_documents import (
    build_task_plan_lifecycle_lock, build_task_plan_json_path, build_task_plan_sha256,
    load_build_task_plan_json, write_json_atomic, load_pending_build_task_plan, _TASK_RUNTIME_FIELDS,
)


def repair_lock(request) -> asyncio.Lock:
    """修复与原任务重试共用执行级锁，避免交接期间同时更改准入。"""
    key = (str(Path(request.workspaceRoot).resolve()).casefold(), request.sourceRunId)
    return _locks.setdefault(key, asyncio.Lock())


def draft_path(request) -> Path:
    """用执行和任务身份的摘要定位唯一草稿，不将外部 ID 拼接为路径。"""
    key = digest([request.sourceRunId, request.taskId])
    return Path(request.workspaceRoot).resolve() / WORKSPACE_ARTIFACT_DIR / "drafts" / "task-repairs" / f"{key}.json"


def load_draft(request) -> dict | None:
    """读取当前格式并验证草稿自身摘要，不能信任客户端提供的任务正文。"""
    path = draft_path(request)
    if not path.is_file():
        return None
    draft = load_build_task_plan_json(path)
    actual = digest({key: value for key, value in draft.items() if key != "draftDigest"})
    if actual != draft.get("draftDigest"):
        raise ValueError("任务修复草稿摘要不一致，请重新生成。")
    if any(draft.get(key) != getattr(request, key) for key in ("sourceRunId", "sourceThreadId", "sessionId", "taskId")):
        raise ValueError("任务修复草稿不属于当前执行。")
    return draft


def save_draft(request, draft: dict) -> dict:
    """只保存修复草稿及其确认身份，不触碰业务代码或原正式 DAG。"""
    draft = {key: value for key, value in draft.items() if key != "draftDigest"}
    draft["draftDigest"] = digest(draft)
    write_json_atomic(draft_path(request), draft)
    return draft


def draft_view(draft: dict | None) -> dict:
    """输出可审阅差异和状态，不将内部任务或冻结设计放入消息。"""
    if not draft:
        return {"draft": None}
    return {"draft": {key: deepcopy(draft[key]) for key in (
        "status", "sourceRunId", "sourceThreadId", "sessionId", "taskId", "draftDigest",
        "reason", "before", "after", "feedback",
    )}}


async def load_repair_source(request):
    """只读取已停止派发的当前 Build，严格核对所有权、计划绑定和执行切片。"""
    from app.graph.workflow import workflow_graph_for_request
    from app.graph.subgraphs.build import _restore_build_runtime
    from app.services.checkpoint_execution import read_checkpoint_execution, execution_is_live
    from app.services.build_task_waiting import awaiting_build_task_retry
    from app.services.failed_run_cleanup import require_failed_run_drained
    from app.services.build_scheduler import resolve_execution_slice
    from app.services.artifact_freshness import task_plan_errors
    from app.workspace.task_documents import load_build_execution_record

    source = await load_task_retry_source(request)
    if execution_is_live(source):
        raise ValueError("Build 仍在运行，请等待在途任务结束后再修复任务。")
    if source.status in {DurableExecutionStatus.CANCELLED, DurableExecutionStatus.STOPPED}:
        raise ValueError("原执行已结束，不能修复历史任务。")
    graph = await workflow_graph_for_request(workspace=source.workspace, project_id=source.project_id)
    checkpoint = await read_checkpoint_execution(source, graph)
    waiting = bool(checkpoint and awaiting_build_task_retry(checkpoint.values))
    if (not checkpoint or checkpoint.node != "build"
            or checkpoint.status not in {DurableExecutionStatus.FAILED, DurableExecutionStatus.INTERRUPTED, DurableExecutionStatus.AWAITING_USER}
            or checkpoint.status is DurableExecutionStatus.AWAITING_USER and not waiting):
        raise ValueError("当前不在失败或等待处理的 Build，或存在必须先处理的确认。")
    await require_failed_run_drained(source.run_id)
    state = {**checkpoint.values, "workspace": source.workspace, "project_id": source.project_id}
    clarification = state.get("clarification") or {}
    if (clarification and clarification.get("mode") != "build_task_plan_generation_error"
            or (state.get("repair_task_plan") or {}).get("decision") == "requires_user_confirmation"):
        raise ValueError("请先处理当前 Build 确认，再修复任务。")
    if load_pending_build_task_plan(state) is not None:
        raise ValueError("存在待确认的任务计划，请先处理该计划。")
    plan = load_build_task_plan_json(build_task_plan_json_path(state))
    if plan.get("schema_version") != "build-dag.v4" or plan.get("confirmation_status") != "confirmed":
        raise ValueError("缺少当前已确认任务计划。")
    unit_validation = (plan.get("unit_graph") or {}).get("validation") or {}
    if unit_validation.get("is_valid") is not True or unit_validation.get("errors"):
        raise ValueError("Unit 骨架未通过校验，需要重新规划，不能通过单任务修复绕过。")
    record = load_build_execution_record(state, source.run_id)
    if record and record.get("progress"):
        state = _restore_build_runtime(state, source.run_id, record["progress"], retry_failed_tasks=False)
        # 正式文件可能只更新了状态；以已校验进度恢复全部任务，合同仍来自原确认计划。
        runtime = {task["id"]: task for task in state["tasks"]}
        state["task_repair_retry_count"] = int(runtime.get(request.taskId, {}).get("retry_count") or 0)
        plan["task_registry"] = {
            key: {**value, **{field: deepcopy(runtime[key][field]) for field in _TASK_RUNTIME_FIELDS if field in runtime[key]}}
            for key, value in plan["task_registry"].items()
        }
    else:
        # 校验阶段可能尚未派发任何任务：只接受完全无执行结果的原确认计划。
        if state.get("build_results") or any(task.get("status") in {"completed", "already_satisfied", "running"} for task in plan["task_registry"].values()):
            raise ValueError("缺少已提交 Build 进度，不能保留已有执行结果。")
        frozen = state.get("build_task_plan") or {}
        if build_task_plan_sha256(frozen) != build_task_plan_sha256(plan):
            raise ValueError("校验失败现场与当前正式计划不一致。")
    state["build_task_plan"] = plan
    state["tasks"] = list(plan["task_registry"].values())
    task = plan["task_registry"].get(request.taskId)
    selected = resolve_execution_slice(build_task_plan=plan, tasks=state["tasks"], build_execution_scope=state.get("build_execution_scope"))["task_ids"]
    errors = (plan.get("task_graph", {}).get("validation") or {}).get("errors", [])
    attributed = any(request.taskId in str(error) for error in errors)
    if (not task or task["id"] not in selected or task.get("owner") not in {"backend", "frontend"}
            or task.get("kind") == "repair" or task.get("execution_strategy", "agent") != "agent"
            or not (task.get("status") == "failed" or task.get("status") == "pending" and attributed)):
        raise ValueError("只能修复当前切片的失败正式任务，或有明确任务归因的校验阻断。")
    if stale := task_plan_errors(state, plan):
        raise ValueError("正式输入已变化，请重新规划任务：" + "、".join(stale))
    return source.model_copy(update={"status": checkpoint.status, "current_node": "build"}), graph, checkpoint, state, waiting


def validate_draft(request, state: dict, draft: dict) -> None:
    """确认前再次核验草稿、整个计划和所有诊断源码，过期结果不得执行。"""
    if draft.get("status") != "pending" or draft.get("draftDigest") != request.draftDigest:
        raise ValueError("该修复草稿已过期或已消费，请重新读取。")
    if draft["basePlanDigest"] != build_task_plan_sha256(state["build_task_plan"]):
        raise ValueError("正式任务计划已变化，请重新生成修复方案。")
    task = state["build_task_plan"]["task_registry"][request.taskId]
    _, signatures = source_files(state["workspace"], task, list(draft["sourceSignatures"]))
    if signatures != draft["sourceSignatures"]:
        raise ValueError("相关源码已变化，请重新生成修复方案。")


async def repair_action(request, progress) -> dict:
    """生成、读取或放弃一个任务草稿；每一轮新草稿都重新等待明确确认。"""
    from app.agents.build_task_repair import propose_task_repair
    from app.protocols.ag_ui_action_stream import AgUiActionProgress

    async with repair_lock(request):
        _, _, _, state, _ = await load_repair_source(request)
        existing = load_draft(request)
        if request.action == "get":
            return draft_view(existing if existing and existing["status"] == "pending" else None)
        with build_task_plan_lifecycle_lock(request.workspaceRoot):
            if request.action in {"discard", "regenerate"}:
                if not existing:
                    raise ValueError("任务修复草稿不存在。")
                if existing["status"] != "pending" or existing["draftDigest"] != request.draftDigest:
                    raise ValueError("任务修复草稿已过期。")
                save_draft(request, {**existing, "status": "discarded"})
                if request.action == "discard":
                    return {"draft": None}
            elif existing and existing["status"] == "pending":
                return draft_view(existing)
        task = state["build_task_plan"]["task_registry"][request.taskId]
        await progress(AgUiActionProgress(stage="diagnosing", message="正在读取失败任务和相关源码。", percent=10))
        dependencies = set(task.get("dependencies", []))
        paths = set(task.get("target_files", []))
        paths.update(path for item in state["tasks"] if item.get("id") in dependencies
                     and item.get("unit_id") == task.get("unit_id") for path in item.get("target_files", []))
        paths.update(task.get("source_refs", {}).get("foundation_paths", []))
        texts, signatures = await asyncio.to_thread(source_files, state["workspace"], task, sorted(paths))
        context = {**deepcopy(state.get("build_context") or {}), "project_plan": state.get("project_plan") or {}}
        inventory = await asyncio.to_thread(related_inventory, state["workspace"], task, sorted(paths))
        model_context = {"task": task, "feedback": request.feedback, "sourceFiles": texts,
                         "relatedFiles": inventory, "failure": task.get("failure_detail") or task.get("failure_reason") or state.get("error")}
        if task.get("owner") == "backend":
            from app.agents.data_source.prompt_context import task_implementation_contract
            model_context["approvedContract"] = task_implementation_contract(context["project_plan"], task)
        await progress(AgUiActionProgress(stage="generating", message="AI 正在生成单任务修改草稿。", percent=35))
        proposal = await propose_task_repair(model_context)
        unread = {item.path for item in proposal.files if item.path not in texts
                  and (Path(state["workspace"]) / item.path).is_file()}
        if proposal.decision == "inspect" or unread:
            more, more_signatures = await asyncio.to_thread(source_files, state["workspace"], task,
                sorted(paths | set(proposal.inspectPaths) | unread))
            # 同一轮读取过的文件若变化，不能用不同时间的证据拼成草稿。
            if any(more_signatures[name] != value for name, value in signatures.items()):
                raise ValueError("诊断期间源码发生变化，请重新生成。")
            texts, signatures = more, more_signatures
            proposal = await propose_task_repair({**model_context, "sourceFiles": texts, "inspectionUsed": True})
        if proposal.decision != "propose":
            raise ValueError(proposal.reason if proposal.decision == "blocked" else "定向阅读后证据仍不足，请补充修复要求。")
        if any(item.path not in texts and (Path(state["workspace"]) / item.path).is_file() for item in proposal.files):
            raise ValueError("修复方案仍涉及未经阅读的既有文件，请补充精确路径后重新生成。")
        await progress(AgUiActionProgress(stage="validating", message="正在检查单任务边界和完整 DAG。", percent=80))
        plan = await asyncio.to_thread(compile_repaired_plan, state, request.taskId, proposal, context)
        _, current_signatures = await asyncio.to_thread(source_files, state["workspace"], task,
            sorted(set(signatures) | set(plan["task_registry"][request.taskId]["target_files"])))
        if any(current_signatures[name] != value for name, value in signatures.items()):
            raise ValueError("生成期间源码发生变化，请重新生成。")
        _, _, _, current, _ = await load_repair_source(request)
        if build_task_plan_sha256(current["build_task_plan"]) != build_task_plan_sha256(state["build_task_plan"]):
            raise ValueError("生成期间任务计划发生变化，请重新生成。")
        draft = {"status": "pending", "feedback": request.feedback, **{key: getattr(request, key) for key in ("sourceRunId", "sourceThreadId", "sessionId", "taskId")},
            "basePlanDigest": build_task_plan_sha256(state["build_task_plan"]), "sourceSignatures": current_signatures,
            "proposal": proposal.model_dump(), "reason": proposal.reason,
            "before": task_view(task), "after": task_view(plan["task_registry"][request.taskId])}
        with build_task_plan_lifecycle_lock(request.workspaceRoot):
            if build_task_plan_sha256(load_build_task_plan_json(build_task_plan_json_path(state))) != draft["basePlanDigest"]:
                raise ValueError("任务计划已变化，未保存过期修复草稿。")
            return draft_view(save_draft(request, draft))
