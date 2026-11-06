"""复核目标实际安装、锁文件与样式构建，返回有明确边界的证据。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.services.agent_ui_dependency_plan import read_object, safe_path, sha256


def installed_evidence(target: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """同时核对公开包 metadata、真实解析位置及锁中 resolved integrity。"""
    modules = safe_path(target, "node_modules")
    if not modules.is_dir():
        raise ValueError("未发现目标实际 node_modules。")
    lock_path = safe_path(target, plan["lockfile"])
    if plan["manager"] == "pnpm":
        lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
        if not isinstance(lock, dict) or str(lock.get("lockfileVersion")) != "9.0":
            raise ValueError("当前只支持 pnpm v9 锁合同。")
        root_dependencies = lock.get("importers", {}).get(".", {}).get("dependencies", {})
    else:
        lock = read_object(lock_path)
        if lock.get("lockfileVersion") != 3:
            raise ValueError("当前只支持 npm v3 锁合同。")
        root_dependencies = lock.get("packages", {}).get("", {}).get("dependencies", {})
    evidence: list[dict[str, str]] = []
    for item in plan["packages"]:
        metadata_path = (modules / item["name"] / "package.json").resolve(strict=True)
        if not metadata_path.is_relative_to(modules):
            raise ValueError("安装包解析到了目标 node_modules 之外。")
        metadata = read_object(metadata_path)
        metadata_sha = sha256(metadata_path.read_bytes())
        if (metadata.get("name") != item["name"] or metadata.get("version") != item["version"]
                or metadata_sha != item["packageJsonSha256"]):
            raise ValueError("实际安装包身份与已审阅发布物不匹配。")
        if plan["manager"] == "pnpm":
            reference = root_dependencies.get(item["name"], {})
            if reference.get("specifier") != item["specifier"]:
                raise ValueError("pnpm 根依赖与批准声明不一致。")
            resolved = str(reference.get("version", "")).split("(")[0]
            key = f"{item['name']}@{resolved}"
            record = lock.get("packages", {}).get(key, {})
            resolution = record.get("resolution", {})
            integrity = resolution.get("integrity", "")
            if plan["source"] == "registry" and resolved != item["version"]:
                raise ValueError("pnpm 锁定版本不匹配。")
            resolved_identity = resolution.get("tarball", resolved)
        else:
            if root_dependencies.get(item["name"]) != item["specifier"]:
                raise ValueError("npm 根依赖与批准声明不一致。")
            record = lock.get("packages", {}).get("node_modules/" + item["name"], {})
            if record.get("version") != item["version"] or record.get("link"):
                raise ValueError("npm 锁定包身份不匹配。")
            integrity = record.get("integrity", "")
            resolved_identity = record.get("resolved", "")
        if integrity != item["integrity"]:
            raise ValueError("锁文件 integrity 不匹配已审阅发布物。")
        if plan["source"] == "local-vendor" and resolved_identity != item["specifier"]:
            raise ValueError("锁文件 tarball 来源不匹配。")
        if plan["source"] == "registry" and str(resolved_identity).startswith(("file:", "link:", "workspace:")):
            raise ValueError("registry 计划不能通过本地链接满足。")
        evidence.append({"name": item["name"], "version": item["version"], "integrity": integrity,
                         "resolved": str(resolved_identity), "packageJsonSha256": metadata_sha})
    if (modules / "@za38.03/agent-ui-creator").exists():
        raise ValueError("Creator 工具不能进入正式目标安装。")
    from app.services.agent_ui_source_dependencies import source_dependency_evidence
    return {"packages": evidence, "sourcePackages": source_dependency_evidence(target, plan, lock),
            "lockSha256": sha256(lock_path.read_bytes())}


def stylesheet_evidence(target: Path, *, output_dir: str = "", built_after_ns: int = 0) -> list[dict[str, str]]:
    """只接受本轮构建产出的 CSS，拒绝未经处理的 Tailwind 指令。"""
    from app.services.agent_ui_dependency_layout import current_dependency_layout

    # 转入后的复验同样读取当前构建合同，不再将未传目录解释为固定 dist。
    output_dir = output_dir or current_dependency_layout(target, read_object(target / "package.json"))["outputDir"]
    output = safe_path(target, output_dir)
    files: list[dict[str, str]] = []
    has_theme = False
    for path in sorted(output.rglob("*.css")):
        safe_path(output, path.relative_to(output).as_posix())
        if path.stat().st_mtime_ns < built_after_ns:
            raise ValueError("不能使用本轮构建之前的旧 CSS 作为成功证据。")
        content = path.read_text(encoding="utf-8")
        if (any(marker in content for marker in ("@source ", "@tailwind ", "@theme ", "@utility ",
                                                "@custom-variant ", "@apply "))
                or '@import "tailwindcss' in content):
            raise ValueError("Tailwind 样式未完成构建处理。")
        has_theme = has_theme or "--aui-" in content or "data-aui-theme" in content
        files.append({"path": path.relative_to(target).as_posix(), "sha256": sha256(path.read_bytes())})
    if not files or not has_theme:
        raise ValueError(f"{output_dir} 中缺少 Agent UI 样式构建证据。")
    return files
