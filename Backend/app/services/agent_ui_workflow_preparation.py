"""在页面开发主Workflow中完成受控依赖/设计准备，等待每份具体差异确认。"""

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from langgraph.config import get_stream_writer

from app.protocols.agent_ui_preparation import AgentUiPreparationAction
from app.services.agent_ui_build_evidence import load_build_evidence
from app.services.agent_ui_dependency_install import execute_dependency_plan, process_run_id as dependency_run_id
from app.services.agent_ui_dependency_plan import prepare_dependency_plan, read_object, safe_path
from app.services.agent_ui_design_transfer import execute_transfer, process_run_id as transfer_run_id
from app.services.agent_ui_design_transfer_plan import _formal_state, prepare_transfer_plan
from app.services.agent_ui_transfer_records import select_installation
from app.services.preview_runtime_state import redact
from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.workspace_process_registry import workspace_process_registry


def _pending(stage: str, *, plan: dict[str, Any] | None = None, error: str = '') -> dict[str, Any]:
    """把当前准备事实投影为同一开发对话中的可审阅确认门。"""
    preparation: dict[str, Any] = {'stage': stage}
    if plan is not None:
        preparation['plan'] = plan
    if error:
        preparation['error'] = redact(error)[:2000]
    return {
        'phase': 'prepare_build_tasks', 'status': 'requires_user_input',
        'build_task_plan_confirmation': {}, 'agent_ui_preparation_action': {},
        'clarification': {'mode': 'agent_ui_integration_pending', 'status': 'requires_user_input',
            'message': '请审阅当前智能体页面准备差异；确认后由开发流程执行并继续。',
            'agentUiPreparation': preparation},
        'timeline': ['prepare_build_tasks'],
    }


def _current_review(workspace: Path, *, error: str = '') -> dict[str, Any]:
    """优先复用有效安装，初始化后重新审阅，读取本身没有写入副作用。"""
    try:
        installation = select_installation(workspace)
    except ValueError:
        plan = prepare_dependency_plan(workspace, source='local-vendor', review_id=uuid4().hex)
        return _pending('dependency', plan=plan, error=error)
    plan = prepare_transfer_plan(workspace, installation_plan_id=installation)
    if plan['status'] != 'reviewable':
        return _pending('blocked', plan=plan, error=error or '；'.join(plan.get('blockers', [])))
    return _pending('initialize' if plan['initialization']['required'] else 'transfer', plan=plan, error=error)


def _prepare(state: dict[str, Any], publish: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
    """重读当前正式页面并执行唯一批准动作，不信任客户端产物或旧准备快照。"""
    scope = state.get('build_execution_scope') or {}
    action = AgentUiPreparationAction.model_validate(state['agent_ui_preparation_action']) if state.get('agent_ui_preparation_action') else None
    if scope.get('type') != 'page':
        if action is not None:
            return _pending('blocked', error='Agent UI 准备只能用于当前页面开发目标。')
        return None
    workspace_value = str(state.get('workspace') or '').strip()
    if not workspace_value:
        return None
    workspace = Path(workspace_value)
    product_path = safe_path(workspace, '.devagentstudio/plans/product-plan.json')
    if not product_path.is_file():
        return None
    try:
        product = read_object(product_path)
        page = next((item for item in project_ui_design_pages(product)
                     if item.get('pageId') == scope.get('targetId')), None)
        if not page or not page.get('agent_surfaces'):
            return _pending('blocked', error='当前页面没有正式 Agent UI 设计。') if action is not None else None
        # 正式文档、Markdown编辑和全部当前资产确认都必须先通过；批准准备不能批准设计。
        _formal_state(workspace)
        try:
            load_build_evidence(workspace)
            return None
        except (OSError, ValueError, KeyError):
            pass
        error = ''
        if action is not None and action.action == 'confirm':
            thread_id, run_id = str(state.get('active_thread_id') or ''), str(state.get('active_run_id') or '')
            if not thread_id or not run_id:
                raise ValueError('Agent UI 准备缺少当前开发运行身份。')

            def progress(stage: str) -> None:
                """把受控命令阶段放入主Workflow的AG-UI进度流。"""
                publish({'type': 'agent_ui_preparation.progress', 'stage': stage,
                         'message': '正在准备智能体页面：' + stage})

            try:
                if action.reviewId:
                    plan = prepare_dependency_plan(workspace, source='local-vendor', review_id=action.reviewId)
                    if plan['planId'] != action.planId:
                        raise ValueError('依赖计划过期，请重新审阅当前差异。')
                    record = execute_dependency_plan(workspace, plan_id=action.planId,
                        source='local-vendor', entry=plan['entry'], review_id=action.reviewId,
                        thread_id=thread_id, run_id=run_id, progress=progress)
                    expected = {'verified'}
                else:
                    plan = prepare_transfer_plan(workspace, installation_plan_id=action.installationPlanId,
                                                  target_source_root=action.targetSourceRoot)
                    if plan['planId'] != action.planId:
                        raise ValueError('转入计划过期，请重新审阅当前差异。')
                    record = execute_transfer(workspace, installation_plan_id=action.installationPlanId,
                        plan_id=action.planId, target_source_root=action.targetSourceRoot,
                        action='initialize' if plan['initialization']['required'] else 'confirm',
                        thread_id=thread_id, run_id=run_id, progress=progress)
                    expected = {'initialized', 'verified'}
                if record.get('state') not in expected:
                    return _pending('blocked', error=str(record.get('message') or '准备未通过验证，已有文件和任务记录保留。'))
                if record.get('state') == 'verified':
                    try:
                        load_build_evidence(workspace)
                        return None
                    except (OSError, ValueError, KeyError):
                        pass
            except ValueError as exc:
                # CAS在写入前拒绝旧批准后，必须重新展示当前差异，不能自动重放写入。
                error = str(exc)
        return _current_review(workspace, error=error)
    except (OSError, ValueError, KeyError) as exc:
        return _pending('blocked', error=str(exc))


async def prepare_agent_ui_workflow(state: dict[str, Any]) -> dict[str, Any] | None:
    """隔离同步准备，并在主Workflow停止时取消原命令owner且等待其收口。"""
    try:
        publish = get_stream_writer()
    except RuntimeError:
        def publish(_value: dict[str, Any]) -> None:
            """非流式单元调用没有AG-UI上下文，不发布进度。"""
    worker = asyncio.create_task(asyncio.to_thread(_prepare, state, publish))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        workspace, run_id = state.get('workspace'), state.get('active_run_id')
        if workspace and run_id:
            # 即使工作区同时变化，也保留原取消异常，等待后台线程完成落盘收口。
            with suppress(OSError, ValueError):
                workspace_process_registry.cancel_run(dependency_run_id(workspace, run_id))
                workspace_process_registry.cancel_run(transfer_run_id(workspace, run_id))
        with suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(worker)
        raise
