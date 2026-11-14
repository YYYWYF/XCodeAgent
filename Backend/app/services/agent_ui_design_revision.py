"""按真实源码所有权调整设计草稿，统一验证正文与共享 Agent UI 后发布。"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Callable

from app.services.agent_ui_design_creator import run_design_creator
from app.services.agent_ui_design_generator import AgentUiGeneratedDesign, _check_cancel, _host_files, _json, _validate_files, _write_record, _write_sources, design_page_records
from app.services.agent_ui_design_body_revision import generate_business_revision
from app.services.agent_ui_design_edit_scope import resolve_design_edit_tasks
from app.services.agent_ui_design_presentation import read_presentations, validate_presentation
from app.services.agent_ui_design_project import (
    SEED_FILES, AgentUiDesignProject, build_agent_ui_design_project,
    prepare_agent_ui_design_project, validate_design_project, _run,
    source_digest,
)
from app.services.agent_ui_tool_host import AgentUiToolHostInspection
from app.services.agent_ui_design_shared import SHARED_AGENT_ROOT, require_shared_application
from app.services.agent_ui_design_content import has_business_body
from app.services.agent_ui_design_actions import validate_native_action_composition
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_generator import UiDesignStreamCancelled


def _snapshot(project: Path) -> dict[str, str]:
    """读取源码和构建输入的真实摘要，拒绝链接；不读取依赖或 Creator 凭据。"""
    paths = [path for path in (project / 'src').rglob('*') if path.is_file() or path.is_symlink()]
    paths += [project / name for name in (*SEED_FILES, 'vite.config.ts', 'index.html')]
    paths += [project / '.agent-ui' / name for name in ('project.json', 'source-lock.json')]
    result: dict[str, str] = {}
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(project.resolve()):
            raise ValueError('Creator 设计资产不能包含链接或工程外路径。')
        if path.is_file():
            result[path.relative_to(project).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _check_changes(before: dict[str, str], project: Path, root: str,
                   business_paths: set[str], presentations: dict[str, Any], targets: set[str]) -> list[str]:
    """逐文件验证目标所有权；共享展示文件只能修改当前页面，服务和 Runtime 保持只读。"""
    after = _snapshot(project)
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    forbidden = ('services/', 'agent-contract/', 'framework/', 'runtime/')
    for path in changed:
        if path == 'src/design/presentation.json':
            current = read_presentations(project)
            if ({key: value for key, value in current.items() if key not in targets}
                    != {key: value for key, value in presentations.items() if key not in targets}):
                raise ValueError('Creator 修改了非目标页的展示配置，已保留原设计。')
            for page_id in targets & current.keys():
                validate_presentation(current[page_id])
            continue
        relative = path.removeprefix(root + '/')
        if path in business_paths:
            continue
        if (not path.startswith(root + '/') or relative.startswith(forbidden)
                or relative in {'application/runtime-config.generated.ts', 'application/Agent.tsx'}):
            raise ValueError(f'Creator 修改超出当前设计范围：{path}；已保留原设计。')
    if not changed:
        raise ValueError('设计调整未产生可核对的文件修改，已保留原设计；本轮未标记成功。')
    return changed


def _creator_request(pages: list[dict[str, Any]], targets: list[str], instruction: str) -> str:
    """仅把 Agent UI 子任务交给 Creator，业务正文作为有明确所有权的只读上下文。"""
    business_paths = [f'src/pages/page-{index}.tsx' for index, page in enumerate(pages) if has_business_body(page)]
    return instruction + '\n\nHost scope data (context, not an additional user request):\n' + _json({
        'scope': 'shared-application', 'sourceRoot': SHARED_AGENT_ROOT,
        'targetPageIds': targets, 'allPageIds': [page['pageId'] for page in pages],
        'businessPagesReadOnly': business_paths, 'sharedPresentationFile': 'src/design/presentation.json',
        'editablePresentationKeys': targets,
        'baseline': 'current shared application, including all previously accepted customizations',
        'pageDifferences': 'use public Agent props in presentation.json for welcome, suggestions, locale, theme, assistantPanelWidth; styles are scoped to each page',
        'layoutOwner': 'one shared AppUIModel and semantic Plugin composition for every page',
        'nativeActionOwnership': 'stop_generation uses the official composer submit-action; copy_response uses the official response-footer copy-action and copies the complete response; preserve their slots and do not add page-local substitutes',
        'sharedChangeEffect': 'shared layout, plugins, application components and styles affect every page and require renewed confirmation of every page',
        'independentPageLayouts': 'unavailable; do not create per-page applications or source roots',
        'renderEntry': 'application/project-config.tsx export agentUIProject',
        'additionalProductCapabilities': 'separate formal revision and user confirmation',
        'dependencyAndSourceTakeover': 'separate explicit user authorization',
        'protectedPaths': [*business_paths, 'services', 'agent-contract', 'framework', 'runtime',
            'application/Agent.tsx', 'application/runtime-config.generated.ts',
            '.agent-ui', 'node_modules', 'package.json', 'pnpm-lock.yaml'],
        'formalConfirmation': 'pending_user_confirmation', 'confirmedPages': pages,
    })


def revise_agent_ui_design(inspection: AgentUiToolHostInspection, *, current_project: AgentUiDesignProject,
                          draft_root: Path, pnpm_cli: Path, requirement_spec: dict[str, Any],
                          product_plan: dict[str, Any], targets: list[str], instruction: str,
                          run_id: str, should_cancel: Callable[[], bool] | None = None,
                          on_progress: Callable[[str, str], None] | None = None) -> AgentUiGeneratedDesign:
    """在同一草稿内按 owner 执行子任务；任何失败均不发布部分结果或替换旧稿。"""
    validate_design_project(current_project)
    require_shared_application(current_project.project_dir)
    pages = project_agent_design_pages(requirement_spec, product_plan)
    if not targets or not set(targets) <= {page['pageId'] for page in pages} or not instruction.strip():
        raise ValueError('设计调整必须绑定当前页面和非空修改要求。')
    if on_progress:
        on_progress('正在分析页面正文与 Agent UI 的修改范围', targets[0])
    tasks = resolve_design_edit_tasks(current_project.project_dir, pages, targets, instruction,
                                      should_cancel=should_cancel)
    _check_cancel(should_cancel)
    validate_design_project(current_project)
    project = prepare_agent_ui_design_project(inspection, draft_root=draft_root, pnpm_cli=pnpm_cli, run_id=run_id)
    _check_cancel(should_cancel)
    validate_design_project(project)
    if (project.root / 'generation.json').exists():
        raise ValueError('Creator 调整必须使用新草稿目录。')
    # 仅向本轮新建草稿复制当前源码，原工程及其 node_modules 从不写入或共享。
    shutil.copytree(current_project.project_dir / 'src', project.project_dir / 'src', dirs_exist_ok=True)
    validate_design_project(current_project)
    executors = {'ui_model' if task.owner == 'business_page' else 'creator' for task in tasks}
    evidence: dict[str, Any] = {'status': 'generating', 'confirmation_status': 'pending_user_confirmation',
                                'executor': next(iter(executors)) if len(executors) == 1 else 'mixed', 'tasks': []}
    record = project.root / 'generation.json'
    _write_record(record, evidence)
    try:
        _check_cancel(should_cancel)
        business_paths = {f'src/pages/page-{index}.tsx' for index, page in enumerate(pages)
                          if page['pageId'] in targets and has_business_body(page)}
        before = _snapshot(project.project_dir)
        for task in tasks:
            _check_cancel(should_cancel)
            task_before = _snapshot(project.project_dir)
            task_presentations = read_presentations(project.project_dir)
            if task.owner == 'business_page':
                if on_progress:
                    on_progress('正在由 UI 模型调整普通页面正文', task.page_ids[0])
                selected = [page for page in pages if page['pageId'] in task.page_ids]
                paths = tuple(f'src/pages/page-{index}.tsx' for index, page in enumerate(pages) if page in selected)
                expected = source_digest(project.project_dir)

                def validate_body(candidate: dict[str, str]) -> None:
                    """沿既有重试预算回灌 AST 与业务绑定错误，候选通过前不写入源码。"""
                    _validate_files(project, candidate, selected, inspection.node_executable, run_id)

                files = generate_business_revision(project.project_dir, selected, paths, task.instruction,
                                                   validate=validate_body, should_cancel=should_cancel)
                _write_sources(project, files, expected, should_cancel)
                allowed_business = set(paths)
                receipt = {'executor': 'ui_model'}
            else:
                if on_progress:
                    on_progress('正在由 Creator 调整 Agent UI', task.page_ids[0])
                receipt = {**run_design_creator(inspection, project.project_dir,
                    _creator_request(pages, list(task.page_ids), task.instruction), run_id=run_id,
                    should_cancel=should_cancel, source_root=SHARED_AGENT_ROOT,
                    on_progress=(lambda message: on_progress(message, task.page_ids[0])) if on_progress else None),
                    'executor': 'creator'}
                allowed_business = set()
            _check_cancel(should_cancel)
            changed = _check_changes(task_before, project.project_dir, SHARED_AGENT_ROOT,
                                     allowed_business, task_presentations, set(task.page_ids))
            evidence['tasks'].append({'pageIds': list(task.page_ids), 'changedFiles': changed, **receipt})
        if on_progress:
            on_progress('正在验证并构建设计稿', targets[-1])
        design = project.project_dir / 'src/design'
        for path, source in _host_files(pages).items():
            if Path(path).name in {'Agent.tsx', 'SharedHost.tsx', 'SharedApplication.tsx', 'SharedPage.tsx',
                                  'runtimeResults.ts', 'useRuntimeResult.ts', 'runtimeContext.tsx', 'runtimeRequest.ts', 'RuntimeBusinessPanel.tsx', 'RuntimeInput.tsx', 'shared-host.css', 'surfaces.ts', 'main.tsx'}:
                (project.project_dir / path).write_text(source, encoding='utf-8')
        app = project.project_dir / 'src/App.tsx'
        app.write_text(app.read_text(encoding='utf-8').replace("from './agent-ui'", "from './design/Agent'")
                       .replace('<Agent {...agentProps}', '<Agent pageId={page.pageId} {...agentProps}'), encoding='utf-8')
        props = design / 'props.ts'
        if props.exists():
            # 初始化的默认语言归应用层配置，避免宿主固定 props 压过 Creator 的页面设置。
            props.write_text(props.read_text(encoding='utf-8').replace("{ locale: 'zh-CN' }", '{}'), encoding='utf-8')
        # 展示规则可能由 Creator 清理；TS 投影始终从当前 JSON 重新编译，不能沿用旧 CSS。
        from app.services.agent_ui_design_presentation import presentation_files
        for path, source in presentation_files(read_presentations(project.project_dir)).items():
            (project.project_dir / path).write_text(source, encoding='utf-8')
        business_pages = [page for page in pages if has_business_body(page)]
        files = {f'src/pages/page-{index}.tsx': (project.project_dir / f'src/pages/page-{index}.tsx').read_text(encoding='utf-8')
                 for index, page in enumerate(pages) if page in business_pages}
        controls = _validate_files(project, files, business_pages, inspection.node_executable, run_id)
        (design / 'pages.tsx').write_text(_host_files(pages, controls)['src/design/pages.tsx'], encoding='utf-8')
        validate_native_action_composition(project.project_dir, pages)
        # 平台更新共享 Host 同样影响全部使用页，不能只按 Creator 回执计算确认范围。
        after = _snapshot(project.project_dir)
        all_changed = [path for path in before.keys() | after.keys() if before.get(path) != after.get(path)]
        shared_changed = any(path not in business_paths | {'src/design/presentation.json', 'src/design/presentation.ts'}
                             for path in all_changed)
        affected = [page['pageId'] for page in pages if shared_changed or page['pageId'] in targets]
        evidence['affectedPageIds'] = affected
        _check_cancel(should_cancel)
        # SDK 初始化样板沿用其既有 lint 排除；真实改动的应用层文件必须显式检查。
        changed_sources = sorted({path for task in evidence['tasks'] for path in task['changedFiles']
                                  if Path(path).suffix in {'.ts', '.tsx'} and (project.project_dir / path).is_file()})
        if changed_sources:
            _run([str(inspection.node_executable), str(pnpm_cli), 'exec', 'eslint', '--no-ignore', *changed_sources],
                 project.workspace, project.project_dir, project.root / 'logs/generated-creator-lint.log', run_id)
        built = build_agent_ui_design_project(project, node_executable=inspection.node_executable,
            pnpm_cli=pnpm_cli, run_id=run_id, check_cancel=lambda: _check_cancel(should_cancel))
        _check_cancel(should_cancel)
        facts = hashlib.sha256(_json({'requirement': requirement_spec, 'product': product_plan}).encode()).hexdigest()
        revision = hashlib.sha256((facts + project.identity + built.source_digest).encode()).hexdigest()
        evidence.update(status='built', factsDigest=facts, revision=revision, sourceDigest=built.source_digest,
                        buildDigest=built.build_digest, environmentIdentity=built.identity,
                        pages=design_page_records(pages),
                        files=[{'path': path, 'sha256': digest} for path, digest in _snapshot(built.project_dir).items()])
        _write_record(project.root / 'environment.json', {'identity': built.identity,
            'sourceDigest': built.source_digest, 'buildDigest': built.build_digest})
        _write_record(record, evidence)
        validate_design_project(built)
        return AgentUiGeneratedDesign(built, revision, facts, tuple(evidence['pages']), tuple(affected))
    except Exception as exc:
        evidence['status'] = 'cancelled' if isinstance(exc, UiDesignStreamCancelled) else 'generation_failed'
        _write_record(record, evidence)
        raise
