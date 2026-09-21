"""读取前端页面模板源码，并校验源码与冻结包中的模板资源。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.workspace.spec_documents import REPOSITORY_ROOT


SOURCE_PAGE_TEMPLATES_DIR = (
    REPOSITORY_ROOT / "Frontend" / "src" / "renderer" / "src" / "templates"
)


def resolve_page_templates_dir() -> Path:
    """按当前运行布局定位唯一的页面模板资源目录。"""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve() / "app" / "page_templates"
    return SOURCE_PAGE_TEMPLATES_DIR


def validate_page_templates(root: Path | None = None) -> Path:
    """验证每个模板都具备唯一 id、清单和可读取的 TSX 源码。"""

    templates_dir = root or resolve_page_templates_dir()
    if not templates_dir.is_dir():
        raise RuntimeError(f"页面模板目录不存在：{templates_dir}")

    template_ids: set[str] = set()
    for entry in sorted(templates_dir.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        index_path = entry / "index.tsx"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            template_id = str(manifest.get("id") or "").strip()
            if not template_id or template_id in template_ids:
                raise ValueError("模板 id 为空或重复")
            index_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError, AttributeError) as exc:
            raise RuntimeError(f"页面模板资源不完整：{entry}") from exc
        template_ids.add(template_id)

    if not template_ids:
        raise RuntimeError(f"页面模板目录为空：{templates_dir}")
    return templates_dir


def load_template_source(template_id: str) -> str:
    """按 manifest.id 读取模板 TSX，供页面设计稿生成使用。"""

    template_id = str(template_id or "").strip()
    if not template_id:
        raise ValueError("load_template_source: template_id 为空。")
    templates_dir = resolve_page_templates_dir()
    if not templates_dir.is_dir():
        raise ValueError(f"load_template_source: 模板目录不存在：{templates_dir}。")

    for entry in templates_dir.iterdir():
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if str(manifest.get("id") or "").strip() != template_id:
            continue
        index_path = entry / "index.tsx"
        if not index_path.is_file():
            raise ValueError(
                f"load_template_source: 模板 {template_id} 缺少 index.tsx：{index_path}。"
            )
        return index_path.read_text(encoding="utf-8")

    raise ValueError(
        f"load_template_source: 未找到 id={template_id} 的页面模板，"
        f"已扫描目录：{templates_dir}。"
    )
