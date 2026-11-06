"""设计阶段的页面级公开 props 与受控样式，不接管官方 Plugin 实现。"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


def read_presentations(project_dir: Path) -> dict[str, Any]:
    """未定制时沿用官方默认；已保存的页面展示资产必须随修订保留。"""
    path = project_dir / "src/design/presentation.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError("页面展示资产必须为页面 ID 到展示配置的映射。")
    return value


def validate_presentation(value: Any) -> dict[str, Any]:
    """只开放静态展示 props 与局部 CSS，拒绝传输、权限及可执行配置。"""
    if not isinstance(value, dict) or set(value) != {"props", "styles"}:
        raise ValueError("页面展示必须仅包含 props 和 styles。")
    props, styles = value["props"], value["styles"]
    if not isinstance(props, dict) or not set(props) <= {
        "welcome", "suggestions", "locales", "locale", "theme", "assistantPanelWidth",
    }:
        raise ValueError("只能修改官方 Agent 的公开展示 props，不能修改连接或业务能力。")
    if not isinstance(styles, list) or len(styles) > 80:
        raise ValueError("页面样式必须为有界规则列表。")
    for rule in styles:
        if not isinstance(rule, dict) or set(rule) != {"selector", "declarations"}:
            raise ValueError("样式规则必须包含 selector 和 declarations。")
        selector, declarations = rule["selector"], rule["declarations"]
        # 禁止选择器列表和 CSS 转义，保证每条规则都只能位于当前页面根节点内。
        if (not isinstance(selector, str) or not selector.strip() or len(selector) > 240
                or not re.fullmatch(r"[\w\s.\-#:\[\]=>+~*()\"'^$|]+", selector)):
            raise ValueError("样式选择器超出页面展示范围。")
        if not isinstance(declarations, dict) or not declarations or len(declarations) > 40:
            raise ValueError("样式声明必须为非空有界对象。")
        for name, content in declarations.items():
            if not re.fullmatch(r"(?:--)?[a-z][a-z0-9-]*", name):
                raise ValueError("样式属性名无效。")
            if (not isinstance(content, str) or len(content) > 400
                    or re.search(r"[{};<>@\\]|url\s*\(|expression\s*\(|image-set\s*\(", content, re.I)):
                raise ValueError("样式不能加载外部资源或注入其他规则。")
    return value


def presentation_files(presentations: dict[str, Any]) -> dict[str, str]:
    """把已校验展示资产编译成页面隔离 CSS，并让实际 SDK 类型检查所有 props。"""
    compiled: dict[str, Any] = {}
    for page_id, raw in presentations.items():
        value = validate_presentation(raw)
        scope = ".agent-design[data-design-page=" + json.dumps(page_id) + "]"
        rules = []
        for rule in value["styles"]:
            selector = scope if rule["selector"] == ":scope" else scope + " " + rule["selector"]
            body = ";".join(f"{name}:{content}" for name, content in rule["declarations"].items())
            rules.append(selector + "{" + body + "}")
        compiled[page_id] = {"props": value["props"], "css": "\n".join(rules)}
    return {
        "src/design/presentation.json": json.dumps(presentations, ensure_ascii=False),
        "src/design/presentation.ts": "import type { AgentProps } from '@za38.03/agent-ui-runtime/ui'\n"
            "type Presentation = { props: Pick<AgentProps, 'welcome' | 'suggestions' | 'locales' | 'locale' | 'theme' | 'assistantPanelWidth'>; css: string }\n"
            "export const pagePresentation: Record<string, Presentation | undefined> = "
            + json.dumps(compiled, ensure_ascii=False) + "\n",
    }


