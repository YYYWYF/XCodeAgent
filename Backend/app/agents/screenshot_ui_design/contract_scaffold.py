from __future__ import annotations

import html
import json
import re
from difflib import SequenceMatcher
from typing import Any


_DYNAMIC_CONTROL_ATTRIBUTE = re.compile(
    r"\bdata-control-id\s*=\s*\{\s*(?:`(?:\\.|[^`])*`|[^{}]*)\s*\}"
)


def _dict_items(value: object) -> list[dict[str, Any]]:
    """从未知列表值中筛出 ProductPlan 对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _attribute_text(value: object) -> str:
    """把稳定标识安全编码为 JSX 静态属性值。"""

    return html.escape(str(value or "").strip(), quote=True)


def _js_text(value: object) -> str:
    """把产品文案编码成可直接嵌入 TSX 表达式的字符串字面量。"""

    return json.dumps(str(value or "").strip(), ensure_ascii=False)


def _sample_value(item: dict[str, Any]) -> str:
    """按信息项语义给契约骨架提供克制的演示值，不引入新的业务字段。"""

    text = " ".join(
        str(item.get(key) or "").lower()
        for key in ("itemId", "label", "description")
    )
    if any(token in text for token in ("余额", "金额", "消费", "balance", "amount")):
        return "¥ 1,286.50"
    if any(token in text for token in ("token", "令牌")):
        return "2.46M"
    if any(token in text for token in ("次数", "请求", "count", "request")):
        return "12,864"
    if any(token in text for token in ("手机", "phone", "mobile")):
        return "138 **** 8888"
    if any(token in text for token in ("用户", "username", "account")):
        return "示例用户"
    if any(token in text for token in ("微信", "绑定", "认证", "status")):
        return "已完成"
    if any(token in text for token in ("语言", "language")):
        return "简体中文"
    if any(token in text for token in ("主题", "theme")):
        return "跟随系统"
    return "示例数据"


def _action_controls(actions: list[dict[str, Any]]) -> list[str]:
    """把 ProductPlan action 转成完整且可静态校验的操作控件。"""

    controls: list[str] = []
    for action in actions:
        action_id = str(action.get("actionId") or "").strip()
        if not action_id:
            continue
        name = str(action.get("name") or action_id).strip()
        behavior = action.get("behavior") if isinstance(action.get("behavior"), dict) else {}
        behavior_type = str(behavior.get("type") or "").strip()
        steps = _dict_items(behavior.get("steps")) if behavior_type == "sequence" else []
        interface_steps = [step for step in steps if step.get("type") == "interface"]
        variants = interface_steps or [None]
        for index, step in enumerate(variants):
            step_id = str(step.get("stepId") or "").strip() if isinstance(step, dict) else ""
            effect = ""
            if isinstance(step, dict):
                effect = str(
                    step.get("uiEffect")
                    or step.get("description")
                    or step.get("name")
                    or name
                ).strip()
            elif behavior_type == "interface":
                effect = str(behavior.get("expectedResult") or name).strip()
            label = name if index == 0 else f"{name} · {step_id or index + 1}"
            attributes = [
                f'data-action-id="{_attribute_text(action_id)}"',
                f'data-control-id="{_attribute_text(action_id)}-control"',
            ]
            if step_id:
                attributes.append(f'data-action-step-id="{_attribute_text(step_id)}"')
            if effect:
                attributes.append(f'data-ui-effect="{_attribute_text(effect)}"')
            danger = " danger" if any(token in name for token in ("删除", "解绑", "移除")) else ""
            button_type = ' type="primary"' if any(token in name for token in ("保存", "确认", "提交")) else ""
            controls.append(
                "        <Button"
                + button_type
                + danger
                + " "
                + " ".join(attributes)
                + f" onClick={{() => setNotice({_js_text(name + '已执行')})}}>{{{_js_text(label)}}}</Button>"
            )
    return controls


def build_contract_scaffold(page: dict[str, Any], page_key: str) -> str:
    """生成先于视觉模型的确定性 TSX 骨架，冻结全部产品事实绑定。"""

    name = str(page.get("name") or page_key).strip()
    description = str(page.get("description") or page.get("goal") or "").strip()
    information_items = _dict_items(page.get("information_items"))
    actions = _dict_items(page.get("actions"))
    item_blocks: list[str] = []
    for item in information_items:
        item_id = str(item.get("itemId") or "").strip()
        if not item_id:
            continue
        label = str(item.get("label") or item_id).strip()
        item_description = str(item.get("description") or "").strip()
        item_blocks.append(
            """        <div
          style={styles.metric}
          data-information-item-id=\"%s\"
          data-control-id=\"%s-display\"
        >
          <span style={styles.metricLabel}>{%s}</span>
          <strong style={styles.metricValue}>{%s}</strong>
          %s
        </div>"""
            % (
                _attribute_text(item_id),
                _attribute_text(item_id),
                _js_text(label),
                _js_text(_sample_value(item)),
                (
                    f"<span style={{styles.metricDescription}}>{{{_js_text(item_description)}}}</span>"
                    if item_description
                    else ""
                ),
            )
        )
    controls = _action_controls(actions)
    body = (
        "\n".join(item_blocks)
        if item_blocks
        else f"        <div style={{styles.emptyDescription}}>{{{_js_text(description or name)}}}</div>"
    )
    action_region = ""
    if controls:
        action_region = """
      <Card bordered={false} style={styles.actionsCard} bodyStyle={{ padding: 20 }}>
        <Space wrap>
%s
        </Space>
      </Card>""" % "\n".join(controls)
    return f"""import React, {{ useState }} from 'react';
import {{ Alert, Button, Card, Space, Typography }} from 'antd';

const {{ Title, Paragraph }} = Typography;

const styles: Record<string, React.CSSProperties> = {{
  page: {{ minHeight: '100%', padding: 28, background: 'var(--ant-color-bg-layout, #f5f7fb)', color: 'var(--ant-color-text, #20232a)' }},
  heading: {{ maxWidth: 760, marginBottom: 24 }},
  title: {{ margin: 0, fontSize: 28, lineHeight: 1.25, letterSpacing: -0.4 }},
  description: {{ margin: '10px 0 0', color: 'var(--ant-color-text-secondary, #697386)', lineHeight: 1.7 }},
  grid: {{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }},
  metric: {{ minHeight: 146, padding: 20, border: '1px solid var(--ant-color-border-secondary, #e7eaf0)', borderRadius: 14, background: 'var(--ant-color-bg-container, #fff)', boxShadow: '0 8px 28px rgba(31, 38, 54, 0.06)', display: 'flex', flexDirection: 'column', justifyContent: 'center' }},
  metricLabel: {{ color: 'var(--ant-color-text-secondary, #697386)', fontSize: 14, fontWeight: 500 }},
  metricValue: {{ marginTop: 12, color: 'var(--ant-color-text, #20232a)', fontSize: 27, lineHeight: 1.2, letterSpacing: -0.3 }},
  metricDescription: {{ marginTop: 10, color: 'var(--ant-color-text-tertiary, #8b93a3)', fontSize: 12, lineHeight: 1.55 }},
  actionsCard: {{ marginTop: 18, borderRadius: 14, boxShadow: '0 8px 28px rgba(31, 38, 54, 0.05)' }},
  notice: {{ marginTop: 18 }},
  emptyDescription: {{ padding: 24, borderRadius: 14, background: 'var(--ant-color-bg-container, #fff)', lineHeight: 1.7 }},
}};

const {page_key} = () => {{
  const [notice, setNotice] = useState('');
  return (
    <main style={{styles.page}}>
      <header style={{styles.heading}}>
        <Title level={{2}} style={{styles.title}}>{{{_js_text(name)}}}</Title>
        {{Boolean({_js_text(description)}) && <Paragraph style={{styles.description}}>{{{_js_text(description)}}}</Paragraph>}}
      </header>
      <section style={{styles.grid}}>
{body}
      </section>{action_region}
      {{notice && <Alert style={{styles.notice}} type=\"success\" showIcon message={{notice}} />}}
    </main>
  );
}};

export default {page_key};
"""


def _opening_tag_end(code: str, start: int) -> int:
    """定位 JSX 起始标签结尾，并跳过字符串及属性表达式中的大于号。"""

    quote = ""
    escaped = False
    brace_depth = 0
    for index in range(start, len(code)):
        char = code[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == ">" and brace_depth == 0:
            return index
    return -1


def _replace_opening_tags(code: str, replacer: Any) -> str:
    """按源码顺序安全重写 JSX 起始标签，不触碰子节点与脚本表达式。"""

    matches: list[tuple[int, int, str, str]] = []
    cursor = 0
    pattern = re.compile(r"<([A-Za-z][\w.]*)\b")
    while True:
        match = pattern.search(code, cursor)
        if match is None:
            break
        end = _opening_tag_end(code, match.start())
        if end < 0:
            break
        matches.append((match.start(), end + 1, match.group(1), code[match.start() : end + 1]))
        cursor = end + 1
    fixed = code
    for start, end, tag, opening in reversed(matches):
        replacement = replacer(tag, opening)
        if replacement != opening:
            fixed = fixed[:start] + replacement + fixed[end:]
    return fixed


def _append_attributes(opening: str, attributes: str) -> str:
    """把静态属性插入普通或自闭合 JSX 起始标签的正确位置。"""

    if opening.rstrip().endswith("/>"):
        slash = opening.rfind("/>")
        return opening[:slash].rstrip() + " " + attributes + " />"
    return opening[:-1].rstrip() + " " + attributes + ">"


def repair_contract_attributes(page: dict[str, Any], code: str) -> tuple[str, int]:
    """确定性清理越界绑定并补齐已有绑定的 controlId，不猜测业务信息归属。"""

    expected_actions = {
        str(item.get("actionId") or "").strip()
        for item in _dict_items(page.get("actions"))
        if str(item.get("actionId") or "").strip()
    }
    expected_items = [
        str(item.get("itemId") or "").strip()
        for item in _dict_items(page.get("information_items"))
        if str(item.get("itemId") or "").strip()
    ]
    expected_item_set = set(expected_items)
    changed = 0

    def normalize(tag: str, opening: str) -> str:
        """规范一条 JSX 起始标签上的业务属性。"""

        nonlocal changed
        updated = opening
        action_match = re.search(r'\bdata-action-id\s*=\s*[\'\"]([^\'\"]+)[\'\"]', updated)
        item_match = re.search(
            r'\bdata-information-item-id\s*=\s*[\'\"]([^\'\"]+)[\'\"]', updated
        )
        bound_id = (
            action_match.group(1).strip()
            if action_match and action_match.group(1).strip() in expected_actions
            else (
                item_match.group(1).strip()
                if item_match and item_match.group(1).strip() in expected_item_set
                else ""
            )
        )
        if bound_id and _DYNAMIC_CONTROL_ATTRIBUTE.search(updated):
            # 同一业务操作在动态列表的各行可复用同一静态控件标识；行数据和点击行为不变。
            updated = _DYNAMIC_CONTROL_ATTRIBUTE.sub(
                f'data-control-id="{_attribute_text(bound_id)}-control"',
                updated,
                count=1,
            )
            changed += 1
        control_match = re.search(
            r'\bdata-control-id\s*=\s*[\'\"]([^\'\"]+)[\'\"]', updated
        )
        if not item_match and control_match:
            # 仅当模型已在同一个实际控件写出了完整 itemId 时补回遗漏的
            # information 标记。绝不依据页面位置或模糊文案猜测字段归属。
            control_id = control_match.group(1).strip()
            explicit_item = next(
                (
                    item_id
                    for item_id in expected_item_set
                    if control_id in {item_id, f"{item_id}-control", f"{item_id}-display"}
                ),
                "",
            )
            if explicit_item:
                updated = _append_attributes(
                    updated,
                    f'data-information-item-id="{_attribute_text(explicit_item)}"',
                )
                item_match = re.search(
                    r'\bdata-information-item-id\s*=\s*[\'\"]([^\'\"]+)[\'\"]',
                    updated,
                )
                changed += 1
        if action_match:
            action_id = action_match.group(1).strip()
            if action_id not in expected_actions:
                updated = re.sub(
                    r'\s+data-action-id\s*=\s*[\'\"][^\'\"]+[\'\"]', "", updated, count=1
                )
                updated = re.sub(
                    r'\s+data-action-step-id\s*=\s*[\'\"][^\'\"]+[\'\"]', "", updated, count=1
                )
                updated = re.sub(
                    r'\s+data-ui-effect\s*=\s*[\'\"][^\'\"]+[\'\"]', "", updated, count=1
                )
                updated = re.sub(
                    r'\s+data-control-id\s*=\s*[\'\"][^\'\"]+[\'\"]', "", updated, count=1
                )
                if "data-preview-only=" not in updated:
                    updated = _append_attributes(updated, 'data-preview-only="true"')
                changed += 1
            elif "data-control-id=" not in updated:
                updated = _append_attributes(
                    updated, f'data-control-id="{_attribute_text(action_id)}-control"'
                )
                changed += 1
        if item_match:
            item_id = item_match.group(1).strip()
            if item_id not in expected_items:
                updated = re.sub(
                    r'\s+data-information-item-id\s*=\s*[\'\"][^\'\"]+[\'\"]', "", updated, count=1
                )
                updated = re.sub(
                    r'\s+data-control-id\s*=\s*[\'\"][^\'\"]+[\'\"]', "", updated, count=1
                )
                if "data-preview-only=" not in updated:
                    updated = _append_attributes(updated, 'data-preview-only="true"')
                changed += 1
            elif "data-control-id=" not in updated:
                updated = _append_attributes(
                    updated, f'data-control-id="{_attribute_text(item_id)}-display"'
                )
                changed += 1
        return updated

    # 未标记的信息展示必须让模型依据页面语义修复。按 DOM 顺序硬套 itemId
    # 会把不相干的数据误认成目标字段，使通用截图迁移产生“校验通过但内容错位”。
    fixed = _replace_opening_tags(code, normalize)

    def mark_unowned_buttons(tag: str, opening: str) -> str:
        """保留辅助按钮与占位链接外观，明确其不属于 ProductPlan 操作。"""

        nonlocal changed
        if tag not in {"Button", "button", "a"}:
            return opening
        if tag == "a" and not re.search(r'\bhref\s*=\s*[\'\"]#[\'\"]', opening):
            return opening
        if "data-action-id=" in opening or "data-preview-only=" in opening:
            return opening
        changed += 1
        return _append_attributes(opening, 'data-preview-only="true"')

    fixed = _replace_opening_tags(fixed, mark_unowned_buttons)
    return fixed, changed


def repair_equivalent_action_aliases(page: dict[str, Any], code: str) -> tuple[str, int]:
    """仅把语义近乎相同的重复 ProductPlan 操作绑定到同一个可见按钮。"""

    actions = {
        str(item.get("actionId") or "").strip(): item
        for item in _dict_items(page.get("actions"))
        if str(item.get("actionId") or "").strip()
    }
    present = set(re.findall(r'\bdata-action-id\s*=\s*[\'\"]([^\'\"]+)[\'\"]', code))
    changed = 0
    for missing_id, missing in actions.items():
        if missing_id in present:
            continue
        missing_behavior = missing.get("behavior") if isinstance(missing.get("behavior"), dict) else {}
        if missing_behavior.get("type") not in {"business", "interface"}:
            continue
        ranked: list[tuple[float, str]] = []
        for existing_id in present & actions.keys():
            existing = actions[existing_id]
            existing_behavior = existing.get("behavior") if isinstance(existing.get("behavior"), dict) else {}
            if missing_behavior.get("type") != existing_behavior.get("type"):
                continue
            description_similarity = SequenceMatcher(
                None, str(missing.get("description") or ""), str(existing.get("description") or "")
            ).ratio()
            result_similarity = SequenceMatcher(
                None,
                str(missing_behavior.get("expectedResult") or ""),
                str(existing_behavior.get("expectedResult") or ""),
            ).ratio()
            if description_similarity >= 0.72 and result_similarity >= 0.90:
                ranked.append((description_similarity + result_similarity, existing_id))
        if len(ranked) != 1:
            # 多个近似按钮无法确定归属时仍交给模型修复，不能猜错业务控件。
            continue
        existing_id = ranked[0][1]
        button = next(
            (
                match
                for match in re.finditer(r"<(button|Button)\b", code)
                if (end := _opening_tag_end(code, match.start())) >= 0
                and re.search(
                    rf'\bdata-action-id\s*=\s*[\'\"]{re.escape(existing_id)}[\'\"]',
                    code[match.start() : end + 1],
                )
            ),
            None,
        )
        if button is None:
            continue
        open_end = _opening_tag_end(code, button.start())
        close = re.search(rf"</{button.group(1)}\s*>", code[open_end + 1 :], re.IGNORECASE)
        if close is None:
            continue
        close_end = open_end + 1 + close.end()
        effect = (
            f' data-ui-effect="{_attribute_text(missing_behavior.get("expectedResult") or missing.get("name"))}"'
            if missing_behavior.get("type") == "interface"
            else ""
        )
        wrapper = (
            '<span style={{display: "contents"}} '
            f'data-action-id="{_attribute_text(missing_id)}" '
            f'data-control-id="{_attribute_text(missing_id)}-alias"{effect}>'
        )
        code = code[: button.start()] + wrapper + code[button.start() : close_end] + "</span>" + code[close_end:]
        present.add(missing_id)
        changed += 1
    return code, changed

