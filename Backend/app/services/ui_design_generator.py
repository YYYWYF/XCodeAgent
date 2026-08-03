"""根据 wireframe-redline-generate 技能为单个页面生成低保真线框图设计稿。

设计稿是一段自包含的 HTML（带内联样式），由 LLM 按 wireframe-redline-generate
SKILL.md 的规范生成，落盘到工作区 .xcodeagent/wireframes/<pageId>.html，供
UI确认节点展示与后续节点引用。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.builtin_skills import read_builtin_skill_md


logger = logging.getLogger(__name__)


WIREFRAME_SKILL_NAME = "wireframe-redline-generate"
WIREFRAMES_RELATIVE_DIR = ".xcodeagent/wireframes"

_FALLBACK_SKILL_NOTE = (
    "(wireframe-redline-generate SKILL.md 未找到，请仍按 annotated redline "
    "wireframe 规范生成：左侧浏览器边框内扁平灰盒线框 + 编号 pin ①–⑤ + 右侧 "
    "SPEC 面板，单一强调色仅用于 pin/spec 编号，其余灰度，输出自包含 HTML。)"
)

# 匹配 <!doctype html> ... </html>（大小写无关，DOTALL），用于从模型返回中提取 HTML。
_HTML_DOCUMENT_RE = re.compile(
    r"<!doctype html>.*?</html>", re.IGNORECASE | re.DOTALL
)


def _wireframe_skill_document() -> str:
    """读取 wireframe-redline-generate 技能 SKILL.md 全文，缺失时返回降级提示。"""

    content = read_builtin_skill_md(WIREFRAME_SKILL_NAME)
    return content if content else _FALLBACK_SKILL_NOTE


def _page_brief(page: dict[str, Any]) -> str:
    """把单个页面信息组织成 prompt 友好的简述。"""

    page_id = str(page.get("pageId") or page.get("id") or "").strip()
    name = str(page.get("name") or "").strip()
    path = str(page.get("path") or "/").strip()
    module_id = str(page.get("module_id") or "").strip()
    description = str(page.get("description") or "").strip()
    components = page.get("components")
    lines = [
        f"- pageId: {page_id or '(未命名)'}",
        f"- name: {name or '(未命名)'}",
        f"- path: {path}",
    ]
    if module_id:
        lines.append(f"- module_id: {module_id}")
    if description:
        lines.append(f"- description: {description}")
    if isinstance(components, list) and components:
        lines.append(
            "- components: " + "、".join(str(item) for item in components if item)
        )
    return "\n".join(lines)


def _build_wireframe_prompt(page: dict[str, Any]) -> str:
    """组合页面信息与技能全文，约束模型只返回单个页面的线框图 HTML。"""

    skill_document = _wireframe_skill_document()
    return (
        "You are a UI wireframe generation model for an app-generation workflow.\n"
        "Generate ONE annotated redline wireframe HTML for the single page described "
        "below, following the wireframe-redline-generate skill strictly.\n"
        "Output rules:\n"
        "- Return a single self-contained HTML document (<!doctype html>...</html>) "
        "with all CSS inlined in a <style> block. No external scripts, no external "
        "assets except font CDN links.\n"
        "- The wireframe must reflect THIS page's purpose and components: use the "
        "page name as the browser-chrome title and the H1, and lay out greybox "
        "blocks that match the page's described regions/components. Do NOT copy the "
        "skill's Acme landing example verbatim — adapt the block layout to this page.\n"
        "- Keep it low-fidelity greybox with numbered pins ①–⑤ and a right-hand SPEC "
        "panel, exactly as the skill specifies. One accent color only, on pins/spec "
        "numbers.\n"
        "- Output the HTML document ONLY. Do not wrap it in markdown fences, do not "
        "add commentary before or after.\n\n"
        "--- PAGE TO WIREFRAME ---\n"
        f"{_page_brief(page)}\n"
        "--- END PAGE ---\n\n"
        "--- INJECTED wireframe-redline-generate SKILL.md (content inlined) ---\n"
        + skill_document
        + "\n--- END INJECTED SKILL.md ---\n"
    )


def _extract_html_document(text: str) -> str:
    """从模型返回文本中提取首个完整 HTML 文档，找不到时返回原文清理结果。"""

    match = _HTML_DOCUMENT_RE.search(text)
    if match:
        return match.group(0)
    # 兜底：去掉 markdown 代码围栏后整段返回，尽量保住 HTML 片段。
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def generate_page_wireframe(page: dict[str, Any]) -> str:
    """调用 LLM 为单个页面生成线框图设计稿 HTML。

    返回自包含 HTML 文档字符串。模型调用失败时抛出，由调用方（Graph 节点）
    捕获并持久化为节点失败状态。
    """

    settings = Settings.from_env()
    prompt = _build_wireframe_prompt(page)
    result = create_chat_model(settings).bind(
        max_tokens=settings.default_max_tokens
    ).invoke(prompt)
    content = _coerce_content_text(getattr(result, "content", ""))
    html = _extract_html_document(content)
    logger.info(
        "ui_wireframe_generated page_id=%s content_chars=%s html_chars=%s",
        str(page.get("pageId") or page.get("id") or ""),
        len(content),
        len(html),
    )
    return html


def wireframe_dir(workspace: str) -> Path:
    """返回工作区下的线框图目录路径。"""

    return Path(workspace).expanduser().resolve() / WIREFRAMES_RELATIVE_DIR


def persist_wireframe(workspace: str, page_id: str, html: str) -> str:
    """把单页设计稿 HTML 原子写入工作区，返回写入后的绝对路径。"""

    safe_page_id = _safe_filename(page_id)
    target_dir = wireframe_dir(workspace)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_page_id}.html"
    tmp = target.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(target)
    return str(target)


def load_wireframe(workspace: str, page_id: str) -> str | None:
    """读取已落盘的单页设计稿 HTML，缺失时返回 None。"""

    safe_page_id = _safe_filename(page_id)
    target = wireframe_dir(workspace) / f"{safe_page_id}.html"
    if not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _safe_filename(page_id: str) -> str:
    """把 pageId 规整为安全的文件名片段，避免路径穿越。"""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(page_id or "page")).strip("-")
    return cleaned or "page"
