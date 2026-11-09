"""规划恢复只读投影：当前 checkpoint、真实执行及用户结束事实共同决定确认门。"""

import inspect
from typing import Any
from app.domain.execution_recovery import RecoveryExecutionError
from app.protocols.application_planning_run_lock import application_planning_run_lock
from app.protocols.application_planning_interrupt import project_application_planning_interrupt
from app.protocols.application_planning_recovery_projection import project_planning_recovery, project_application_planning_recovery_snapshot
from app.protocols.workflow.projection import _workflow_summary, _workflow_visual_payload
from app.services.application_lifecycle import load_application_lifecycle, application_lifecycle_payload
from app.services.planning_recovery_read import read_planning_recovery_checkpoint
from app.services.detached_execution import active_detached_run_for_thread
from app.services.ui_design_generator import load_page_code
from app.services.ui_design_manifest import present_ui_pages
from app.services.ui_design_project_setup import ui_design_project_dir
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path, workspace_root


async def read_planning_recovery(*, graph: Any, workspace: str, application_id: str | None, thread_id: str, snapshot: Any = None):
    """读取当前规划现场，不运行 Graph、不释放资源、不把查询登记为业务任务。"""
    if snapshot is None and callable(graph):
        from app.services.recovery_checkpoint import checkpoint_thread_exists
        if not await checkpoint_thread_exists(workspace, thread_id):
            if application_planning_run_lock(thread_id).locked() or active_detached_run_for_thread(workspace, thread_id):
                raise RecoveryExecutionError("APPLICATION_PLANNING_CHECKPOINT_PENDING",
                    "规划仍在执行，正在等待首个已提交进度，稍后将重新同步。")
            return None, None, load_application_lifecycle(workspace)
    active_graph = (
        graph(
            workspace=workspace,
            project_id=application_id,
        )
        if callable(graph)
        else graph
    )
    if inspect.isawaitable(active_graph):
        active_graph = await active_graph
    if snapshot is None:
        snapshot = await read_planning_recovery_checkpoint(active_graph, thread_id)
    values = getattr(snapshot, "values", {})
    result = dict(values) if isinstance(values, dict) else {}
    if not result:
        lifecycle = load_application_lifecycle(workspace)
        if application_planning_run_lock(thread_id).locked() or active_detached_run_for_thread(workspace, thread_id):
            # 首个 checkpoint 尚未提交时也不能把活跃 writer 误判为空白新任务。
            raise RecoveryExecutionError("APPLICATION_PLANNING_CHECKPOINT_PENDING",
                "规划仍在执行，正在等待首个已提交进度，稍后将重新同步。")
        return None, snapshot, lifecycle
    checkpoint_run_id = str(result.get("active_run_id") or "").strip()
    # 当前 checkpoint 仅定位原执行；重试不再要求精确恢复 lineage。
    from app.persistence.execution_recovery import get_execution
    source = await get_execution(workspace, checkpoint_run_id) if checkpoint_run_id else None
    from app.persistence.execution_recovery import get_latest_execution_for_thread
    from app.services.checkpoint_execution import execution_is_live
    active_source = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
    accepted_run = active_detached_run_for_thread(workspace, thread_id)
    if accepted_run and accepted_run != checkpoint_run_id and (
        active_source is None or active_source.run_id != accepted_run
    ):
        # 新提交尚未写执行索引时也不能恢复上一轮确认；读取只等待现场，不启动新业务。
        raise RecoveryExecutionError("APPLICATION_PLANNING_CHECKPOINT_PENDING",
            "规划仍在执行，正在等待首个已提交进度，稍后将重新同步。")
    if active_source and active_source.run_id != checkpoint_run_id and execution_is_live(active_source):
        # 首个新 checkpoint 前只由实际运行登记证明交接，不用生命周期 Run 否决现有现场。
        from types import SimpleNamespace
        source = active_source
        checkpoint_run_id = source.run_id
        result = {"active_run_id": source.run_id, "workspace": workspace,
                  "phase": source.current_node or source.first_node, "status": "running"}
        snapshot = SimpleNamespace(values=result, config={}, tasks=(), next=())
    lineage_resolution = None
    projection = await project_planning_recovery(
        workspace=workspace,
        thread_id=thread_id,
        graph=active_graph,
        snapshot=snapshot,
        source=source,
        lineage_resolution=lineage_resolution,
    )
    if projection.classification == "awaiting_user":
        result = project_application_planning_interrupt(result, snapshot)
    result = project_application_planning_recovery_snapshot(
        result,
        projection=projection,
    )
    # UI 确认阶段：后台生成池把最新 page status/code 写进 ui-designs.json，
    # 但 checkpoint 里的 ui_designs 仍停留在入队时的 queued/generating（池不写
    # checkpoint）。recovery 只读 checkpoint 不跑 Graph，若不回填 manifest，
    # 返回的快照里 pages 无 code 且 status 过时，右侧渲染区永远显示「生成中」。
    if str(result.get("phase") or "") == "ui_confirmation":
        manifest = load_ui_designs_json(
            ui_designs_json_path(dict(snapshot.values))
        )
        if isinstance(manifest, dict) and manifest.get("pages"):
            # 正式 manifest 刻意剥离了 code（源码是运行时数据，不入库）。但确认界面
            # 要靠它渲染预览、「查看设计稿」按钮也按它判可用性 —— 不回填的话，
            # 重新打开工作区后每一页（本轮新生成的和继承来的都一样）都点不开。
            result["ui_designs"] = _with_page_code(manifest, dict(snapshot.values))
            clarification = result.get("clarification")
            if isinstance(clarification, dict):
                product_plan = result.get("product_plan")
                product_plan = (
                    product_plan if isinstance(product_plan, dict) else {}
                )
                clarification["pages"] = present_ui_pages(
                    result["ui_designs"], product_plan
                )
    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is not None:
        result["lifecycle"] = application_lifecycle_payload(lifecycle)
    recovery_run_id = str(result.get("active_run_id") or f"recovery:{thread_id}")
    summary = _workflow_summary(result, [])
    summary["message"] = projection.message
    if projection.reason_code == "APPLICATION_PLANNING_ENDED":
        # 只发布独立结束回执；修改必须发起新请求，不能消费停止前的旧确认。
        summary.update(status="completed", phase="plan_control", planControl={
            "action": "end", "status": "ended", "targetRunId": recovery_run_id,
        })
        summary.pop("error", None)
    visual_payload = _workflow_visual_payload(
        run_id=recovery_run_id,
        thread_id=thread_id,
        summary=summary,
        events=[],
        result=result,
    )
    return visual_payload, snapshot, lifecycle


def _with_page_code(manifest: dict[str, Any], state_values: dict[str, Any]) -> dict[str, Any]:
    """给正式 manifest 的每页补回 `code`，供确认界面渲染预览。

    `persisted_ui_manifest` 落盘时刻意剥离了 `code`（源码是运行时数据，不入库），
    但确认界面要靠它渲染预览、「查看设计稿」按钮也按它判可用性。重新打开工作区时
    只读 checkpoint + 磁盘 manifest，不回填的话每一页都点不开 —— 本轮新生成的
    和从上一轮继承来的一视同仁。

    读不到某个页面的源码就原样留空：按钮禁用总好过报错。
    """

    pages = manifest.get("pages")
    if not isinstance(pages, list):
        return manifest
    project_dir = str(ui_design_project_dir(workspace_root(state_values)))
    enriched: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict) or page.get("code"):
            enriched.append(page)
            continue
        page_key = str(page.get("page_key") or "").strip()
        code = load_page_code(project_dir, page_key) if page_key else None
        enriched.append({**page, "code": code} if code else page)
    return {**manifest, "pages": enriched}
