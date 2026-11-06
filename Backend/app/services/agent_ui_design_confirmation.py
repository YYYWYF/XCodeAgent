"""多文件 UiDesign 的正式确认与下游只读准入。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.services.agent_ui_design_project import REPOSITORY, SEED_FILES, _check_installed_packages
from app.services.agent_ui_design_presentation import read_presentations
from app.services.agent_ui_design_shared import require_shared_application


def _page_content_digest(project: Path, page_id: str, page_ids: list[str]) -> str:
    """绑定共享源码、本页业务正文及展示参数，其他页 props 不改变本页确认身份。"""
    require_shared_application(project)
    if page_id not in page_ids:
        raise ValueError('当前页面不属于共享 Agent UI 工程。')
    business_path = f'src/pages/page-{page_ids.index(page_id)}.tsx'
    digest = hashlib.sha256()
    for path in sorted((project / 'src').rglob('*')):
        if path.is_symlink():
            raise ValueError('Agent UI 确认源码不能包含链接。')
        if not path.is_file():
            continue
        relative = path.relative_to(project).as_posix()
        if relative in {'src/design/presentation.json', 'src/design/presentation.ts'}:
            continue
        if relative.startswith('src/pages/') and relative != business_path:
            continue
        digest.update(relative.encode())
        digest.update(b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    presentation = read_presentations(project).get(page_id, {})
    digest.update(json.dumps(presentation, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    for name in (*SEED_FILES, 'vite.config.ts', 'index.html'):
        digest.update(name.encode())
        digest.update(hashlib.sha256((project / name).read_bytes()).digest())
    return digest.hexdigest()


def _installed_package_digest(root: Path) -> str:
    """绑定实际包资源，排除包管理器生成的嵌套依赖，不跟随包内资源链接。"""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] == "node_modules":
            continue
        if path.is_symlink():
            raise ValueError("Agent UI 安装包资源不能链接到其他目录。")
        if path.is_file():
            digest.update(relative.as_posix().encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def agent_design_evidence(state: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    """从当前真实资产和安装身份派生确认凭据，不启动工具、模型或预览。"""
    from app.services.agent_ui_design_projection import current_project

    project, asset = current_project(state, page)
    keys = ("revision", "factsDigest", "environmentIdentity", "sourceDigest", "buildDigest")
    if any(not isinstance(asset.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", asset[key]) for key in keys):
        raise ValueError("Agent UI 资产摘要无效，请重新生成当前设计。")
    revision = hashlib.sha256((asset["factsDigest"] + asset["environmentIdentity"] + asset["sourceDigest"]).encode()).hexdigest()
    if revision != asset["revision"]:
        raise ValueError("Agent UI 修订与当前事实、环境或源码不匹配。")
    manifest = json.loads((REPOSITORY / "vendor/agent-ui/manifest.json").read_text(encoding="utf-8"))
    _check_installed_packages(project.project_dir, manifest)
    packages: list[dict[str, str]] = []
    for item in manifest["packages"]:
        if item["name"].endswith(("agent-ui-creator", "agent-ui-web-component")):
            continue
        metadata = project.project_dir / "node_modules" / item["name"] / "package.json"
        packages.append({"name": item["name"], "version": item["version"],
                         "packageJsonSha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
                         "installedDigest": _installed_package_digest(metadata.resolve().parent),
                         "tarballSha256": item["sha256"]})
    return {"pageId": page['pageId'], "factsDigest": asset['factsDigest'],
            "environmentIdentity": asset['environmentIdentity'],
            "pageContentDigest": _page_content_digest(project.project_dir, page['pageId'], asset['pageIds']),
            "packages": packages,
            "lockSha256": hashlib.sha256((project.project_dir / "pnpm-lock.yaml").read_bytes()).hexdigest()}


def verify_agent_design_page(state: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    """确认时验证 ready/confirmed 页面，生成新的凭据但不替用户提交确认。"""
    from app.services.agent_ui_design_projection import current_project
    from app.services.ui_design_agent_surfaces import project_agent_design_pages
    from app.services.ui_design_interactions import agent_project_page_bindings

    if page.get("status") not in {"ready", "confirmed"}:
        raise ValueError("当前页面尚未生成成功，请完成生成后再确认。")
    verification = page.get("verification")
    checks = verification.get("checks", []) if isinstance(verification, dict) else []
    if not isinstance(checks, list):
        raise ValueError("当前设计的验证记录无效。")
    passed = {item.get("id") for item in checks if isinstance(item, dict) and item.get("status") == "passed"}
    if not {"lint", "typecheck", "build"}.issubset(passed):
        raise ValueError("当前设计缺少完整的 lint、typecheck 和 build 验证记录。")
    project, asset = current_project(state, page)
    product_page = next(item for item in project_agent_design_pages(
        state["requirement_spec"], state["product_plan"]) if item["pageId"] == page["pageId"])
    bindings = agent_project_page_bindings(project.project_dir, product_page, asset["pageIds"])
    evidence = agent_design_evidence(state, page)
    return {**{key: value for key, value in page.items() if key not in {"code", "project_preview", "preview_error"}},
            "bindings": bindings,
            "asset_confirmation": evidence,
            "verification": {"status": "passed", "checks": checks, "errors": []}}


def require_confirmed_agent_designs(state: dict[str, Any], manifest: dict[str, Any]) -> None:
    """技术规划、交接与恢复只消费已经明确确认且仍匹配真实资产的版本。"""
    pages = [page for page in manifest.get("pages", []) if isinstance(page, dict) and page.get("design_asset")]
    if manifest.get("confirmation_status") == "skipped":
        return
    from app.services.ui_design_agent_surfaces import project_ui_design_pages
    expected = {page["pageId"] for page in project_ui_design_pages(state.get("product_plan") or {})
                if page.get("agent_surfaces")}
    if len(pages) != len(expected) or expected != {page.get("pageId") for page in pages}:
        raise ValueError("当前 Agent UI 设计资产缺失或页面集合已变化。")
    if not pages:
        return
    if any(page["design_asset"] != pages[0]["design_asset"] for page in pages):
        raise ValueError("当前 Agent UI 页面不属于同一已确认工程。")
    if manifest.get("confirmation_status") != "confirmed":
        raise ValueError("当前 Agent UI 设计尚未正式确认。")
    from app.workspace.ui_design_documents import prepare_ui_markdown_revision
    _candidate, request = prepare_ui_markdown_revision(state, manifest)
    if request:
        raise ValueError("UI 设计说明存在尚未同步的修改，请返回设计审阅并重新确认。")
    for page in pages:
        if page.get("status") != "confirmed" or page.get("asset_confirmation") != agent_design_evidence(state, page):
            raise ValueError(f"页面 {page.get('pageId')} 的 Agent UI 确认证据已过期，请重新审阅并确认。")
