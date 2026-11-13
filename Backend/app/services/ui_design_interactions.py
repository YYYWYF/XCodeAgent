"""从真实设计源码投影交互事实，并在技术模型调用前检查 UI 上游完整性。"""

from pathlib import Path
from typing import Any

from app.services.agent_action_bindings import agent_action_bindings
from app.services.agent_ui_design_content import business_design_page, has_business_body
from app.services.product_plan import project_active_agent_product_plan
from app.services.ui_design_manifest import build_ui_page_manifest, ui_action_bindings, validate_ui_design_code


def agent_project_page_bindings(
    project_dir: Path, page: dict[str, Any], page_ids: list[str],
) -> dict[str, Any]:
    """从当前多文件工程读取业务控件及效果，官方 Agent 控件仍由共享 Host 持有。"""
    page_id = str(page["pageId"])
    surfaces = page.get("agent_surfaces", [])
    business = business_design_page(page)
    code = ""
    # 独立页的页面控件同样读取实际源码，不伪造官方会话内部的控件证据。
    if has_business_body(page) and (
        business["actions"] or business.get("information_items")
    ):
        source = project_dir / f"src/pages/page-{page_ids.index(page_id)}.tsx"
        if source.is_symlink() or not source.resolve().is_relative_to(project_dir.resolve()):
            raise ValueError("Agent UI 业务页面源码不能链接到工程之外。")
        code = source.read_text(encoding="utf-8")
        errors = validate_ui_design_code(business, code)
        if errors:
            raise ValueError("业务页面未满足当前产品事实：" + "；".join(errors))
    bindings = build_ui_page_manifest(page, page_key=page_id, code=code)["bindings"]
    owned = {action_id for surface in surfaces for action_id in surface["actionIds"]}
    actions = {action["actionId"]: action for action in page.get("actions", [])}
    for binding in bindings["actions"]:
        action = actions[binding["actionId"]]
        if action["actionId"] in owned and (action.get("behavior") or {}).get("type") == "interface":
            # 效果来自已确认且由共享 Host 承担的动作，不虚构包内部控件 ID。
            binding["uiEffect"] = str(action["behavior"].get("expectedResult") or "").strip()
    bindings["agent_surfaces"] = [
        {key: surface.get(key) for key in ("agentId", "type", "actionIds", "contextItemIds")}
        for surface in surfaces
    ]
    return bindings


def require_ui_interaction_effects(product_plan: dict[str, Any], ui_designs: dict[str, Any]) -> None:
    """拒绝把缺失的已确认 UI 效果交给技术规划模型修复，跳过 UI 时沿用既有产品投影。"""
    if ui_designs.get("confirmation_status") == "skipped":
        return
    product = project_active_agent_product_plan(product_plan)
    owners = agent_action_bindings(product)
    ui_pages = {page["pageId"]: page for page in ui_designs.get("pages", [])}
    missing: list[str] = []
    for page in product.get("pages", []):
        page_id = page["pageId"]
        bindings = {item["actionId"]: item for item in ui_action_bindings(ui_pages.get(page_id, {}))}
        for action in page.get("actions", []):
            action_id = action["actionId"]
            behavior = action.get("behavior") or {}
            binding = bindings.get(action_id, {})
            if behavior.get("type") == "interface" and action_id not in owners.get(page_id, {}):
                if not str(binding.get("uiEffect") or "").strip():
                    missing.append(f"{page_id}/{action_id}")
            elif behavior.get("type") == "sequence":
                effects = {step["stepId"]: step.get("uiEffect") for step in binding.get("stepEffects", [])}
                for step in behavior.get("steps", []):
                    if step.get("type") == "interface" and not str(effects.get(step["stepId"]) or "").strip():
                        missing.append(f"{page_id}/{action_id}/{step['stepId']}")
    if missing:
        raise ValueError("UI 设计缺少本地交互效果 uiEffect：" + "、".join(missing)
                         + "。请返回设计阶段重新审阅并确认；技术规划不能修复上游 UI 事实。")
