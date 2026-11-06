"""单页重新生成时，只从当前校验通过的工程保留其他页面源码。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.agent_ui_design_project import validate_design_project
from app.services.agent_ui_design_presentation import read_presentations
from app.services.agent_ui_design_shared import read_shared_application
from app.services.agent_ui_design_content import has_business_body
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path

if TYPE_CHECKING:
    from app.services.ui_design_generation_pool import UiDesignGenerationTask


def retained_page_sources(task: UiDesignGenerationTask, pages: list[dict[str, Any]]) -> dict[str, str]:
    """首次生成完整工程；后续只保留非目标业务页，陈旧资产明确失败而非全量重画。"""
    # 延迟导入，避免预览投影与工作流装配互相初始化。
    from app.services.agent_ui_design_projection import current_project

    targets = {member.page_id for member in task.members}
    if targets == {page["pageId"] for page in pages}:
        return {}
    state = {"workspace": task.workspace, "project_id": task.project_id,
             "requirement_spec": task.requirement_spec, "product_plan": task.product_plan}
    manifest = load_ui_designs_json(ui_designs_json_path(state)) or {}
    current = [page for page in manifest.get("pages", []) if page.get("design_asset")]
    if not current:
        return {}
    project, asset = current_project(state, current[0])
    if any(page["design_asset"] != asset for page in current):
        raise ValueError("当前智能体页面的工程身份不一致，请重新生成全部智能体页面。")
    retained: dict[str, str] = {}
    for index, page in enumerate(pages):
        if page["pageId"] in targets or not has_business_body(page):
            continue
        path = f"src/pages/page-{index}.tsx"
        retained[path] = (project.project_dir / path).read_text(encoding="utf-8")
    # 读取前后的完整摘要必须一致，不能复制到读取期间被修改的源码。
    validate_design_project(project)
    return retained


def retained_presentations(task: UiDesignGenerationTask) -> dict[str, Any]:
    """换一换只清除目标页定制，其他页面已接受的展示配置继续使用。"""
    from app.services.agent_ui_design_projection import current_project

    targets = {member.page_id for member in task.members}
    state = {"workspace": task.workspace, "project_id": task.project_id,
             "requirement_spec": task.requirement_spec, "product_plan": task.product_plan}
    manifest = load_ui_designs_json(ui_designs_json_path(state)) or {}
    current = [page for page in manifest.get("pages", []) if page.get("design_asset")]
    if not current:
        return {}
    project, _asset = current_project(state, current[0])
    return {page_id: value for page_id, value in read_presentations(project.project_dir).items()
            if page_id not in targets}


def retained_agent_sources(task: UiDesignGenerationTask, pages: list[dict[str, Any]]) -> dict[str, str]:
    """重生成业务页时保留所有页面共用的应用层，不重置已接受的共享定制。"""
    from app.services.agent_ui_design_projection import current_project

    state = {'workspace': task.workspace, 'project_id': task.project_id,
             'requirement_spec': task.requirement_spec, 'product_plan': task.product_plan}
    current = [page for page in (load_ui_designs_json(ui_designs_json_path(state)) or {}).get('pages', [])
               if page.get('design_asset')]
    if not current:
        return {}
    project, _asset = current_project(state, current[0])
    result = read_shared_application(project.project_dir)
    validate_design_project(project)
    return result
