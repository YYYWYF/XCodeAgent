"""在开发阶段清理本轮已删除页面的代码、路由与设计稿。

**为什么需要它**：删除一个页面只改变了计划层面的目录，基线里那份已生成的页面代码
和设计稿并不会自己消失。而这些产物又恰好都"本轮没有变更"，在开发入口被禁用后，
用户没有任何路径能把它们清理掉 —— 需求上是删除，产物却永久残留。

本模块把删除做成一个**确定性的本地操作**：
- 目标文件由 pageId 确定推出（`frontend/src/pages/<PageKey>/`、路由条目、
  `.devagentstudio/ui-design/pages/<PageKey>/`），不需要调用模型；
- 先在只读预演里返回"将要删什么"，用户确认后才真正执行；
- 只删除能被精确证明属于该页面的东西。其它页面里指向它的链接 / 注释**只报告、
  不自动改写** —— 那是别人写的业务代码，改错比留着更糟。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.development_change_snapshot import read_change_snapshot
from app.services.repository_branch import read_workspace_branch_name

# 页面路由的唯一配置来源（与前端 `utils/pageIdentity.ts` 的约定一致）。
ROUTES_FILE_RELATIVE = Path("frontend/src/constants/routes.tsx")
PAGES_DIR_RELATIVE = Path("frontend/src/pages")
UI_DESIGN_DIR_RELATIVE = Path(WORKSPACE_ARTIFACT_DIR / "ui-design/pages")


def page_directory_name(page_id: str) -> str:
    """把 pageId 转成 React 页面目录名，与前端 `pageDirectoryFromPath` 保持一致。"""

    segments = [segment for segment in str(page_id or "").split("_") if segment]
    return "".join(segment[0].upper() + segment[1:] for segment in segments)


@dataclass
class DeletionPreview:
    """描述一次页面删除将影响哪些文件，供确认界面展示。"""

    page_id: str
    page_label: str
    directories: list[str] = field(default_factory=list)
    route_entries: list[str] = field(default_factory=list)
    referenced_by: list[str] = field(default_factory=list)

    @property
    def removable(self) -> bool:
        """至少命中一个可删除目标时才允许执行，避免空操作被当成成功。"""

        return bool(self.directories or self.route_entries)

    def to_payload(self) -> dict[str, Any]:
        """转换为 AG-UI 状态的 camelCase 载荷。"""

        return {
            "pageId": self.page_id,
            "pageLabel": self.page_label,
            "directories": list(self.directories),
            "routeEntries": list(self.route_entries),
            "referencedBy": list(self.referenced_by),
            "removable": self.removable,
        }


def deleted_page_targets(workspace: str | Path) -> dict[str, str]:
    """从本轮变更快照取已删除页面（pageId → 名称）。

    名称来自 ProductPlan，缺失时退回 pageId，仅用于让确认界面可读。
    """

    snapshot = read_change_snapshot(workspace) or {}
    deleted = {
        str(page_id): "deleted"
        for page_id, status in (snapshot.get("pages") or {}).items()
        if status == "deleted"
    }
    if not deleted:
        return {}
    labels: dict[str, str] = {}
    from app.workspace.plan_documents import load_project_plan_json

    product_path = Path(workspace).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR / "plans/product-plan.json"
    if product_path.is_file():
        try:
            product = load_project_plan_json(product_path)
        except (OSError, ValueError):
            product = {}
        for page in product.get("pages") or []:
            if isinstance(page, dict) and str(page.get("pageId") or "") in deleted:
                labels[str(page["pageId"])] = str(page.get("name") or page["pageId"])
    return {page_id: labels.get(page_id, page_id) for page_id in sorted(deleted)}


def _route_entry_pattern(page_id: str) -> re.Pattern[str]:
    """匹配该页面的路由条目对象，兼容单双引号与属性顺序。"""

    return re.compile(
        r"\{\s*path:\s*[\"']" + re.escape(page_id) + r"[\"'][^{}]*\}\s*,?\s*",
        re.MULTILINE,
    )


def preview_page_deletion(workspace: str | Path, page_id: str) -> DeletionPreview:
    """只读预演：列出该页面会被删除的目录与路由条目，不做任何修改。"""

    root = Path(workspace).expanduser().resolve()
    directory_name = page_directory_name(page_id)
    preview = DeletionPreview(
        page_id=page_id,
        page_label=deleted_page_targets(root).get(page_id, page_id),
    )
    for relative in (PAGES_DIR_RELATIVE / directory_name, UI_DESIGN_DIR_RELATIVE / directory_name):
        if (root / relative).is_dir():
            preview.directories.append(relative.as_posix())
    routes_path = root / ROUTES_FILE_RELATIVE
    if routes_path.is_file():
        try:
            content = routes_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            content = ""
        for match in _route_entry_pattern(page_id).finditer(content):
            preview.route_entries.append(" ".join(match.group(0).split()))
    # 其它页面里对它的引用只报告不修改：那是对他人业务代码的改写，必须由人决定。
    preview.referenced_by = _referencing_files(root, page_id, directory_name)
    return preview


def _referencing_files(root: Path, page_id: str, directory_name: str) -> list[str]:
    """扫描前端源码里仍指向该页面的文件（排除即将删除的页面自身）。"""

    pages_root = root / PAGES_DIR_RELATIVE
    if not pages_root.is_dir():
        return []
    pattern = re.compile(rf"\b{re.escape(page_id)}\b|\b{re.escape(directory_name)}\b")
    found: list[str] = []
    for path in sorted(pages_root.rglob("*.tsx")):
        relative = path.relative_to(root)
        if relative.parts[len(PAGES_DIR_RELATIVE.parts)] == directory_name:
            continue
        try:
            if pattern.search(path.read_text(encoding="utf-8")):
                found.append(relative.as_posix())
        except (OSError, UnicodeError):
            continue
    return found


def delete_page_artifacts(workspace: str | Path, page_id: str) -> dict[str, Any]:
    """执行页面删除：移除代码目录、设计稿目录与路由条目。

    先删文件再改路由：即使中途失败，留下的也是一条指向不存在组件的路由（构建期
    立刻报错、容易定位），而不是删了路由却留下死目录这种更难发现的残留。
    """

    root = Path(workspace).expanduser().resolve()
    preview = preview_page_deletion(root, page_id)
    if not preview.removable:
        raise ValueError(f"页面 {page_id} 没有可删除的代码或路由条目。")

    removed_directories: list[str] = []
    for relative in preview.directories:
        target = root / relative
        if target.is_dir():
            shutil.rmtree(target)
            removed_directories.append(relative)
    # 刻意不清理空的父目录：pages/ 与 ui-design/pages/ 是模板级固定目录，
    # 是否保留由模板契约决定，这里不越权删除。

    removed_routes: list[str] = []
    routes_path = root / ROUTES_FILE_RELATIVE
    if routes_path.is_file():
        content = routes_path.read_text(encoding="utf-8")
        pattern = _route_entry_pattern(page_id)
        remaining, count = pattern.subn("", content)
        if count:
            routes_path.write_text(remaining, encoding="utf-8")
            removed_routes = preview.route_entries

    return {
        "pageId": page_id,
        "pageLabel": preview.page_label,
        "directories": removed_directories,
        "routeEntries": removed_routes,
        "referencedBy": preview.referenced_by,
        "branch": read_workspace_branch_name(root),
    }
