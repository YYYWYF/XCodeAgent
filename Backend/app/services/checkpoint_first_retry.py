"""一次点击先恢复原 checkpoint，失败则只从固定原始节点重新执行。"""

from typing import Any

from app.domain.execution_recovery import ExecutionFailureOrigin, RecoveryExecutionError
from app.services.business_node_retry import prepare_business_node_retry, read_retry_hint
from app.services.execution_failure_classifier import classify_execution_failure
from app.services.retry_origin import RetryOrigin, load_retry_origin, complete_retry_origin
from app.services.retry_preparation import await_checkpoint_preparation, CheckpointPreparationTimeout


async def _failure_checkpoint_config(source, graph, hint):
    """读取原节点的恢复配置，不把恢复配置缺失升级成业务入口缺失。"""
    get_state = getattr(graph, "aget_state", None)
    if get_state is not None:
        snapshot = await get_state({"configurable": {"thread_id": source.thread_id, "checkpoint_ns": ""}})
        if (snapshot.values or {}).get("active_run_id") == source.run_id and hint.node in snapshot.next:
            return snapshot.config
    async for snapshot in graph.aget_state_history({"configurable": {"thread_id": source.thread_id}}):
        values = getattr(snapshot, "values", {}) or {}
        if values.get("active_run_id") == source.run_id and hint.node in (getattr(snapshot, "next", ()) or ()):
            return getattr(snapshot, "config", None)
    return None


def requires_manual_resolution(failure: Any) -> bool:
    """配置、额度及外部服务连接失败不进行 checkpoint 和业务双重自动尝试。"""

    if failure is None:
        return False
    return (
        failure.origin == ExecutionFailureOrigin.EXTERNAL_DEPENDENCY
        or failure.stage == "model_setup"
        or (failure.origin == ExecutionFailureOrigin.MODEL_CALL and (
            failure.http_status in {400, 401, 402, 403, 404, 429, 502, 503, 504}
            or failure.code.lower() in {"connecterror", "connectionerror", "connecttimeout", "readtimeout",
                                      "apiconnectionerror", "apitimeouterror", "timeout_error",
                                      "unit_generation_model_connection_failed", "unit_generation_model_request_timeout"}
        ))
    )


async def prepare_checkpoint_first_retry(source, graph, *, allow_stopped=False):
    """在任何恢复操作前固定原始现场，再复用业务入口的运行交接与资源管理。"""

    origin = load_retry_origin(source.workspace, source.run_id, source.thread_id)
    if origin is None:
        hint = await read_retry_hint(source, graph)
        if hint is not None:
            config = None
            try:
                config = await await_checkpoint_preparation(
                    _failure_checkpoint_config(source, graph, hint), source.run_id,
                )
            except Exception:
                # 已读出的节点和提问仍然有效；恢复配置读不到就直接进入自动降级。
                config = None
            origin = RetryOrigin(thread_id=source.thread_id, source_run_id=source.run_id,
                                 node=hint.node, request=hint.request, checkpoint_config=config)
    if origin is None:
        from app.services.business_node_retry import release_unretryable_execution

        await release_unretryable_execution(source)
        raise RecoveryExecutionError("RETRY_ENTRY_NOT_FOUND", "找不到恢复入口，请手动发起流程。")
    attempt = not origin.checkpoint_attempted and not requires_manual_resolution(source.failure)
    # 先持久化消费状态；恢复过程写出任何 checkpoint 前，原始身份已经稳定。
    origin = origin.model_copy(update={"checkpoint_attempted": True})
    context = await prepare_business_node_retry(source, graph, allow_stopped=allow_stopped, retry_origin=origin)
    origin = context.retry_origin or origin
    # 用户选择重建缺失前置产物时，直接进入对应生成节点，不先重放已知缺输入的现场。
    context.graph = CheckpointFirstRetryGraph(context.graph, context, origin,
        attempt and not origin.regeneration_node)
    return context


def _root_update(chunk: Any) -> dict:
    """只读取 LangGraph 原生 root updates，不把子图中间失败当成最终失败。"""

    if not isinstance(chunk, tuple):
        return {}
    if len(chunk) == 3:
        namespace, mode, data = chunk
        if namespace:
            return {}
    elif len(chunk) == 2:
        mode, data = chunk
    else:
        return {}
    return data if mode == "updates" and isinstance(data, dict) else {}


class CheckpointFirstRetryGraph:
    """包裹既有 Graph 流，自动降级仍由同一 AG-UI Run 和原资源所有者执行。"""

    def __init__(self, graph, context, origin: RetryOrigin, attempt: bool):
        """保留普通 Graph 接口，并绑定不变的原始业务失败信息。"""
        self.graph, self.context, self.origin, self.attempt = graph, context, origin, attempt
        self.completion_recorded = False

    def __getattr__(self, name):
        """状态查询及节点定义继续使用原 Graph，不另建执行协议。"""
        return getattr(self.graph, name)

    async def _completed(self):
        """原节点成功后解除锚定，后续业务节点失败按新的业务现场处理。"""
        if not self.completion_recorded:
            await complete_retry_origin(self.context.workspace, self.context.new_run_id, self.origin)
            self.completion_recorded = True

    async def astream(self, state, *, config, stream_mode, subgraphs=False, **kwargs):
        """恢复最多一次；异常和终止失败自动降级，取消及用户待处理错误直接收口。"""
        options = {**kwargs, "stream_mode": stream_mode}
        if subgraphs:
            options["subgraphs"] = True
        if self.attempt:
            buffered = []
            resumed = False
            iterator = None
            try:
                if not self.origin.checkpoint_config:
                    raise ValueError("原始失败 checkpoint 无法读取恢复配置。")
                # 只覆盖本次身份和固定原提问；其余业务状态从原失败 checkpoint 继续。
                overlay = {key: state[key] for key in (
                    "active_run_id", "active_thread_id", "owner_session_id", "observability",
                    "retry_source_run_id", "lifecycle",
                ) if key in state}
                overlay.update(request=self.origin.request, resume_from=self.origin.node,
                               application_planning_interaction={})
                fork = await await_checkpoint_preparation(
                    self.graph.aupdate_state(self.origin.checkpoint_config, overlay), self.context.new_run_id,
                )
                # fork 决定 checkpoint 位置；取消及 worker 身份必须继续属于本轮 Run，不能沿用来源元数据。
                resume_config = {**config, **fork, "metadata": {
                    **fork.get("metadata", {}), **config.get("metadata", {}),
                }}
                iterator = self.graph.astream(None, config=resume_config, **options)
                async for chunk in iterator:
                    update = _root_update(chunk)
                    target = update.get(self.origin.node)
                    if not resumed:
                        buffered.append(chunk)
                        if isinstance(target, dict) and target.get("status") == "failed":
                            diagnostic = target.get("failure_diagnostic") or target.get("failureDiagnostic")
                            if isinstance(diagnostic, dict):
                                from app.domain.execution_recovery import ExecutionFailureEvidence
                                failure = ExecutionFailureEvidence.model_validate(diagnostic)
                                if requires_manual_resolution(failure):
                                    for item in buffered:
                                        yield item
                                    return
                            raise ValueError(str(target.get("error") or target.get("message") or "checkpoint 恢复未成功。"))
                        if isinstance(target, dict) or "__interrupt__" in update:
                            resumed = True
                            await self._completed()
                            for item in buffered:
                                yield item
                            buffered.clear()
                    else:
                        yield chunk
                if resumed:
                    return
                # 没有到达原业务节点或确认门，不能将空流当作恢复成功。
            except Exception as exc:
                if resumed or (not isinstance(exc, CheckpointPreparationTimeout)
                               and requires_manual_resolution(classify_execution_failure(exc, operation=self.origin.node))):
                    raise
            finally:
                if iterator is not None:
                    if resumed:
                        await iterator.aclose()
                    else:
                        from app.services.failed_run_cleanup import require_failed_run_drained
                        # 恢复失败也先取消再关闭流，不能在降级前无限等待退出。
                        await require_failed_run_drained(self.context.new_run_id, iterator.aclose())
            from app.services.failed_run_cleanup import require_failed_run_drained
            from app.services.workspace_process_registry import workspace_process_registry
            await require_failed_run_drained(self.context.new_run_id)
            if self.origin.node == "build":
                from app.services.build_restart import archive_checkpoint_build_attempt
                # 工作线程和命令全部退出后才移出恢复尝试绑定；仍沿固定原节点/提问降级。
                archive_checkpoint_build_attempt(state, self.context.new_run_id)
            workspace_process_registry.allow_run(self.context.new_run_id)
        # 全新输入和 checkpoint 分支；不读取刚才恢复过程产生的新失败现场。
        from app.services.stage_artifacts import load_stage_artifacts
        state = {**state, "request": self.origin.request}
        state.update(load_stage_artifacts(state, self.context.recovery_plan.target_node))
        completion_node = (
            self.origin.node if self.origin.node in {"technical_planning_generate", "technical_planning_commit"}
            else self.context.recovery_plan.target_node
        )
        async for chunk in self.graph.astream(state, config=config, **options):
            # 技术事务 begin 成功不代表原 generate/commit 已成功，不能提前解除原始锚点。
            target = _root_update(chunk).get(completion_node)
            if isinstance(target, dict) and target.get("status") != "failed":
                await self._completed()
            yield chunk
