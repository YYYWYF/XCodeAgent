from __future__ import annotations

import json
from typing import Any

from .models import ScreenshotPageMapping, ScreenshotVisualObservation
from .prompts import _product_contract_text, _visual_reference_text


def build_screenshot_render_prompt(
    page: dict[str, Any],
    page_key: str,
    mapping: ScreenshotPageMapping,
    observations: list[ScreenshotVisualObservation],
    global_style_summary: str,
) -> str:
    """只用已确认产品事实和截图观察生成视觉 TSX，避免通用骨架污染布局。"""

    evidence = _visual_reference_text(mapping, observations, global_style_summary)
    return (
        f"Create one complete React TSX component named {page_key}. Return code only.\n"
        "The confirmed ProductPlan below is the sole source of functions and business data. "
        "The verified SCREENSHOT OBSERVATION below is the visual source. Render the route content only; the "
        "same application sidebar is inserted deterministically outside every page component. "
        "Do not render an <aside>, global navigation, brand lockup, or account sidebar yourself. "
        "Export a named component with `export default ComponentName;` or "
        "`export default function ComponentName()`.\n\n"
        "VISUAL REQUIREMENTS:\n"
        "- Match the screenshot's major sections, nesting, reading order, relative widths, "
        "density, alignment, font hierarchy, whitespace, palette, borders, and radii.\n"
        "- Preserve tables as tables, forms as forms, charts as charts, horizontal controls as "
        "horizontal controls, and the screenshot's most prominent regions at similar scale.\n"
        "- Use the screenshot's CJK/system font character and color relationships with readable "
        "contrast. Do not replace a complex page with a uniform card grid or generic template.\n"
        "- Keep the initial state visually similar to the screenshot. Do not add a preview-state "
        "switcher, extra toolbar, duplicate app shell, or generic placeholder text.\n"
        "- Use responsive CSS Grid/Flex; the reference dimensions guide proportions, not exact "
        "pixel coordinates. Preserve useful visual controls even when they are only preview "
        "affordances.\n\n"
        "- The isolated preview does NOT load Tailwind or any utility CSS framework. "
        "Never rely on Tailwind className tokens such as flex, px-4, bg-white, or text-[#111827]. "
        "Use complete inline style objects or a self-contained <style> element with real CSS rules.\n\n"
        "PRODUCT FUNCTION REQUIREMENTS:\n"
        "- Render every information item as a visible display with its exact literal "
        "data-information-item-id and a unique literal data-control-id.\n"
        "- Render every action as a real visible control with its exact literal data-action-id "
        "and a unique literal data-control-id. Never put required data-* IDs in dynamic "
        "expressions, template strings, or map() variables: write each required ID as a static "
        "JSX string literal on a visible element. Preserve the action behavior. Every interface "
        "action also requires a literal data-ui-effect describing its expectedResult; each "
        "interface step in a sequence requires literal data-action-step-id and data-ui-effect.\n"
        "- Screenshot-only controls have data-preview-only=\"true\" and do not invent "
        "ProductPlan actions. All visible buttons must carry either data-action-id or "
        "data-preview-only=\"true\".\n\n"
        "CONFIRMED PRODUCT PAGE:\n"
        + json.dumps(page, ensure_ascii=False, indent=2)
        + "\n\nSCREENSHOT OBSERVATION:\n"
        + evidence
        + "\n\nEXACT BINDING CHECKLIST:\n"
        + _product_contract_text(page)
    )


def build_screenshot_visual_repair_prompt(
    page: dict[str, Any],
    page_key: str,
    mapping: ScreenshotPageMapping,
    observations: list[ScreenshotVisualObservation],
    global_style_summary: str,
    previous_code: str,
    repair_instruction: str,
) -> str:
    """在保持业务绑定与已有有效结构的前提下修复截图视觉差异。"""

    return (
        build_screenshot_render_prompt(
            page, page_key, mapping, observations, global_style_summary
        )
        + "\n\nImprove the following existing candidate. Keep all correct business bindings, "
        "interactive behavior, and visual regions. Address the highest impact differences "
        "in layout, typography, palette, and density. Return the complete TSX only.\n\n"
        + "REPAIR INSTRUCTION:\n"
        + repair_instruction
        + "\n\nEXISTING TSX:\n"
        + previous_code
    )
