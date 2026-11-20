"""将已校验旧设计作为当前产品修订的输入，不授予预览或确认资格。"""

from __future__ import annotations

from typing import Any

from app.services.agent_ui_design_project import AgentUiDesignProject
from app.services.ui_design_agent_surfaces import project_agent_design_pages


def revision_project(state: dict[str, Any], page: dict[str, Any]) -> tuple[AgentUiDesignProject, dict[str, Any]]:
    """产品变化时仍验证旧资产全部证据，且只允许页面身份及顺序稳定的增量修订。"""
    from app.services.agent_ui_design_projection import _project_from_asset, current_project, design_facts_digest

    facts = design_facts_digest(state)
    asset = page.get('design_asset')
    if isinstance(asset, dict) and asset.get('factsDigest') == facts:
        return current_project(state, page)
    project, asset = _project_from_asset(state, page)
    pages = project_agent_design_pages(state['requirement_spec'], state['product_plan'])
    if asset['pageIds'] != [item['pageId'] for item in pages]:
        raise ValueError('产品页面集合或顺序已变化，请重新生成全部智能体页面。')
    return project, asset
