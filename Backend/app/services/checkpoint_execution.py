"""统一解释 checkpoint 的业务位置；执行索引只定位线程，运行登记只证明工作仍存活。"""

from dataclasses import dataclass, replace
from typing import Any

from app.domain.execution_recovery import DurableExecutionStatus, RecoveryExecutionError


@dataclass(frozen=True)
class CheckpointExecution:
    """保存同一次已提交现场的状态、节点、提问和原生快照。"""

    snapshot: Any
    status: DurableExecutionStatus
    node: str
    request: str


def execution_is_live(source) -> bool:
    """只观察实际运行，不把文件里的 running 或旧租约当成存活证明。"""
    from app.protocols.workflow.run_control import workflow_run_registry
    from app.services.run_workers import active_worker_runs
    from app.services.workspace_process_registry import workspace_process_registry
    return (workflow_run_registry.is_active(source.run_id, workspace=source.workspace)
            or source.run_id in active_worker_runs()
            or bool(workspace_process_registry.active_run_processes(source.run_id)))


def interpret_checkpoint(source, snapshot, graph) -> CheckpointExecution | None:
    """仅解释属于原 Run 的 root 现场，待执行节点优先于过期的阶段展示字段。"""
    values = getattr(snapshot, "values", {}) or {}
    if not isinstance(values, dict) or values.get("active_run_id") != source.run_id:
        return None
    config = (getattr(snapshot, "config", {}) or {}).get("configurable", {})
    if config.get("checkpoint_ns"):
        return None
    tasks = getattr(snapshot, "tasks", ()) or ()
    failed = [task.name for task in tasks if getattr(task, "error", None)]
    # 用户停止后的原生 CancelledError 是中断证据，不应把继续生成误投影为业务失败。
    if source.status is DurableExecutionStatus.INTERRUPTED and all(
        str(task.error) == "CancelledError()" for task in tasks if getattr(task, "error", None)
    ):
        failed = []
    pending = list(getattr(snapshot, "next", ()) or ())
    raw_status = str(values.get("status") or "")
    phase = str(values.get("phase") or "")
    candidates = failed or ([phase] if raw_status == "failed" and phase in getattr(graph, "nodes", {}) else pending)
    node = str(candidates[0]) if len(candidates) == 1 else ""
    if node == "workflow_entry":
        # 请求已提交但尚未进入业务节点时，入口来自同一 checkpoint 的调度字段。
        entry = str(values.get("direct_modification_resume_node") or values.get("resume_from") or "")
        if not entry:
            from app.graph.direct_modification_workflow import _route_direct_entry
            from app.graph.application_planning_workflow import _route_start as planning_start
            from app.graph.workflow import route_workflow_start as workbench_start

            route = (_route_direct_entry if source.workflow_scope == "conversation" else
                     planning_start if source.execution_kind == "application_planning" else workbench_start)
            entry = route(values)
        if entry in graph.nodes and entry != "workflow_entry":
            node = entry
    interaction = values.get("application_planning_interaction") or {}
    question = interaction.get("request") if interaction.get("action") in {"answer", "revise", "design_change"} else ""
    request = str(question or values.get("design_change_generation_request")
                  or values.get("design_change_request") or values.get("request") or "").strip()
    if failed or raw_status == "failed":
        status = DurableExecutionStatus.FAILED
    elif any(getattr(task, "interrupts", ()) for task in tasks) or raw_status in {"requires_user_input", "awaiting_user"}:
        status = DurableExecutionStatus.AWAITING_USER
    elif execution_is_live(source):
        status = DurableExecutionStatus.RUNNING
    elif pending:
        status = DurableExecutionStatus.INTERRUPTED
    elif raw_status in {"completed", "cancelled", "stopped"}:
        status = DurableExecutionStatus(raw_status)
    else:
        # 未提交明确终态且真实工作已退出：保留最后现场，不能借用索引假定仍在确认。
        status = DurableExecutionStatus.INTERRUPTED
    return CheckpointExecution(snapshot, status, node, request)


async def read_checkpoint_execution(source, graph) -> CheckpointExecution | None:
    """有界读取最新原执行现场，历史中的旧失败不能覆盖较新的成功或确认。"""
    from app.services.retry_preparation import await_checkpoint_preparation

    async def read():
        """按 LangGraph 原生历史顺序只取最新同 Run 现场。"""
        config = {"configurable": {"thread_id": source.thread_id, "checkpoint_ns": ""}}
        # 原生 history 会先解码整段历史；当前 Run 的最新快照足够时只读取这一份。
        get_state = getattr(graph, "aget_state", None)
        if get_state is not None:
            snapshot = await get_state(config)
            current = interpret_checkpoint(source, snapshot, graph)
            if current is not None and (current.status != DurableExecutionStatus.FAILED or current.node):
                return current
        latest = None
        async for snapshot in graph.aget_state_history(config):
            result = interpret_checkpoint(source, snapshot, graph)
            if result is not None:
                if latest is None:
                    latest = result
                    if result.status != DurableExecutionStatus.FAILED or result.node:
                        return result
                elif result.node and result.node not in {"handle_failure", "await_user_input", "workflow_entry"}:
                    # handle_failure 的终态不再保留业务 phase；只在同次失败内找最近业务现场。
                    return replace(latest, node=result.node, request=latest.request or result.request)
        return latest

    try:
        return await await_checkpoint_preparation(read(), source.run_id)
    except Exception as exc:
        # 只有读取成功且没有同 Run 现场才表示缺失；暂时读失败不能释放有效恢复入口。
        raise RecoveryExecutionError("RETRY_CHECKPOINT_UNAVAILABLE",
            "暂时无法读取执行进度，请稍后重试。原恢复入口已保留。") from exc


def checkpoint_source(source, execution: CheckpointExecution | None):
    """把索引投影到同一 checkpoint；用户主动结束的记录不被自动重新打开。"""
    if execution is None or source.status in {DurableExecutionStatus.CANCELLED, DurableExecutionStatus.STOPPED}:
        return source
    return source.model_copy(update={"status": execution.status, "current_node": execution.node or None})


async def resolve_checkpoint_source(source, graph):
    """让恢复投影和实际点击使用同一状态解释，旧等待记录不能堵住恢复。"""
    execution = await read_checkpoint_execution(source, graph)
    source = checkpoint_source(source, execution)
    if execution is None and not execution_is_live(source) and source.status in {
        DurableExecutionStatus.RUNNING, DurableExecutionStatus.AWAITING_USER,
    }:
        return source.model_copy(update={"status": DurableExecutionStatus.INTERRUPTED})
    return source


async def index_checkpoint_execution(workspace: str, thread_id: str, snapshot):
    """索引写入丢失时从当前 checkpoint 重建查询索引，不补造缺失的业务现场。"""
    from datetime import datetime, timezone
    from pathlib import Path
    from app.domain.execution_recovery import DurableExecutionRecord
    from app.persistence.execution_recovery import insert_execution

    values = getattr(snapshot, "values", {}) or {}
    run_id = str(values.get("active_run_id") or "")
    saved_workspace = str(values.get("workspace") or "")
    if not run_id or not saved_workspace or Path(saved_workspace).resolve() != Path(workspace).resolve():
        return None
    scope = values.get("workflow_scope") or None
    now = datetime.now(timezone.utc)
    return await insert_execution(DurableExecutionRecord(
        run_id=run_id, thread_id=thread_id, workspace=workspace,
        project_id=values.get("project_id") or None, owner_session_id=values.get("owner_session_id") or None,
        execution_kind="application_planning" if scope == "application_planning" else "workbench",
        workflow_scope=scope, first_node=str(values.get("resume_from") or values.get("phase") or "workflow_entry"),
        status=DurableExecutionStatus.INTERRUPTED, started_at=now, updated_at=now,
    ))


async def index_missing_checkpoint_executions(workspace: str):
    """只为当前应用指向的执行补建索引，避免扫描或复活已结束的历史会话。"""
    from app.persistence.execution_recovery import get_execution
    from app.services.application_lifecycle import load_application_lifecycle
    from app.graph.workflow import workflow_graph_for_request

    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        return
    refs = [(item.run_id, item.thread_id) for item in lifecycle.active_executions.values()]
    if lifecycle.active_run_id and lifecycle.initialization.thread_id:
        refs.append((lifecycle.active_run_id, lifecycle.initialization.thread_id))
    for run_id, thread_id in dict.fromkeys(refs):
        if await get_execution(workspace, run_id) is not None:
            continue
        graph = await workflow_graph_for_request(workspace=workspace)
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
        if (getattr(snapshot, "values", {}) or {}).get("active_run_id") == run_id:
            await index_checkpoint_execution(workspace, thread_id, snapshot)
