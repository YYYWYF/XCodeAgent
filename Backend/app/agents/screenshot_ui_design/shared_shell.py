from __future__ import annotations

import html
import json
import re
from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any

from .images import PreparedUiScreenshot, detect_sidebar_width_ratio
from .models import ScreenshotVisualObservation


_SIDEBAR_WORDS = ("sidebar", "side rail", "side navigation", "侧边栏", "侧栏", "侧边导航")
_NAV_WORDS = ("navigation", "nav", "menu", "导航", "菜单")
_FOOTER_WORDS = ("footer", "secondary", "辅助", "底部")
_SHELL_ITEM_WORDS = (
    "sidebar", "left_nav", "side_nav", "auxiliary", "account_entry",
    "侧边", "导航", "辅助入口", "当前账号入口",
)


def _value(value: object) -> str:
    """把不可信产品文案转为安全的 TSX 字符串字面量。"""

    return json.dumps(str(value or "").strip(), ensure_ascii=False)


def _attribute(value: object) -> str:
    """把稳定标识转为安全的 JSX 静态属性。"""

    return html.escape(str(value or "").strip(), quote=True)


def _page_id(page: dict[str, Any]) -> str:
    """读取 ProductPlan 页面标识。"""

    return str(page.get("pageId") or page.get("id") or "").strip()


def _items(value: object) -> list[dict[str, Any]]:
    """筛出 ProductPlan 数组内的对象成员。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _region_text(observation: ScreenshotVisualObservation, words: tuple[str, ...]) -> list[str]:
    """从符合职责的视觉区域提取可见文字，保持截图阅读顺序。"""

    candidates = [
        [text.strip() for text in region.visible_text if text.strip()]
        for region in observation.layout_regions
        if any(word in f"{region.name} {region.role}".lower() for word in words)
    ]
    return max(candidates, key=len, default=[])


def _has_sidebar(observation: ScreenshotVisualObservation) -> bool:
    """仅在截图确实包含侧边导航时启用共享侧边栏。"""

    return any(
        any(word in f"{region.name} {region.role}".lower() for word in _SIDEBAR_WORDS)
        for region in observation.layout_regions
    )


def _navigation_name(value: object) -> str:
    """归一化截图短菜单名与 ProductPlan 的“××页面/××页”名称。"""

    name = re.sub(r"\s*[>›»→]\s*$", "", str(value or "").strip().casefold())
    return re.sub(r"(?:页面|页)$", "", name).strip()


def _sidebar_width(observations: list[ScreenshotVisualObservation]) -> int:
    """从多张参考图取侧边栏宽度中位数，避免单张异常尺寸污染所有页面。"""

    widths = [
        region.bounds.width
        for observation in observations
        for region in observation.layout_regions
        if any(word in f"{region.name} {region.role}".lower() for word in _SIDEBAR_WORDS)
        and any(
            word in f"{region.name} {region.role}".lower()
            for word in ("shell", "container", "rail", "容器", "侧边栏")
        )
        and not any(
            word in f"{region.name} {region.role}".lower()
            for word in ("footer", "brand", "导航组", "辅助", "底部")
        )
        and region.bounds.width < observation.source_viewport_width * 0.5
    ]
    return max(176, min(288, round(median(widths)))) if widths else 224


def _is_dark(observations: list[ScreenshotVisualObservation]) -> bool:
    """根据首张参考图的主背景判断共享壳的明暗基调。"""

    if not observations:
        return False
    match = re.search(r"#[0-9a-fA-F]{6}\b", observations[0].dominant_background)
    if not match:
        return False
    color = match.group()[1:]
    rgb = [int(color[index : index + 2], 16) for index in (0, 2, 4)]
    return sum(rgb) / 3 < 110


@dataclass(frozen=True, slots=True)
class SharedShell:
    """保存单次截图任务中全部页面共用的侧边栏视觉事实。"""

    enabled: bool
    width: int
    brand: tuple[str, ...]
    primary_labels: tuple[str, ...]
    footer_labels: tuple[str, ...]
    account_label: str
    dark: bool
    width_ratio: float = 0.0


def derive_shared_shell(
    observations: list[ScreenshotVisualObservation],
    pages: list[dict[str, Any]] | None = None,
    screenshots: list[PreparedUiScreenshot] | None = None,
) -> SharedShell:
    """从任意 1–10 张截图归纳一份任务级侧边栏，不依赖固定页面名称。"""

    sources = [item for item in observations if _has_sidebar(item)]
    if not sources:
        return SharedShell(False, 0, (), (), (), "", False)
    image_by_hash = {
        item.reference.sha256: item.overview for item in (screenshots or [])
    }
    measured_ratios = [
        ratio
        for observation in sources
        if (image := image_by_hash.get(observation.screenshot_sha256)) is not None
        if (ratio := detect_sidebar_width_ratio(
            image, hint_x=observation.page_content_bounds.x
        )) is not None
    ]
    fallback_width = _sidebar_width(sources)
    width_ratio = (
        float(median(measured_ratios))
        if measured_ratios
        else float(median(
            fallback_width / item.source_viewport_width
            for item in sources if item.source_viewport_width > 0
        ))
    )
    width_ratio = max(0.08, min(0.42, width_ratio))
    sidebar_width = round(width_ratio * median(item.source_viewport_width for item in sources))
    page_names = {
        _navigation_name(page.get("name"))
        for page in (pages or [])
        if str(page.get("name") or "").strip()
    }
    nav_sequences = [
        [text.strip() for text in region.visible_text if text.strip()]
        for observation in sources
        for region in observation.layout_regions
        if region.bounds.x < observation.source_viewport_width * 0.35
        and region.bounds.width < observation.source_viewport_width * 0.5
        and any(word in f"{region.name} {region.role}".lower() for word in _NAV_WORDS)
    ]
    primary = max(
        nav_sequences,
        key=lambda values: (sum(_navigation_name(text) in page_names for text in values), len(values)),
        default=[],
    )
    prefixes = [
        values[: next(index for index, text in enumerate(values) if _navigation_name(text) in page_names)]
        for values in nav_sequences
        if any(_navigation_name(text) in page_names for text in values)
    ]
    observed_brand = max(
        (_region_text(item, ("brand", "logo", "品牌", "标识")) for item in sources),
        key=len,
        default=[],
    )[:2]
    brand_first = Counter(prefix[0] for prefix in prefixes if prefix).most_common(1)
    brand = observed_brand or ([brand_first[0][0]] if brand_first else [])
    if brand_first and not observed_brand:
        subtitle = Counter(
            prefix[1]
            for prefix in prefixes
            if len(prefix) > 1 and prefix[0] == brand[0]
        ).most_common(1)
        if subtitle:
            brand.append(subtitle[0][0])
    account_candidates = [
        text
        for observation in sources
        for region in observation.layout_regions
        if region.bounds.x < observation.source_viewport_width * 0.35
        and any(
            word in f"{region.name} {region.role}".lower()
            for word in ("sidebar account", "account entry", "user avatar", "footer user", "user profile", "侧边栏账号", "账户入口", "用户头像")
        )
        for text in region.visible_text
        if text.strip()
    ]
    account_label = Counter(account_candidates).most_common(1)[0][0].strip() if account_candidates else "账号"
    primary = [
        text for text in primary
        if text not in brand and not (account_label and text.startswith(account_label))
    ]
    last_page_index = max(
        (index for index, text in enumerate(primary) if _navigation_name(text) in page_names),
        default=-1,
    )
    footer = [
        text for text in primary[last_page_index + 1 :]
        if text != account_label and not text.startswith(account_label) and text not in brand
    ] if last_page_index >= 0 else []
    if not footer:
        footer = max(
            (
                [text for text in region.visible_text if text.strip()]
                for observation in sources
                for region in observation.layout_regions
                if region.bounds.x < observation.source_viewport_width * 0.35
                and any(word in f"{region.name} {region.role}".lower() for word in _FOOTER_WORDS)
                and not any(word in f"{region.name} {region.role}".lower() for word in ("user", "account", "账号"))
            ),
            key=len,
            default=[],
        )
    if last_page_index >= 0:
        primary = primary[: last_page_index + 1]
    return SharedShell(
        True,
        sidebar_width,
        tuple(brand[:2]),
        tuple(primary[:12]),
        tuple(footer[:10]),
        account_label,
        _is_dark(sources),
        width_ratio,
    )


def _is_shell_navigation(action: dict[str, Any], pages: list[dict[str, Any]]) -> bool:
    """只把全局页面切换归入共享栏，保留内容区的业务跳转。"""

    behavior = action.get("behavior") if isinstance(action.get("behavior"), dict) else {}
    if behavior.get("type") != "navigation":
        return False
    target = str(behavior.get("targetPageId") or "").strip()
    destination = next((page for page in pages if _page_id(page) == target), None)
    if destination is None:
        return False
    action_id = str(action.get("actionId") or "").lower()
    name = str(action.get("name") or "").strip().casefold()
    return action_id.startswith("navigate_") or name == str(destination.get("name") or "").strip().casefold()


def _is_shell_information(item: dict[str, Any]) -> bool:
    """识别 ProductPlan 明确属于侧边栏的信息项。"""

    descriptor = f"{item.get('itemId') or ''} {item.get('label') or ''}".lower()
    return any(word in descriptor for word in _SHELL_ITEM_WORDS)


def split_page_contract(
    page: dict[str, Any],
    pages: list[dict[str, Any]],
    shell: SharedShell,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """将已确认页面事实拆为内容区与共享壳，最终仍按完整原页校验。"""

    if not shell.enabled:
        return page, [], []
    actions = _items(page.get("actions"))
    information = _items(page.get("information_items"))
    shell_labels = set(shell.primary_labels) | set(shell.footer_labels)
    shell_names = {_navigation_name(label) for label in shell_labels}
    shell_actions = [
        action
        for action in actions
        if _is_shell_navigation(action, pages)
        or _navigation_name(action.get("name")) in shell_names
        or "account_entry" in str(action.get("actionId") or "").lower()
    ]
    shell_items = [
        item for item in information
        if _is_shell_information(item)
        or _navigation_name(item.get("label")) in shell_names
        or (bool(shell.account_label) and str(item.get("label") or "").strip().startswith(shell.account_label))
    ]
    shell_action_ids = {str(item.get("actionId") or "") for item in shell_actions}
    shell_item_ids = {str(item.get("itemId") or "") for item in shell_items}
    content_page = {
        **page,
        "actions": [item for item in actions if str(item.get("actionId") or "") not in shell_action_ids],
        "information_items": [
            item for item in information if str(item.get("itemId") or "") not in shell_item_ids
        ],
    }
    return content_page, shell_actions, shell_items


def validate_content_without_shell(code: str, pages: list[dict[str, Any]]) -> str:
    """拒绝内容稿再次绘制含多个全局页面入口的侧边栏。"""

    if not re.search(
        r"<aside\b|className\s*=\s*['\"][^'\"]*(?:sidebar|side-nav)|"
        r"style\s*=\s*\{\s*styles\.(?:sidebar|sider)\b",
        code,
        re.IGNORECASE,
    ):
        return ""
    names = {
        _navigation_name(page.get("name"))
        for page in pages
        if str(page.get("name") or "").strip()
    }
    if sum(1 for name in names if name and name in code.casefold()) < 2:
        return ""
    return "内容稿重复生成了全局侧边栏；请移除包含跨页面导航的 <aside>，只返回本页内容区。"


def remove_generated_sidebar(code: str, pages: list[dict[str, Any]]) -> str:
    """从当前任务已生成页面中移除独立侧栏，保留其内容区供统一壳复用。"""

    if not validate_content_without_shell(code, pages):
        return ""
    openings = list(re.finditer(r"<aside\b", code, re.IGNORECASE))
    closings = list(re.finditer(r"</aside\s*>", code, re.IGNORECASE))
    if len(openings) != 1 or len(closings) != 1 or closings[0].start() < openings[0].start():
        return ""
    # 仅去掉一段可确认的全局侧栏；内容和既有交互不能因壳统一而丢失。
    return code[: openings[0].start()] + code[closings[0].end() :]


def bind_existing_sidebar_information(
    code: str, items: list[dict[str, Any]]
) -> str:
    """功能侧栏无法安全拆离时，在其真实可见区域补齐侧栏信息项。"""

    opening = re.search(r"<aside\b", code, re.IGNORECASE)
    closing = re.search(r"</aside\s*>", code, re.IGNORECASE)
    if opening is None or closing is None or closing.start() <= opening.start():
        return code
    side = code[opening.start() : closing.end()]
    for item in items:
        item_id = str(item.get("itemId") or "").strip()
        if not item_id or re.search(
            rf'\bdata-information-item-id\s*=\s*[\'\"]{re.escape(item_id)}[\'\"]',
            code,
        ):
            continue
        side = (
            '<div style={{display: "contents"}} '
            f'data-information-item-id="{_attribute(item_id)}" '
            f'data-control-id="screenshot-sidebar-{_attribute(item_id)}">'
            + side + "</div>"
        )
    return code[: opening.start()] + side + code[closing.end() :]


def _default_component(code: str) -> tuple[str, str]:
    """把模型页面的默认导出转为内部组件，留出共享壳的唯一默认导出。"""

    named_function = re.search(
        r"(?m)^\s*export\s+default\s+function\s+([A-Za-z_$][\w$]*)\s*\(", code
    )
    if named_function:
        return (
            code[: named_function.start()]
            + code[named_function.start() :].replace("export default ", "", 1),
            named_function.group(1),
        )
    named_export = re.search(
        r"(?m)^\s*export\s+default\s+([A-Za-z_$][\w$]*)\s*;?\s*$", code
    )
    if named_export:
        return code[: named_export.start()] + code[named_export.end() :], named_export.group(1)
    raise ValueError(
        "共享侧边栏需要命名页面组件及唯一的 export default；"
        "请以 export default PageName; 或 export default function PageName() 导出。"
    )


def _bound_button(action: dict[str, Any], label: str, style: str) -> str:
    """将 ProductPlan 操作写成可见、可静态校验的共享栏按钮。"""

    action_id = str(action.get("actionId") or "").strip()
    behavior = action.get("behavior") if isinstance(action.get("behavior"), dict) else {}
    effect = (
        f' data-ui-effect="{_attribute(behavior.get("expectedResult") or label)}"'
        if behavior.get("type") == "interface"
        else ""
    )
    return (
        f'<button type="button" style={{screenshotShellStyles.{style}}} '
        f'data-action-id="{_attribute(action_id)}" '
        f'data-control-id="screenshot-shell-{_attribute(action_id)}"{effect} '
        f'onClick={{() => setShellNotice({_value(label)})}}>{{{_value(label)}}}</button>'
    )


def _wrap_information(inner: str, items: list[dict[str, Any]]) -> str:
    """在现有可见区域外包裹静态信息标记，不增加截图中没有的说明文字。"""

    for item in items:
        item_id = str(item.get("itemId") or "").strip()
        inner = (
            '<div style={{display: "contents"}} '
            f'data-information-item-id="{_attribute(item_id)}" '
            f'data-control-id="screenshot-shell-{_attribute(item_id)}">'
            + inner
            + "</div>"
        )
    return inner


def compose_shared_shell(
    code: str,
    *,
    page: dict[str, Any],
    pages: list[dict[str, Any]],
    shell: SharedShell,
    shell_actions: list[dict[str, Any]],
    shell_items: list[dict[str, Any]],
) -> str:
    """为内容稿注入完全相同的侧边栏结构和样式，再交给原完整契约校验。"""

    if not shell.enabled:
        return code
    content_code, component = _default_component(code)
    current_id = _page_id(page)
    actions_by_target = {
        str(action.get("behavior", {}).get("targetPageId") or ""): action
        for action in shell_actions
        if _is_shell_navigation(action, pages)
    }
    page_by_name = {_navigation_name(item.get("name")): item for item in pages}
    primary_labels = list(shell.primary_labels) or [
        str(item.get("name") or _page_id(item)) for item in pages
    ]
    if pages and not any(_navigation_name(label) in page_by_name for label in primary_labels):
        primary_labels = [str(item.get("name") or _page_id(item)) for item in pages]
    shell_actions_by_name = {
        _navigation_name(action.get("name")): action for action in shell_actions
    }
    shell_items_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in shell_items:
        shell_items_by_name.setdefault(_navigation_name(item.get("label")), []).append(item)
    consumed_item_ids: set[str] = set()
    consumed_action_ids: set[str] = set()
    nav_nodes: list[str] = []
    for label in primary_labels:
        normalized_label = _navigation_name(label)
        target = page_by_name.get(normalized_label)
        target_id = _page_id(target) if isinstance(target, dict) else ""
        action = actions_by_target.get(target_id) or shell_actions_by_name.get(normalized_label)
        if action is not None:
            consumed_action_ids.add(str(action.get("actionId") or ""))
        style = "navActive" if target_id == current_id else "navItem"
        node = (
            _bound_button(action, label, style)
            if action is not None
            else f'<span style={{screenshotShellStyles.{style}}}>{{{_value(label)}}}</span>'
        )
        matched_items = shell_items_by_name.get(normalized_label, [])
        consumed_item_ids.update(str(item.get("itemId") or "") for item in matched_items)
        nav_nodes.append(_wrap_information(node, matched_items))
    footer_labels = [
        label for label in shell.footer_labels
        if _navigation_name(label) not in page_by_name and label != shell.account_label
    ]
    footer_actions = {
        str(action.get("name") or "").strip(): action
        for action in shell_actions
        if str(action.get("name") or "").strip() in footer_labels
    }
    footer_nodes = [
        _bound_button(footer_actions[label], label, "footerLink")
        if label in footer_actions
        else f'<span style={{screenshotShellStyles.footerLink}}>{{{_value(label)}}}</span>'
        for label in footer_labels
    ]
    consumed_action_ids.update(
        str(action.get("actionId") or "") for action in footer_actions.values()
    )
    account_action = next(
        (action for action in shell_actions if "account_entry" in str(action.get("actionId") or "").lower()),
        None,
    )
    if account_action is not None:
        consumed_action_ids.add(str(account_action.get("actionId") or ""))
    # 截图菜单的短文案不一定等于 ProductPlan 操作名；未命中的真实操作
    # 必须在同一可见侧栏保留绑定，不能在统一壳重组时静默丢失。
    nav_nodes.extend(
        _bound_button(action, str(action.get("name") or action.get("actionId") or "操作"), "navItem")
        for action in shell_actions
        if str(action.get("actionId") or "") not in consumed_action_ids
    )
    account_label = shell.account_label
    brand = shell.brand or ("应用",)
    nav_items = [
        item for item in shell_items
        if str(item.get("itemId") or "") not in consumed_item_ids
        if any(word in f"{item.get('itemId')} {item.get('label')}".lower() for word in ("nav", "sidebar", "侧边", "导航"))
    ]
    brand_items = [
        item for item in shell_items
        if item not in nav_items and str(item.get("itemId") or "") not in consumed_item_ids
        and any(word in f"{item.get('itemId')} {item.get('label')}".lower()
                for word in ("title", "brand", "logo", "标题", "品牌"))
    ]
    account_items = [
        item for item in shell_items
        if item not in nav_items and item not in brand_items
        and str(item.get("itemId") or "") not in consumed_item_ids
        and (any(word in f"{item.get('itemId')} {item.get('label')}".lower()
                 for word in ("account", "profile", "user", "账号", "用户", "头像"))
             or (bool(account_label) and str(item.get("label") or "").startswith(account_label)))
    ]
    footer_items = [
        item for item in shell_items
        if item not in nav_items and item not in brand_items and item not in account_items
        and str(item.get("itemId") or "") not in consumed_item_ids
    ]
    account_node = (
        _bound_button(account_action, account_label, "account")
        if account_action is not None
        else f'<span style={{screenshotShellStyles.account}}>{{{_value(account_label)}}}</span>'
    )
    nav_region = _wrap_information(
        f'<nav style={{screenshotShellStyles.nav}}>{"".join(nav_nodes)}</nav>', nav_items
    )
    footer_region = _wrap_information(
        f'<div style={{screenshotShellStyles.footer}}>{"".join(footer_nodes)}'
        + _wrap_information(account_node, account_items)
        + "</div>",
        footer_items,
    )
    colors = (
        ("#15181f", "#1d222b", "#f4f5f7", "#a9b1bc", "#2b323d")
        if shell.dark
        else ("#f9fafb", "#ffffff", "#111318", "#111318", "#e9edf1")
    )
    sidebar_bg, content_bg, text_color, muted_color, active_bg = colors
    sidebar_width = (
        f"'clamp(176px, {shell.width_ratio * 100:.2f}vw, 420px)'"
        if shell.width_ratio
        else str(shell.width)
    )
    brand_badge = (
        f'<span style={{screenshotShellStyles.badge}}>{{{_value(brand[1])}}}</span>'
        if len(brand) > 1
        else ""
    )
    brand_region = _wrap_information(
        f'<div style={{screenshotShellStyles.brand}}><span>{{{_value(brand[0])}}}</span>'
        + brand_badge + "</div>",
        brand_items,
    )
    return "import * as ScreenshotReact from 'react';\n" + content_code + f"""
const screenshotShellStyles: Record<string, ScreenshotReact.CSSProperties> = {{
  frame: {{ display: 'flex', minHeight: '100vh', width: '100%', background: '{content_bg}', color: '{text_color}', fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif' }},
  sidebar: {{ boxSizing: 'border-box', display: 'flex', flexDirection: 'column', width: {sidebar_width}, minWidth: {sidebar_width}, minHeight: '100vh', padding: '24px 14px 18px', background: '{sidebar_bg}', borderRight: '1px solid rgba(128, 136, 149, 0.10)' }},
  brand: {{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 14px 26px', fontSize: 'clamp(20px, 1.57vw, 30px)', fontWeight: 700, letterSpacing: -0.7 }},
  badge: {{ borderRadius: 5, padding: '3px 5px', background: '{text_color}', color: '{content_bg}', fontSize: 10, fontWeight: 500, letterSpacing: 0 }},
  nav: {{ display: 'flex', flexDirection: 'column', gap: 4 }},
  navItem: {{ display: 'flex', alignItems: 'center', width: '100%', minHeight: 'clamp(38px, 2.93vw, 56px)', padding: '8px 12px', border: 0, borderRadius: 9, background: 'transparent', color: '{muted_color}', fontSize: 'clamp(14px, 0.94vw, 18px)', textAlign: 'left', cursor: 'pointer' }},
  navActive: {{ display: 'flex', alignItems: 'center', width: '100%', minHeight: 'clamp(38px, 2.93vw, 56px)', padding: '8px 12px', border: 0, borderRadius: 9, background: '{active_bg}', color: '{text_color}', fontSize: 'clamp(14px, 0.94vw, 18px)', fontWeight: 600, textAlign: 'left', cursor: 'pointer' }},
  footer: {{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 'auto', paddingTop: 24 }},
  footerLink: {{ display: 'block', width: '100%', padding: '7px 12px', border: 0, background: 'transparent', color: '{muted_color}', fontSize: 13, textAlign: 'left', cursor: 'pointer' }},
  account: {{ display: 'block', marginTop: 12, padding: '10px 12px', border: 0, borderTop: '1px solid rgba(128, 136, 149, 0.16)', background: 'transparent', color: '{text_color}', fontSize: 13, textAlign: 'left', cursor: 'pointer' }},
  content: {{ flex: 1, minWidth: 0, background: '{content_bg}' }},
  notice: {{ padding: '6px 12px', color: '{muted_color}', fontSize: 11 }},
}};

function ScreenshotSharedPage() {{
  const [shellNotice, setShellNotice] = ScreenshotReact.useState('');
  return (
    <div style={{screenshotShellStyles.frame}}>
      <aside style={{screenshotShellStyles.sidebar}}>
        {brand_region}
        {nav_region}
        {footer_region}
        {{shellNotice && <span style={{screenshotShellStyles.notice}}>{{shellNotice}}</span>}}
      </aside>
      <section style={{screenshotShellStyles.content}}><{component} /></section>
    </div>
  );
}}

export default ScreenshotSharedPage;
"""
