"""在页面开发主Workflow中自动准备已确认设计，仅依赖变更和异常暂停。"""

import asyncio
import json
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
            'message': ('生产依赖变更需要确认，随后自动继续页面准备。' if stage == 'dependency'
                        else '智能体页面准备已暂停，请处理提示的问题后重新检查。'),
            'agentUiPreparation': preparation},
        'timeline': ['prepare_build_tasks'],
    }


def _current_plan(workspace: Path) -> tuple[str, dict[str, Any]]:
    """优先复用有效安装，每个准备阶段都重新计算当前差异及保护摘要。"""
    try:
        installation = select_installation(workspace)
    except ValueError:
        return 'dependency', prepare_dependency_plan(workspace, source='local-vendor')
    plan = prepare_transfer_plan(workspace, installation_plan_id=installation)
    if plan['status'] != 'reviewable':
        return 'blocked', plan
    return 'initialize' if plan['initialization']['required'] else 'transfer', plan


def _changes_production_dependencies(workspace: Path, plan: dict[str, Any]) -> bool:
    """生产依赖声明新增或变更仍需确认，已有声明的安装和样式准备可自动执行。"""
    change = next((item for item in plan['changes'] if item['path'] == 'package.json'), None)
    if change is None:
        return False
    current = read_object(safe_path(workspace, 'frontend/package.json'))
    proposed = json.loads(change['after'])
    return current.get('dependencies', {}) != proposed.get('dependencies', {})


def _execute(workspace: Path, stage: str, plan: dict[str, Any], state: dict[str, Any],
             publish: Callable[[dict[str, Any]], None]) -> None:
    """复用原命令owner、CAS和失败留痕，自动准备也必须绑定主Workflow身份。"""
    thread_id, run_id = str(state.get('active_thread_id') or ''), str(state.get('active_run_id') or '')
    if not thread_id or not run_id:
        raise ValueError('Agent UI 准备缺少当前开发运行身份。')

    def progress(current: str) -> None:
        """沿原AG-UI主对话发布可见进度，不创建额外产品接口。"""
        labels = {'install': '安装并验证依赖', 'initialize': '初始化页面组件', 'copy': '转入已确认设计',
                  'composition': '检查组件组合', 'validation': '验证目标工程'}
        publish({'type': 'agent_ui_preparation.progress', 'stage': current,
                 'message': '正在准备智能体页面：' + labels.get(current, current)})

    if stage == 'dependency':
        record = execute_dependency_plan(workspace, plan_id=plan['planId'], source='local-vendor',
            entry=plan['entry'], review_id=plan['reviewId'], thread_id=thread_id, run_id=run_id, progress=progress)
        expected = 'verified'
    else:
        record = execute_transfer(workspace, installation_plan_id=plan['installation']['planId'],
            plan_id=plan['planId'], target_source_root=plan['target']['sourceRoot'],
            action='initialize' if stage == 'initialize' else 'confirm',
            thread_id=thread_id, run_id=run_id, progress=progress)
        expected = 'initialized' if stage == 'initialize' else 'verified'
    if record.get('state') != expected:
        raise ValueError(str(record.get('message') or '准备未通过验证，已有文件和任务记录保留。'))


def _prepare(state: dict[str, Any], publish: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
    """正式设计已确认后自动完成常规准备；生产依赖变更和任何失败均先暂停。"""
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
        if action is not None and action.action == 'confirm':
            plan = prepare_dependency_plan(workspace, source='local-vendor', review_id=action.reviewId)
            if plan['planId'] != action.planId:
                raise ValueError('依赖计划过期，请重新检查当前差异。')
            _execute(workspace, 'dependency', plan, state, publish)
        seen: set[str] = set()
        # 最多依赖、初始化、转入三次写入；初始化后重新规划，失败或无进展绝不循环重放。
        for attempt in range(4):
            _formal_state(workspace)
            try:
                load_build_evidence(workspace)
                return None
            except (OSError, ValueError, KeyError):
                pass
            stage, plan = _current_plan(workspace)
            if stage == 'blocked':
                return _pending('blocked', plan=plan, error='；'.join([
                    *plan.get('blockers', []), *[f'保留现有修改：{path}' for path in plan.get('conflicts', [])]]))
            if stage == 'dependency' and _changes_production_dependencies(workspace, plan):
                review = prepare_dependency_plan(workspace, source='local-vendor', review_id=uuid4().hex)
                return _pending('dependency', plan=review)
            if attempt == 3 or plan['planId'] in seen:
                raise ValueError('准备后仍缺少有效转入证据，请核对当前文件和任务记录后重新检查。')
            seen.add(plan['planId'])
            _execute(workspace, stage, plan, state, publish)
            if stage == 'transfer':
                # 转入自报成功仍须通过完整构建来源验证；失败不得转回安装并自动重试。
                load_build_evidence(workspace)
                return None
        raise ValueError('Agent UI 准备未完成。')
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
