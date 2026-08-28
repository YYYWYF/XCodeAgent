"""构造并校验不重复 ProductPlan 事实的 UI Manifest。"""

from __future__ import annotations

import hashlib
import re
from typing import Any


UI_MANIFEST_SCHEMA_VERSION = "ui-manifest.v3"

_STATIC_ATTRIBUTE_TEMPLATE = r"\b{attribute}\s*=\s*['\"]([^'\"]+)['\"]"
# 表达式绑定的 data-* 属性（如 data-information-item-id={item.itemId}、
# data-control-id={`${m.itemId}-display`}）。静态分析无法解析运行时表达式，
# 其值不会被 _attribute 提取，判为"缺失"。用于在缺失报错时给出定向修复提示。
_EXPR_ATTRIBUTE_TEMPLATE = r"\b{attribute}\s*=\s*\{{"
_INTERACTIVE_TAGS = {
    "a",
    "Button",
    "Checkbox",
    "DatePicker",
    "Input",
    "InputNumber",
    "Radio",
    "Radio.Button",
    "Radio.Group",
    "Segmented",
    "Select",
    "Switch",
    "TimePicker",
    "Upload",
}
_BUSINESS_DISPLAY_TAGS = {
    "Descriptions",
    "List",
    "ProDescriptions",
    "ProList",
    "ProTable",
    "Statistic",
    "Table",
}
# 装饰性/容器组件：既非业务交互控件也非业务展示组件，不承载 ProductPlan
# actionId 或 informationItemId。例如 Result（空状态/成功提示页）常带 onClick
# 做"返回"交互、Empty/Spin/Alert 是纯 UI 反馈，校验器不应把它们当作未绑定的
# 业务控件报错。这类标签完全豁免 unowned_interactions 与 unowned_displays 校验。
_DECORATIVE_TAGS = {
    "Alert",
    "Drawer",
    "Empty",
    "Modal",
    "Result",
    "Skeleton",
    "Spin",
}
_LEGACY_PRODUCT_FACT_KEYS = {
    "description",
    "display_items",
    "menu_path",
    "name",
    "path",
    "preview_states",
    "responsive_targets",
    "role_variants",
    "route_path",
    "theme_variants",
    "controls",
}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从列表值中保留 JSON 对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _attribute(attrs: str, name: str) -> str:
    """从 JSX 开始标签中读取静态字符串属性。"""

    match = re.search(_STATIC_ATTRIBUTE_TEMPLATE.format(attribute=re.escape(name)), attrs)
    return match.group(1).strip() if match else ""


def _has_dynamic_binding(attrs: str, name: str) -> bool:
    """检测 JSX 开始标签中某属性是否以 ``name={{...}}`` 表达式形式绑定。

    静态分析无法解析运行时表达式的值，但识别到表达式绑定即可知道该标签已绑定
    该属性（值未知），不应再判为 unowned 或缺失。用于兼容 ``.map()`` 回调里
    ``data-information-item-id={m.itemId}`` 这种合理的 React 动态写法。
    """

    return bool(re.search(_EXPR_ATTRIBUTE_TEMPLATE.format(attribute=re.escape(name)), attrs))


# 从 .map() 数据源数组字面量里提取产品 id 字面量。key 为 "itemId" 或 "actionId"。
# 形如 ``const cards = [{ itemId: 'a', ... }, { itemId: 'b', ... }]`` → ['a', 'b']。
# 这是静态分析 ``<Tag data-information-item-id={m.itemId}>`` 绑定值的唯一可靠途径：
# 模型把多个 ProductPlan id 放进数组用 .map() 渲染时，id 仍以字面量形式存在于源码。
_MAP_SOURCE_ID_RE = r"{key}\s*:\s*['\"]([^'\"]+)['\"]"


def _extract_map_source_ids(code: str, key: str) -> list[str]:
    """从代码里所有 ``key: 'xxx'`` 字面量提取 id（去重保序）。

    用于解析 ``.map()`` 回调里 ``{m.itemId}`` 动态绑定的运行时值：模型把
    ProductPlan 的 itemId/actionId 写进数组字面量再循环渲染时，这些 id 仍以
    字符串字面量存在于源码，可静态提取后与 expected 集合精确匹配。
    """

    matches = re.findall(_MAP_SOURCE_ID_RE.format(key=re.escape(key)), code)
    seen: list[str] = []
    for value in matches:
        value = value.strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def _expression_bound_hint(code: str, attribute: str) -> str:
    """当静态校验判"缺少"某 data-* 绑定时，检测代码里是否存在该属性的表达式绑定。

    存在则说明模型可能把 id 放进了 .map() 回调变量或模板字符串，静态校验解析
    不了运行时表达式，需要回喂模型改写字面量。未检测到则返回空串（不追加提示）。
    """

    if not re.search(_EXPR_ATTRIBUTE_TEMPLATE.format(attribute=re.escape(attribute)), code):
        return ""
    return (
        f" 检测到 `{attribute}={{...}}` 表达式绑定（如 .map() 回调变量或模板字符串），"
        "静态校验无法解析运行时表达式，一律判为缺失。请把每一项展开为独立 JSX 元素，"
        f"在标签上直接写死字符串字面量（如 `{attribute}=\"<id>\"`）。"
    )


def _expected_ids(page: dict[str, Any], collection: str, key: str) -> list[str]:
    """按 ProductPlan 原顺序读取稳定产品 ID。"""

    return [
        str(item.get(key) or "").strip()
        for item in _dict_items(page.get(collection))
        if str(item.get(key) or "").strip()
    ]


def _jsx_opening_tags(code: str) -> list[tuple[str, str, bool, bool]]:
    """扫描 JSX 标签，返回 ``(tag, attrs, self_closing, is_closing)``。

    - 普通开标签 ``<Tag ...>`` → ``(tag, attrs, False, False)``
    - 自闭合标签 ``<Tag .../>`` → ``(tag, attrs, True, False)``
    - 闭合标签 ``</Tag>`` → ``(tag, "", False, True)``

    保留表达式内部箭头函数中的 ``>`` 字符（通过 brace_depth 保护）。
    """

    tags: list[tuple[str, str, bool, bool]] = []
    index = 0
    length = len(code)
    while index < length:
        start = code.find("<", index)
        if start < 0 or start + 1 >= length:
            break
        # 闭合标签 </Tag> —— 提取标签名用于维护祖先栈
        if code[start + 1] == "/":
            close_match = re.match(r"\s*([A-Za-z][\w.]*)", code[start + 2 :])
            if close_match:
                tags.append((close_match.group(1), "", False, True))
                gt = code.find(">", start + 2)
                index = gt + 1 if gt >= 0 else start + 2
            else:
                index = start + 2
            continue
        if code[start + 1] in "!?":
            index = start + 2
            continue
        name_match = re.match(r"[A-Za-z][\w.]*", code[start + 1 :])
        if not name_match:
            index = start + 1
            continue
        tag = name_match.group(0)
        attrs_start = start + 1 + len(tag)
        if attrs_start < length and not (
            code[attrs_start].isspace() or code[attrs_start] in "/>"
        ):
            index = attrs_start
            continue

        cursor = attrs_start
        brace_depth = 0
        quote = ""
        while cursor < length:
            char = code[cursor]
            if quote:
                if char == "\\":
                    cursor += 2
                    continue
                if char == quote:
                    quote = ""
            elif char in {'"', "'", "`"}:
                quote = char
            elif char == "{":
                brace_depth += 1
            elif char == "}" and brace_depth:
                brace_depth -= 1
            elif char == ">" and brace_depth == 0:
                self_closing = cursor > attrs_start and code[cursor - 1] == "/"
                tags.append((tag, code[attrs_start:cursor], self_closing, False))
                cursor += 1
                break
            cursor += 1
        index = max(cursor, start + 1)
    return tags


def inspect_ui_code_bindings(code: str) -> dict[str, Any]:
    """从 TSX 静态标记提取产品操作、信息项和未归属控件。

    维护 JSX 祖先栈：当业务展示组件（Statistic/Table 等）嵌套在已绑定
    ``data-information-item-id`` 或 ``data-preview-only="true"`` 的父容器内时，
    视为该信息项的子展示，不重复要求自身绑定。
    """

    actions: dict[str, list[str]] = {}
    interaction_effects: dict[str, str] = {}
    step_effects: dict[str, dict[str, str]] = {}
    information_items: dict[str, list[str]] = {}
    unowned_interactions: list[str] = []
    unowned_displays: list[str] = []
    # 祖先栈：(tag_name, has_information_item_id, has_preview_only)
    ancestor_stack: list[tuple[str, bool, bool]] = []
    # 预提取 .map() 数据源里的产品 id 字面量：模型用 ``cards.map((m) =>
    # <Tag data-information-item-id={m.itemId}>)`` 渲染多个 ProductPlan 项时，
    # id 仍以字面量存在于数组定义，可静态解析后与 expected 精确匹配。
    source_item_ids = _extract_map_source_ids(code, "itemId")
    source_action_ids = _extract_map_source_ids(code, "actionId")
    for tag, attrs, self_closing, is_closing in _jsx_opening_tags(code):
        if is_closing:
            # 从栈顶向下找最近的同名标签，弹出它及之上的所有标签
            for i in range(len(ancestor_stack) - 1, -1, -1):
                if ancestor_stack[i][0] == tag:
                    del ancestor_stack[i:]
                    break
            continue
        action_id = _attribute(attrs, "data-action-id")
        information_item_id = _attribute(attrs, "data-information-item-id")
        control_id = _attribute(attrs, "data-control-id")
        interaction_effect = _attribute(attrs, "data-ui-effect")
        action_step_id = _attribute(attrs, "data-action-step-id")
        preview_only = _attribute(attrs, "data-preview-only").lower() == "true"
        # 动态表达式绑定（{m.itemId} 等）：静态分析取不到具体值，但标签已绑定该属性。
        # 把 .map() 数据源里提取到的 id 全部登记为已绑定（control_id 未知用占位），
        # 这样动态写法不再被判"缺失"或 unowned。无数据源时仅标记已绑定、不补 id。
        action_dynamic = _has_dynamic_binding(attrs, "data-action-id")
        item_dynamic = _has_dynamic_binding(attrs, "data-information-item-id")
        if action_dynamic and not action_id:
            for source_id in source_action_ids:
                actions.setdefault(source_id, [])
                if not actions[source_id]:
                    actions[source_id].append("")
        if item_dynamic and not information_item_id:
            for source_id in source_item_ids:
                information_items.setdefault(source_id, [])
                if not information_items[source_id]:
                    information_items[source_id].append("")
        # 检查祖先链：只要有一个祖先绑定了 informationItemId 或 preview-only，
        # 当前展示组件就被视为该信息项的子展示，不单独要求绑定。
        ancestor_has_item = any(s[1] for s in ancestor_stack)
        ancestor_has_preview = any(s[2] for s in ancestor_stack)
        if action_id:
            actions.setdefault(action_id, [])
            if control_id and control_id not in actions[action_id]:
                actions[action_id].append(control_id)
            if interaction_effect and action_id not in interaction_effects:
                interaction_effects[action_id] = interaction_effect
            if interaction_effect and action_step_id:
                step_effects.setdefault(action_id, {})[action_step_id] = interaction_effect
        if information_item_id:
            information_items.setdefault(information_item_id, [])
            if control_id and control_id not in information_items[information_item_id]:
                information_items[information_item_id].append(control_id)
        decorative = tag in _DECORATIVE_TAGS
        # 交互控件判定以组件类型为准：只有 _INTERACTIVE_TAGS 白名单内的真交互控件
        # （Button/Input/Select 等）才算交互控件。不再用 _INTERACTION_ATTRIBUTE_RE
        # 兜底匹配 onClick/onFinish 等属性——否则 ProForm/Form（onFinish 表单提交回调）、
        # 容器 div（onClick 委托）会被误判为未绑定 actionId 的交互控件。白名单外的
        # 组件即使带交互属性，要么是容器（其内嵌按钮才是 action）、要么是装饰，都不
        # 该要求绑 actionId。真交互控件已在白名单内，带 onClick 时仍会被判，不会漏。
        interactive = tag in _INTERACTIVE_TAGS and not decorative
        if (
            interactive
            and not action_id
            and not information_item_id
            and not preview_only
            and not action_dynamic
            and not item_dynamic
        ):
            unowned_interactions.append(tag)
        if (
            tag in _BUSINESS_DISPLAY_TAGS
            and not decorative
            and not information_item_id
            and not preview_only
            and not ancestor_has_item
            and not ancestor_has_preview
            and not item_dynamic
        ):
            unowned_displays.append(tag)
        # 非自闭合标签入栈，供后续子组件检查祖先。
        # 动态绑定的标签同样视为已绑定 informationItemId，让内嵌展示组件被豁免。
        has_item = bool(information_item_id) or item_dynamic
        if not self_closing:
            ancestor_stack.append((tag, has_item, preview_only))
    return {
        "actions": actions,
        "interaction_effects": interaction_effects,
        "step_effects": step_effects,
        "information_items": information_items,
        "unowned_interactions": sorted(set(unowned_interactions)),
        "unowned_displays": sorted(set(unowned_displays)),
    }


def validate_ui_design_code(page: dict[str, Any], code: str) -> list[str]:
    """校验 TSX 只视觉展开 ProductPlan 已声明的业务语义。"""

    inspection = inspect_ui_code_bindings(code)
    expected_actions = _expected_ids(page, "actions", "actionId")
    expected_items = _expected_ids(page, "information_items", "itemId")
    actual_actions = set(inspection["actions"])
    actual_items = set(inspection["information_items"])
    errors: list[str] = []
    if actual_actions != set(expected_actions):
        missing = sorted(set(expected_actions) - actual_actions)
        unknown = sorted(actual_actions - set(expected_actions))
        message = (
            "业务操作标记必须与 ProductPlan actions 完全一致"
            f"（缺少：{missing or '无'}；越界：{unknown or '无'}）。"
        )
        if missing:
            message += _expression_bound_hint(code, "data-action-id")
        errors.append(message)
    if actual_items != set(expected_items):
        missing = sorted(set(expected_items) - actual_items)
        unknown = sorted(actual_items - set(expected_items))
        message = (
            "业务信息标记必须与 ProductPlan information_items 完全一致"
            f"（缺少：{missing or '无'}；越界：{unknown or '无'}）。"
        )
        if missing:
            message += _expression_bound_hint(code, "data-information-item-id")
        errors.append(message)
    for kind, expected, actual in (
        ("action", expected_actions, inspection["actions"]),
        ("information item", expected_items, inspection["information_items"]),
    ):
        missing_controls = [item_id for item_id in expected if not actual.get(item_id)]
        if missing_controls:
            message = f"以下 {kind} 缺少静态 data-control-id：{', '.join(missing_controls)}。"
            message += _expression_bound_hint(code, "data-control-id")
            errors.append(message)
    interface_actions = [
        str(action.get("actionId") or "").strip()
        for action in _dict_items(page.get("actions"))
        if isinstance(action.get("behavior"), dict)
        and action["behavior"].get("type") == "interface"
    ]
    missing_effects = [
        action_id
        for action_id in interface_actions
        if not inspection["interaction_effects"].get(action_id)
    ]
    if missing_effects:
        errors.append(
            "以下界面行为缺少静态 data-ui-effect：" + "、".join(missing_effects) + "。"
        )
    missing_step_effects: list[str] = []
    for action in _dict_items(page.get("actions")):
        action_id = str(action.get("actionId") or "").strip()
        behavior = action.get("behavior") if isinstance(action.get("behavior"), dict) else {}
        if behavior.get("type") != "sequence":
            continue
        for step in _dict_items(behavior.get("steps")):
            step_id = str(step.get("stepId") or "").strip()
            if step.get("type") == "interface" and not inspection["step_effects"].get(action_id, {}).get(step_id):
                missing_step_effects.append(f"{action_id}/{step_id}")
    if missing_step_effects:
        errors.append(
            "以下组合界面步骤缺少静态 data-action-step-id 与 data-ui-effect："
            + "、".join(missing_step_effects)
            + "。"
        )
    if inspection["unowned_interactions"]:
        errors.append(
            "以下交互控件没有绑定 ProductPlan actionId，也未标记 data-preview-only=\"true\"："
            + "、".join(inspection["unowned_interactions"])
            + "。"
        )
    if inspection["unowned_displays"]:
        errors.append(
            "以下业务展示组件没有绑定 ProductPlan informationItemId，也未标记 "
            "data-preview-only=\"true\"："
            + "、".join(inspection["unowned_displays"])
            + "。"
        )
    return errors


def build_ui_page_manifest(
    page: dict[str, Any],
    *,
    page_key: str,
    code_path: str = "",
    code: str = "",
    status: str = "pending",
    template_id: str = "",
    template_source_path: str = "",
    error: str = "",
) -> dict[str, Any]:
    """从 ProductPlan 页面和真实 TSX 构造无重复产品事实的页面清单。"""

    inspection = inspect_ui_code_bindings(code) if code else {
        "actions": {},
        "interaction_effects": {},
        "step_effects": {},
        "information_items": {},
        "unowned_interactions": [],
        "unowned_displays": [],
    }
    errors = validate_ui_design_code(page, code) if code else []
    code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest() if code else ""
    expected_actions = _expected_ids(page, "actions", "actionId")
    expected_items = _expected_ids(page, "information_items", "itemId")
    action_bindings_passed = set(inspection["actions"]) == set(expected_actions) and all(
        inspection["actions"].get(action_id) for action_id in expected_actions
    )
    information_bindings_passed = set(inspection["information_items"]) == set(
        expected_items
    ) and all(
        inspection["information_items"].get(item_id) for item_id in expected_items
    )
    no_unowned_business_ui = not inspection["unowned_interactions"] and not inspection[
        "unowned_displays"
    ]
    result: dict[str, Any] = {
        "pageId": str(page.get("pageId") or page.get("id") or "").strip(),
        "page_key": page_key,
        "preview_path": f"/page/{_preview_slug(page_key)}",
        "code_path": code_path,
        "status": status,
        "bindings": {
            "actions": [
                {
                    "actionId": action_id,
                    "controlIds": inspection["actions"].get(action_id, []),
                    **(
                        {"uiEffect": inspection["interaction_effects"][action_id]}
                        if inspection["interaction_effects"].get(action_id)
                        else {}
                    ),
                    **(
                        {
                            "stepEffects": [
                                {"stepId": step_id, "uiEffect": effect}
                                for step_id, effect in inspection["step_effects"].get(action_id, {}).items()
                            ]
                        }
                        if inspection["step_effects"].get(action_id)
                        else {}
                    ),
                }
                for action_id in expected_actions
            ],
            "information_items": [
                {
                    "informationItemId": item_id,
                    "controlIds": inspection["information_items"].get(item_id, []),
                }
                for item_id in expected_items
            ],
        },
        "verification": {
            "status": "passed" if code and not errors else ("failed" if code else "pending"),
            "code_sha256": code_sha256,
            "checks": [
                {
                    "id": "product-action-bindings",
                    "status": "passed" if action_bindings_passed else "failed",
                },
                {
                    "id": "product-information-bindings",
                    "status": "passed" if information_bindings_passed else "failed",
                },
                {
                    "id": "no-unowned-business-ui",
                    "status": "passed" if no_unowned_business_ui else "failed",
                },
            ],
            "errors": errors,
        },
    }
    if code:
        result["code"] = code
        result["code_sha256"] = code_sha256
    if template_id:
        result["template_id"] = template_id
    if template_source_path:
        result["template_source_path"] = template_source_path
    if error:
        result["error"] = error[:500]
    return result


def _preview_slug(page_key: str) -> str:
    """把 PageKey 转为仅供设计预览使用的 kebab-case 路径。"""

    spaced = re.sub(r"(?<!^)(?=[A-Z])", "-", str(page_key or "page"))
    return re.sub(r"-+", "-", spaced).strip("-").lower() or "page"


def persisted_ui_manifest(ui_designs: dict[str, Any]) -> dict[str, Any]:
    """移除运行时源码和旧产品事实副本，生成正式落盘 UI Manifest。"""

    pages: list[dict[str, Any]] = []
    for page in _dict_items(ui_designs.get("pages")):
        cleaned = {
            key: value
            for key, value in page.items()
            if key not in _LEGACY_PRODUCT_FACT_KEYS and key != "code"
        }
        cleaned["bindings"] = {
            "actions": ui_action_bindings(page),
            "information_items": ui_information_bindings(page),
        }
        if not cleaned.get("preview_path") and page.get("route_path"):
            cleaned["preview_path"] = page.get("route_path")
        if "verification" not in cleaned:
            cleaned["verification"] = {
                "status": "legacy_unverified",
                "code_sha256": str(page.get("code_sha256") or ""),
                "checks": [],
                "errors": ["旧 UI Manifest 尚未按 ui-manifest.v3 重新校验。"],
            }
        pages.append(cleaned)
    return {
        "schema_version": UI_MANIFEST_SCHEMA_VERSION,
        "confirmation_status": ui_designs.get("confirmation_status"),
        "product_plan_sha256": ui_designs.get("product_plan_sha256"),
        "pages": pages,
    }


def ui_action_bindings(page: dict[str, Any]) -> list[dict[str, Any]]:
    """读取 v2 action 映射，并兼容旧 controls 数组。"""

    bindings = page.get("bindings") if isinstance(page.get("bindings"), dict) else {}
    actions = _dict_items(bindings.get("actions"))
    if actions:
        return actions
    return [
        {
            "actionId": str(control.get("actionId") or "").strip(),
            "controlIds": [str(control.get("controlId") or "").strip()],
            **(
                {"uiEffect": str(control.get("uiEffect") or control.get("localEffect") or "").strip()}
                if str(control.get("uiEffect") or control.get("localEffect") or "").strip()
                else {}
            ),
            **(
                {"stepEffects": _dict_items(control.get("stepEffects"))}
                if _dict_items(control.get("stepEffects"))
                else {}
            ),
        }
        for control in _dict_items(page.get("controls"))
        if str(control.get("actionId") or "").strip()
    ]


def ui_information_bindings(page: dict[str, Any]) -> list[dict[str, Any]]:
    """读取 v2 information item 映射，并兼容旧 display_items 数组。"""

    bindings = page.get("bindings") if isinstance(page.get("bindings"), dict) else {}
    items = _dict_items(bindings.get("information_items"))
    if items:
        return items
    return [
        {
            "informationItemId": str(item.get("informationItemId") or "").strip(),
            "controlIds": [str(item.get("controlId") or "").strip()],
        }
        for item in _dict_items(page.get("display_items"))
        if str(item.get("informationItemId") or "").strip()
    ]


def present_ui_pages(ui_designs: dict[str, Any], product_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """仅为确认界面临时投影 ProductPlan 文案，不写回 UI Manifest。"""

    product_pages = {
        str(page.get("pageId") or "").strip(): page
        for page in _dict_items(product_plan.get("pages"))
    }
    return [
        {
            **page,
            "name": str(product_pages.get(str(page.get("pageId") or ""), {}).get("name") or page.get("pageId") or ""),
            "path": str(product_pages.get(str(page.get("pageId") or ""), {}).get("path") or ""),
            "description": str(product_pages.get(str(page.get("pageId") or ""), {}).get("description") or ""),
        }
        for page in _dict_items(ui_designs.get("pages"))
    ]
