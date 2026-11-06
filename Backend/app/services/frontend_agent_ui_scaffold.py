"""Build 前只读复核共享 Agent UI；依赖与资产写入仅由 T21/T23 执行。"""

from pathlib import Path
from typing import Any

from app.services.agent_ui_build_contract import project_agent_ui_build_contracts
from app.services.agent_ui_dependency_plan import read_object, safe_path
from app.workspace.plan_documents import load_project_plan_json


def verify_frontend_agent_ui_build(
    workspace: str | Path | None, tasks: list[dict[str, Any]],
    *, unit_ids: list[str] | None = None,
) -> None:
    """仅复核本轮执行切片的共享页面；独立 Agent 不依赖前端交接。"""
    active_units = set(unit_ids) if unit_ids is not None else None
    references = [(task.get('source_refs') or {}).get('agent_ui') for task in tasks
                  if active_units is None or task.get('unit_id') in active_units]
    page_ids = {unit.removeprefix('page:') for unit in active_units or () if unit.startswith('page:')}
    if active_units is not None and not page_ids and not any(references):
        return
    if workspace is None:
        if any(references):
            raise ValueError('Agent UI Build 缺少当前工作区。')
        return
    root = Path(workspace).expanduser().resolve()
    product_path = safe_path(root, '.devagentstudio/plans/product-plan.json')
    if not product_path.is_file():
        if any(references):
            raise ValueError('Agent UI Build 缺少正式 ProductPlan。')
        return
    product = read_object(product_path)
    # 用切片 Unit 对照正式产品事实，页面不能通过删除任务来源绕过转入前置。
    from app.services.ui_design_agent_surfaces import project_ui_design_pages
    pages = [page for page in project_ui_design_pages(product) if page.get('agent_surfaces')
             and (active_units is None or page.get('pageId') in page_ids)]
    if not pages and not any(references):
        return
    technical = load_project_plan_json(safe_path(root, '.devagentstudio/plans/technical-plan.json'),
                                       hydrate_detail_designs=True)
    contracts = project_agent_ui_build_contracts(product, technical, workspace=root)
    for reference in references:
        if reference and (not isinstance(reference, dict)
                          or reference != contracts.get(reference.get('pageId'))):
            raise ValueError('Agent UI 任务合同已过期，请按当前共享来源重新编译任务。')
