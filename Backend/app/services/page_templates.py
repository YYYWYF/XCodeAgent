"""读取前端页面模板源码，并校验源码与冻结包中的模板资源。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.services.ui_design_agent_template import (
    AGENT_UI_TEMPLATE_MODULE,
    AGENT_UI_TEMPLATE_VERSION,
    component_for_surface,
)
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


def load_template_source(template_id: str, *, surface_type: str | None = None) -> str:
    """按 manifest.id 读取模板，并在调用方提供 Surface 时校验兼容性。"""

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
        if surface_type is not None:
            category = str(manifest.get("category") or "").strip()
            supported_surfaces = manifest.get("supportedSurfaces")
            expected_category = "agent" if surface_type == "standalone_page" else "business"
            if (
                surface_type not in {"standard_page", "floating_panel", "standalone_page"}
                or category not in {"business", "agent"}
                or category != expected_category
                or not isinstance(supported_surfaces, list)
                or surface_type not in supported_surfaces
            ):
                raise ValueError(
                    f"load_template_source: 模板 {template_id} 与页面 Surface "
                    f"{surface_type or 'missing'} 不兼容。"
                )
            agent_ui = manifest.get("agentUi")
            if category == "agent" and (
                not isinstance(agent_ui, dict)
                or agent_ui.get("module") != AGENT_UI_TEMPLATE_MODULE
                or agent_ui.get("component") != component_for_surface(surface_type)
                or agent_ui.get("version") != AGENT_UI_TEMPLATE_VERSION
            ):
                raise ValueError(
                    f"load_template_source: Agent 模板 {template_id} 缺少当前固定组件证据。"
                )
            if category == "business" and agent_ui is not None:
                raise ValueError(
                    f"load_template_source: 业务模板 {template_id} 不得声明 Agent UI 固定组件。"
                )
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
