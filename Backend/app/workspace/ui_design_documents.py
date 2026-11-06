"""UiDesign 的可审阅 Markdown；文件身份只由真实资产派生。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from app.workspace.spec_documents import ui_designs_json_path


def ui_designs_markdown_path(state: dict[str, Any]) -> Path:
    """将设计说明与内部清单放在同一正式产物目录。"""
    return ui_designs_json_path(state).with_suffix(".md")


def render_ui_designs_markdown(manifest: dict[str, Any]) -> str:
    """展示当前原型来源及可编辑的逐页修改要求，隐藏结构字段保留在内部清单。"""
    status = {"confirmed": "已确认", "skipped": "已跳过"}.get(manifest.get("confirmation_status"), "待确认")
    lines = ["# UI 设计说明", "", f"- 状态：{status}", "",
        "请结合当前原型审阅。可编辑每页「修改要求」标记之间的内容；来源记录由平台维护。",
        "修改要求先进入原设计修订流程，完成后必须重新确认。模拟预览不代表真实业务联调通过。", ""]
    for index, page in enumerate(manifest.get("pages", [])):
        lines.extend([f"## 页面 {page['pageId']}", ""])
        asset = page.get("design_asset")
        if isinstance(asset, dict):
            lines.extend(["- 原型：Agent UI 多文件工程（官方 Mock）",
                f"- 当前修订：`{asset.get('revision', '')}`",
                f"- 源码摘要：`{asset.get('sourceDigest', '')}`",
                f"- 构建摘要：`{asset.get('buildDigest', '')}`"])
        else:
            lines.extend(["- 原型：单页设计稿", f"- 源码摘要：`{page.get('code_sha256', '')}`"])
        evidence = page.get("asset_confirmation") or {}
        for package in evidence.get("packages", []):
            lines.append(f"- 运行包：`{package['name']}@{package['version']}`")
        lines.extend(["", "### 修改要求", "", f"<!-- ui-notes:{index}:start -->",
            str(page.get("design_notes") or ""), f"<!-- ui-notes:{index}:end -->", ""])
    return "\n".join(lines)


def _edited_notes(manifest: dict[str, Any], markdown: str) -> dict[str, str]:
    """只允许修改用户可见说明，不从 Markdown 接受路径、hash 或确认状态。"""
    expected = render_ui_designs_markdown(manifest)
    pattern = r"(<!-- ui-notes:(\d+):start -->\n)([\s\S]*?)(\n<!-- ui-notes:\2:end -->)"
    original = list(re.finditer(pattern, expected))
    edited = list(re.finditer(pattern, markdown))
    if (len(original) != len(edited) or [item[2] for item in original] != [item[2] for item in edited]
            or re.sub(pattern, r"\1\4", expected) != re.sub(pattern, r"\1\4", markdown)):
        raise ValueError("UI 设计说明的来源记录或页面标记已变化。请保留平台记录，仅编辑每页的「修改要求」。")
    return {page["pageId"]: edited[index][3].strip() for index, page in enumerate(manifest.get("pages", []))
            if edited[index][3].strip() != str(page.get("design_notes") or "").strip()}


def prepare_ui_markdown_revision(state: dict[str, Any], manifest: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """确认前同步可见编辑并撤销目标页确认；具体变更继续交原 Formal Revision 路由。"""
    path = ui_designs_markdown_path(state)
    if not path.is_file() or path.is_symlink():
        raise ValueError("UI 设计说明缺失或不是受控文件，请刷新设计说明后重试。")
    if path.stat().st_size > 200_000:
        raise ValueError("UI 设计说明过大，请缩短修改要求后重试。")
    notes = _edited_notes(manifest, path.read_text(encoding="utf-8"))
    if not notes:
        return manifest, ""
    updated = {**manifest, "confirmation_status": "pending_user_confirmation", "pages": [
        {**{key: value for key, value in page.items() if key != "asset_confirmation"},
         "status": "pending", "design_notes": notes[page["pageId"]]}
        if page["pageId"] in notes else page for page in manifest.get("pages", [])]}
    request = "\n".join(f"页面 {page_id} 的 UI 设计说明修改为：{note or '清除原修改要求'}" for page_id, note in notes.items())
    if len(request) > 20_000:
        raise ValueError("UI 设计修改要求超过 20000 字符，请缩短后重新提交。")
    return updated, request


def _write_markdown(path: Path, content: str) -> None:
    """先完整写入同目录临时文件，再原子切换，避免读到截断的设计说明。"""
    descriptor, temporary = tempfile.mkstemp(prefix=".ui-designs-", suffix=".md", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_ui_designs_markdown(state: dict[str, Any], manifest: dict[str, Any]) -> str:
    """更新平台来源时保留尚未提交的 Markdown 修改，不静默吞掉用户编辑。"""
    path = ui_designs_markdown_path(state)
    previous_path = ui_designs_json_path(state)
    if path.is_symlink() or previous_path.is_symlink():
        raise ValueError("UI 设计正式文档不能使用符号链接。")
    notes: dict[str, str] = {}
    if path.is_file() and previous_path.is_file():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        notes = _edited_notes(previous, path.read_text(encoding="utf-8"))
    visible = {**manifest, "pages": [{**page, "design_notes": notes.get(page["pageId"], page.get("design_notes", ""))}
                                    for page in manifest.get("pages", [])]}
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_ui_designs_markdown(visible)
    _write_markdown(path, content)
    return content
