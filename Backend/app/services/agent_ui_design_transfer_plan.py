"""T22 正式设计转入只读计划；不初始化、不写目标、不批准设计或执行转入。"""

from __future__ import annotations

import difflib
from pathlib import Path
import re
from typing import Any

from app.services.agent_ui_transfer_records import transfer_installation, transfer_ownership
from app.services.agent_ui_dependency_plan import digest, formal_snapshot, read_object, safe_path, sha256, target_root, target_snapshot
from app.services.agent_ui_design_confirmation import _installed_package_digest, require_confirmed_agent_designs
from app.services.agent_ui_design_projection import current_project
from app.services.agent_ui_transfer_files import GENERATED, rewrite_files, source_files
from app.services.agent_ui_transfer_tool import tool_identity
from app.services.agent_ui_host_integration import plan_host_integration
from app.services.agent_ui_dependency_layout import current_dependency_layout
from app.services.agent_ui_rsbuild_adapter import prepare_rsbuild_adapter


def _formal_state(workspace: Path) -> dict[str, Any]:
    """读取当前正式文档，复用原新鲜度及 Markdown 编辑检查，不写回任何文件。"""
    from app.services.artifact_freshness import upstream_errors
    from app.services.product_plan import require_current_product_plan
    from app.services.project_plan import TECHNICAL_PLAN_ARTIFACT_TYPE
    from app.services.stage_artifacts import _ARTIFACTS
    from app.workspace.plan_documents import edited_technical_plan_markdown, load_project_plan_json
    from app.workspace.product_plan_documents import edited_product_plan_markdown
    from app.workspace.spec_documents import edited_requirement_spec_markdown

    state: dict[str, Any] = {"workspace": str(workspace)}
    for key, relative in _ARTIFACTS.items():
        path = safe_path(workspace, ".devagentstudio/" + relative)
        read_object(path)
        markdown = safe_path(workspace, ".devagentstudio/" + str(Path(relative).with_suffix(".md")))
        if not markdown.is_file():
            raise ValueError(f"正式 Markdown 缺失：{markdown.name}")
        value = load_project_plan_json(path, hydrate_detail_designs=True)
        if value.get("confirmation_status") != "confirmed":
            raise ValueError(f"{key} 尚未正式确认。")
        errors = upstream_errors(workspace / ".devagentstudio", key, value)
        if errors:
            raise ValueError(f"{key} 已过期：{'；'.join(errors)}")
        state[key] = value
    require_current_product_plan(state["product_plan"], state["requirement_spec"])
    if state["technical_plan"].get("artifact_type") != TECHNICAL_PLAN_ARTIFACT_TYPE:
        raise ValueError("正式 TechnicalPlan 类型无效。")
    if not all(state["technical_plan"].get(key) for key in ("product_plan_sha256", "ui_designs_sha256")):
        raise ValueError("TechnicalPlan 缺少当前产品与设计来源。")
    for reader, key in ((edited_requirement_spec_markdown, "requirement_spec"),
                        (edited_product_plan_markdown, "product_plan"),
                        (edited_technical_plan_markdown, "technical_plan")):
        if reader(state, state[key]) is not None:
            raise ValueError(f"{key} 的 Markdown 有未确认修改，请返回原正式修订流程。")
    require_confirmed_agent_designs(state, state["ui_designs"])
    return state


def _public_identity(target: Path, confirmation: dict[str, Any]) -> dict[str, Any]:
    """绑定实际包资源及公开 catalog/type exports；不读取包私有 API。"""
    packages: list[dict[str, str]] = []
    public_types: dict[str, str] = {}
    catalog_digest = ""
    for item in confirmation["packages"]:
        root = (target / "node_modules" / item["name"]).resolve(strict=True)
        if not root.is_relative_to(target / "node_modules"):
            raise ValueError("实际包解析越界。")
        actual = _installed_package_digest(root)
        if actual != item["installedDigest"]:
            raise ValueError("设计与目标的实际运行包资源不同，需先复验设计。")
        metadata = read_object(root / "package.json")
        packages.append({"name": item["name"], "version": item["version"], "installedDigest": actual})
        exports = metadata.get("exports", {})
        for name, export in exports.items():
            if isinstance(export, dict) and isinstance(export.get("types"), str) and "*" not in name:
                path = safe_path(root, export["types"].removeprefix("./"))
                public_types[item["name"] + "/" + name] = sha256(path.read_bytes())
        if item["name"] == "@za38.03/agent-ui-plugins":
            exported = exports.get("./catalog.json")
            if not isinstance(exported, str):
                raise ValueError("实际 Plugins 包缺少公开 catalog.json。")
            catalog_digest = sha256(safe_path(root, exported.removeprefix("./")).read_bytes())
    if not catalog_digest or not public_types:
        raise ValueError("实际包缺少可消费的 catalog/shared API。")
    return {"packages": packages, "catalogSha256": catalog_digest, "sharedApiSha256": digest(public_types)}


def _controls(project: Path) -> dict[str, str]:
    """只绑定工具记录和 BASE 的字节身份，不把其内容或工具凭据加入转入清单。"""
    return {name: sha256(safe_path(project, name).read_bytes()) for name in (
        ".agent-ui/project.json", ".agent-ui/source-lock.json")}


def prepare_transfer_plan(workspace: str | Path, *, installation_plan_id: str,
                          target_source_root: str | None = None) -> dict[str, Any]:
    """根据当前设计与目标生成逐文件 diff；所有失败和冲突均保持磁盘原样。"""
    root, target = target_root(workspace)
    if not re.fullmatch(r"[a-f0-9]{64}", installation_plan_id):
        raise ValueError("必须提供当前 T21 安装计划身份。")
    target_record = safe_path(target, ".agent-ui/project.json")
    if target_source_root is None:
        target_source_root = read_object(target_record).get("sourceRoot") if target_record.is_file() else "src/agent-ui"
    if not isinstance(target_source_root, str):
        raise ValueError("目标 sourceRoot 无效。")
    destination = safe_path(target, target_source_root)
    if (not target_source_root.startswith("src/") or any(part.startswith(".") or part.casefold() in {
            "design", "base", "node_modules", "services", "framework", "runtime", "mock", "mocks", "replay"}
            for part in Path(target_source_root).parts[1:])):
        raise ValueError("目标 sourceRoot 必须为独立 src 子目录。")
    before = target_snapshot(target)
    formal = formal_snapshot(root)
    state = _formal_state(root)
    installation = transfer_installation(root, installation_plan_id)
    if installation.get("state") != "verified":
        raise ValueError("目标安装证据缺失或过期，请先通过 T21 重新验证。")
    pages = [page for page in state["ui_designs"]["pages"] if page.get("design_asset")]
    if not pages:
        raise ValueError("当前正式 UiDesign 没有 Agent UI 多文件资产。")
    project, asset = current_project(state, pages[0])
    source = project.project_dir
    source_controls = _controls(source)
    source_root = read_object(safe_path(source, ".agent-ui/project.json")).get("sourceRoot")
    if not isinstance(source_root, str) or not source_root.startswith("src/"):
        raise ValueError("设计 sourceRoot 无效。")
    safe_path(source, source_root)
    if read_object(safe_path(source, ".agent-ui/source-lock.json")).get("sourceRoot") != source_root:
        raise ValueError("设计工具记录与 sourceRoot 不匹配。")
    initialized = target_record.is_file()
    if initialized and read_object(target_record).get("sourceRoot") != target_source_root:
        raise ValueError("已有目标的 sourceRoot 不匹配，不能重新初始化或重置 ownership。")
    if initialized:
        if read_object(safe_path(target, ".agent-ui/source-lock.json")).get("sourceRoot") != target_source_root:
            raise ValueError("已有目标 BASE 与 sourceRoot 不匹配。")
    public_api = _public_identity(target, pages[0]["asset_confirmation"])
    tool = tool_identity()
    mapping, excluded = source_files(source, source_root, target_source_root)
    roots = [(source_root, target_source_root)]
    rewritten, blockers, generated = rewrite_files(source, target, mapping, roots, root)
    owned = transfer_ownership(root, target_source_root)
    adapter_files: dict[str, str] = {}
    if (current_dependency_layout(target, read_object(target / "package.json"))["bundler"] == "rsbuild"
            and any("需要目标构建器的显式适配" in reason for reason in blockers)):
        try:
            adapter_files = prepare_rsbuild_adapter(target, target_source_root, owned=owned)
        except ValueError as exc:
            blockers.append(str(exc))
        else:
            rewritten, blockers, generated = rewrite_files(source, target, mapping, roots, root, rsbuild_adapter=True)
    # 被排除的服务/实现不能静默丢失设计侧定制；只能复用目标自己的同版默认资源。
    baseline = {name: value.get("sha256") for item in read_object(safe_path(source, ".agent-ui/source-lock.json")).get("items", {}).values()
        for name, value in item.get("files", {}).items()}
    for entry in safe_path(source, source_root + "/plugins").glob("*/index.tsx"):
        relative = entry.relative_to(safe_path(source, source_root)).as_posix()
        if entry.relative_to(source).as_posix() not in mapping and relative not in baseline:
            blockers.append(f"{source_root}/{relative}: 缺少当前公开包应用层或 BASE ownership，不能静默丢弃展示定制")
    for relative, expected_hash in baseline.items():
        path = source_root + "/" + relative
        source_file = safe_path(source, path)
        if path not in mapping and relative not in GENERATED and source_file.is_file() and sha256(source_file.read_bytes()) != expected_hash:
            blockers.append(f"{path}: 受保护实现已定制，不能作为应用层转入或静默丢弃")
    if initialized:
        for name in mapping:
            relative = name.removeprefix(source_root + "/")
            parts = Path(relative).parts
            if len(parts) == 3 and parts[0] == "plugins":
                for filename in ("application.json", "definition.ts", "manifest.json"):
                    origin = safe_path(source, source_root + f"/plugins/{parts[1]}/{filename}")
                    destination_file = safe_path(target, target_source_root + f"/plugins/{parts[1]}/{filename}")
                    if not destination_file.is_file() or origin.read_bytes() != destination_file.read_bytes():
                        blockers.append(f"{relative}: 包应用层记录与目标不同，保留目标 ownership，需先显式处理")
    if not initialized and destination.exists():
        blockers.append("目标 sourceRoot 已存在但未由公开工具初始化，保留现有文件，禁止默认接管。")
    host = plan_host_integration({**state, 'application_config': read_object(root / '.devagentstudio/application.json')},
        target=target, source=source, source_root=target_source_root,
        page_ids=[page['pageId'] for page in pages], workspace=root)
    if host['status'] == 'blocked':
        blockers.append(host['reason'])
    changes: list[dict[str, Any]] = []
    for name, output in mapping.items():
        file = safe_path(target, output)
        if file.exists() and not file.is_file():
            raise ValueError("拟转入目标不是普通文件。")
        if file.is_file() and file.stat().st_size > 200_000:
            raise ValueError("目标冲突文件过大，请先缩小差异审阅范围。")
        entry = rewritten[name]
        old = file.read_text(encoding="utf-8") if file.exists() else ""
        after = entry["after"]
        operation = "unchanged" if file.exists() and old == after else (
            "modify" if file.exists() and owned.get(output) == before.get(output) else "conflict" if file.exists() else "add")
        changes.append({**entry, "sourcePath": name, "targetPath": output, "operation": operation,
            "beforeSha256": sha256(file.read_bytes()) if file.exists() else None,
            "afterSha256": sha256(after.encode()), "diff": "".join(difflib.unified_diff(
                old.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=output, tofile=output))})
    for output, after in {**host['files'], **adapter_files}.items():
        file = safe_path(target, output)
        if file.exists() and (not file.is_file() or file.stat().st_size > 200_000):
            raise ValueError('Host 扩展目标不是可审阅的普通文件。')
        old = file.read_text(encoding='utf-8') if file.is_file() else ''
        # 注册表只做已审阅 AST 插入；现有 Provider 实现依然服从逐文件 ownership。
        operation = 'unchanged' if file.is_file() and old == after else (
            'modify' if output in {'src/extensions/providers.ts', *adapter_files} or owned.get(output) == before.get(output)
            else 'conflict' if file.exists() else 'add')
        owner = 'platform:agent-ui-rsbuild/' if output in adapter_files else 'platform:agent-ui-host/'
        changes.append({'sourcePath': owner + output, 'targetPath': output,
            'sourceSha256': sha256(after.encode()), 'after': after, 'importAdjustments': [],
            'operation': operation, 'beforeSha256': before.get(output), 'afterSha256': sha256(after.encode()),
            'diff': ''.join(difflib.unified_diff(old.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=output, tofile=output))})
    conflicts = [item["targetPath"] for item in changes if item["operation"] == "conflict"]
    # 读前/读后检查绑定完整输入；并发写入不能制造混合版本的计划。
    require_confirmed_agent_designs(state, state["ui_designs"])
    if (before != target_snapshot(target) or formal != formal_snapshot(root) or source_controls != _controls(source)
            or any(sha256(safe_path(source, name).read_bytes()) != entry["sourceSha256"] for name, entry in rewritten.items())
            or _public_identity(target, pages[0]["asset_confirmation"]) != public_api
            or tool_identity() != tool
            or transfer_installation(root, installation_plan_id).get("state") != "verified"):
        raise ValueError("设计、正式规划、目标或安装在读取期间变化，请重新制定计划。")
    plan: dict[str, Any] = {"readOnly": True, "executionAvailable": True, "workspace": str(root),
        "status": "blocked" if blockers or conflicts else "reviewable", "blockers": sorted(set(blockers)), "conflicts": conflicts,
        "source": {"projectRoot": source.relative_to(root).as_posix(), "draft": asset["draft"], "revision": asset["revision"], "sourceRoot": source_root,
            "sourceDigest": asset["sourceDigest"], "buildDigest": asset["buildDigest"], "controls": source_controls},
        "target": {"projectRoot": "frontend", "sourceRoot": target_source_root, "snapshotSha256": digest(before)}, "formal": formal,
        "installation": {"planId": installation_plan_id, "evidence": installation["installation"], "styles": installation["styles"]},
        "publicApi": public_api, "tool": tool, "changes": changes, "excluded": excluded,
        "hostIntegration": {key: value for key, value in host.items() if key != 'files'},
        "protected": before, "generatedReferences": generated,
        "initialization": {"required": not initialized, "requiresReplan": not initialized,
            "owner": "target-public-tool", "input": {"sourceRoot": target_source_root, "mode": "platform"}},
        "validation": ["重新核对计划/CAS", "公开工具初始化及组合/注册校验", "目标 lint/typecheck/build",
            "确认 AppUIModel/props/样式与设计一致", "Host 单 Runtime 与相关视觉/交互验收"],
        "recovery": "本次只读，无文件变更；后续执行需审阅新计划，按当前 hash 恢复自己仍拥有的文件，不承诺依赖副作用回滚。"}
    return {"planId": digest(plan), **plan}
