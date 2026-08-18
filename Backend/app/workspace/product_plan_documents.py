from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workflow_artifact_root


def _text_items(value: Any) -> list[str]:
    """把列表转换为可渲染的非空文本。"""

    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _bullet_items(value: Any) -> str:
    """把字符串列表渲染为 Markdown 项目符号。"""

    return "\n".join(f"- {item}" for item in _text_items(value)) or "- 无"


def _information_items(value: Any) -> str:
    """把结构化业务信息项渲染为可审核的 Markdown。"""

    if not isinstance(value, list):
        return "- 无"
    lines = [
        f"- `{item.get('itemId', '')}` {item.get('label', '业务信息')}：{item.get('description', '')}"
        for item in value
        if isinstance(item, dict)
    ]
    return "\n".join(lines) or "- 无"


def _action_behavior(item: dict[str, Any]) -> str:
    """把产品行为渲染成不包含 endpoint 等技术细节的审核文本。"""

    behavior = item.get("behavior") if isinstance(item.get("behavior"), dict) else {}
    behavior_type = str(behavior.get("type") or "business")
    labels = {
        "business": "业务结果",
        "navigation": "页面跳转",
        "interface": "界面行为",
        "external": "外部目标",
        "sequence": "组合行为",
    }
    target = ""
    if behavior_type == "navigation":
        target = f"；目标页面 `{behavior.get('targetPageId', '')}`"
    elif behavior_type == "external":
        target = f"；外部目标 {behavior.get('externalTarget', '')}"
    elif behavior_type == "sequence":
        steps = [
            f"`{step.get('stepId', '')}` {step.get('expectedResult', '')}"
            for step in behavior.get("steps", [])
            if isinstance(step, dict)
        ]
        target = f"；步骤 {' → '.join(steps)}"
    return (
        f"{labels.get(behavior_type, behavior_type)}；"
        f"预期结果 {behavior.get('expectedResult', item.get('description', ''))}{target}"
    )


def render_product_plan_markdown(plan: dict[str, Any]) -> str:
    """把 ProductPlan 渲染成供产品审核的 Markdown 文档。"""

    app = plan.get("app") if isinstance(plan.get("app"), dict) else {}
    page_sections: list[str] = []
    for page in plan.get("pages", []):
        if not isinstance(page, dict):
            continue
        actions = [
            f"- `{item.get('actionId', '')}` {item.get('name', '页面操作')}："
            f"{item.get('description', '')}；{_action_behavior(item)}"
            for item in page.get("actions", [])
            if isinstance(item, dict)
        ]
        page_sections.append(
            "\n".join(
                [
                    f"### {page.get('name', page.get('pageId', '未命名页面'))}",
                    "",
                    f"- 页面 ID：`{page.get('pageId', '')}`",
                    f"- 路由：`{page.get('path', '')}`",
                    f"- 页面目标：{page.get('goal', page.get('description', '待补充'))}",
                    f"- 允许角色：{'、'.join(_text_items(page.get('allowed_roles'))) or '待补充'}",
                    f"- 页面跳转：{'、'.join(_text_items(page.get('navigation_targets'))) or '无'}",
                    "",
                    "业务信息：",
                    _information_items(page.get("information_items")),
                    "",
                    "核心操作：",
                    *(actions or ["- 无"]),
                    "",
                    "产品验收标准：",
                    _bullet_items(page.get("acceptance_criteria")),
                ]
            )
        )
    return "\n".join(
        [
            f"# {app.get('name', '未命名应用')}产品规划",
            "",
            f"- 摘要：{app.get('summary', '待补充')}",
            f"- 状态：{plan.get('confirmation_status', 'draft')}",
            f"- 版本：{plan.get('version', '0.1.0')}",
            "",
            "## 页面与用户操作",
            "",
            *(page_sections or ["- 暂无页面"]),
            "",
            "## 产品级验收标准",
            "",
            _bullet_items(plan.get("product_acceptance_criteria")),
            "",
        ]
    )


def product_plan_markdown_path(state: dict[str, Any]) -> Path:
    """返回 ProductPlan Markdown 正式路径。"""

    return workflow_artifact_root(state) / "plans" / "product-plan.md"


def product_plan_json_path(state: dict[str, Any]) -> Path:
    """返回 ProductPlan JSON 正式路径。"""

    return workflow_artifact_root(state) / "plans" / "product-plan.json"


def edited_product_plan_markdown(
    state: dict[str, Any],
    plan: dict[str, Any],
) -> str | None:
    """读取产品在确认前直接修改的 ProductPlan Markdown。"""

    path = product_plan_markdown_path(state)
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    return content if content != render_product_plan_markdown(plan) else None


def write_product_plan_documents(state: dict[str, Any], plan: dict[str, Any]) -> tuple[str, str]:
    """原子边界内连续写入 ProductPlan JSON 与 Markdown。"""

    json_path = product_plan_json_path(state)
    markdown_path = product_plan_markdown_path(state)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_product_plan_markdown(plan), encoding="utf-8")
    return str(markdown_path), str(json_path)
