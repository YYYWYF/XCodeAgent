"""只读选择薄应用层并精确调整模块路径，拒绝把工具和包实现带入正式工程。"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import tempfile
import unicodedata
from typing import Any
from uuid import uuid4

from app.services.agent_ui_dependency_plan import safe_path, sha256
from app.services.agent_ui_dependency_layout import current_dependency_layout
from app.services.agent_ui_design_project import command_environment
from app.services.agent_ui_tool_environment import resolve_node_executable
from app.services.agent_ui_design_shared import require_shared_application
from app.services.workspace_process_registry import workspace_process_registry

HOST_FILES = {"Agent.tsx", "SharedHost.tsx", "SharedApplication.tsx", "SharedPage.tsx", "runtimeResults.ts",
              "useRuntimeResult.ts", "shared-host.css", "surfaces.ts", "props.ts", "presentation.ts", "presentation.json"}
GENERATED = {"index.ts", "plugins/index.ts", "plugins/registry.generated.ts",
             "application/Agent.tsx", "application/runtime-config.generated.ts",
             "composition-revision.generated.json", "agent-contract/frontend-tools.generated.ts",
             "agent-ui/conversation/frontend-tool-uis.generated.ts", "agent-ui/conversation/integrations.generated.tsx"}
TEXT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css", ".json", ".svg"}


def plugin_application(root: Path, relative: str) -> bool:
    """只纳入官方包包装层入口，不复制 manifest、服务实现或已接管源码。"""
    parts = PurePosixPath(relative).parts
    if len(parts) != 3 or parts[0] != "plugins" or parts[2] != "index.tsx":
        return False
    marker = safe_path(root, f"plugins/{parts[1]}/application.json")
    definition = safe_path(root, f"plugins/{parts[1]}/definition.ts")
    if not marker.is_file() or not definition.is_file():
        return False
    value = json.loads(marker.read_text())
    # 包级实现继续由公开 definition 引用，只有项目拥有的展示包装代码进入转入清单。
    return (value == {"schemaVersion": 1, "pluginId": parts[1]}
        and f'"@za38.03/agent-ui-plugins/{parts[1]}"' in definition.read_text())


def application_file(relative: str) -> bool:
    """只允许组合、薄应用层、主题/语言及 renderer；实现目录始终排除。"""
    parts = PurePosixPath(relative).parts
    if (relative in GENERATED or any(part.startswith(".") or part in {
            "plugins", "services", "framework", "runtime", "agent-contract", "node_modules", "BASE",
            "mock", "mocks", "replay", "cache", "transactions"} for part in parts)
            or parts[-1].lower() in {"credentials.json", "secrets.json", "mock-config.json", "replay.json"}
            or ".generated." in relative or PurePosixPath(relative).suffix not in TEXT_SUFFIXES):
        return False
    return relative in {"app-ui.json", "styles.css"} or parts[0] in {
        "application", "renderers", "locales", "themes", "assets"}


def source_files(source: Path, source_root: str, target_root: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """仅映射共享应用及按页展示 Host，已有独立应用须先显式重新生成。"""
    require_shared_application(source)
    mapping: dict[str, str] = {}
    excluded: list[dict[str, str]] = []
    roots = [(source_root, target_root)]
    for origin, destination in roots:
        root = safe_path(source, origin)
        if not root.exists():
            if origin == source_root:
                raise ValueError("设计 sourceRoot 不存在。")
            continue
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            safe_path(root, relative)
            if not path.is_file():
                continue
            name = path.relative_to(source).as_posix()
            if application_file(relative) or plugin_application(root, relative):
                mapping[name] = destination + "/" + relative
            else:
                excluded.append({"path": name, "reason": "由目标公开工具生成或属于受保护实现，不转入"})
    for name in sorted(HOST_FILES):
        origin = "src/design/" + name
        if safe_path(source, origin).is_file():
            mapping[origin] = target_root + "/design/" + name
    normalized = [unicodedata.normalize("NFC", name).casefold() for name in mapping.values()]
    if len(set(normalized)) != len(normalized):
        raise ValueError("转入映射存在大小写或 Unicode 冲突。")
    if source_root + "/app-ui.json" not in mapping or source_root + "/application/project-config.tsx" not in mapping:
        raise ValueError("设计缺少 AppUIModel 或公开应用层入口。")
    if len(mapping) > 300:
        raise ValueError("设计转入文件过多，请先缩小审阅范围。")
    excluded.extend({"path": path, "reason": "设计 Host、测试输入或工具记录，不转入正式目标"} for path in (
        "src/App.tsx", "src/main.tsx", "src/design/pages.tsx", "src/design/locales.json", ".agent-ui", "node_modules", "pnpm-lock.yaml"))
    return mapping, excluded


def scan_imports(source: Path, files: dict[str, str], workspace: Path) -> dict[str, Any]:
    """用已有 Node/TypeScript 解析真实模块引用；不执行被分析源码或工具初始化。"""
    node = resolve_node_executable()
    # 取消轮询会分段 communicate；私有临时文件避免大输入在首轮超时后停止写入。
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stream:
        json.dump({"projectRoot": str(source), "files": {
            name: code for name, code in files.items() if PurePosixPath(name).suffix in {".ts", ".tsx", ".js", ".jsx"}}}, stream)
        stream.seek(0)
        completed = workspace_process_registry.run(
            [str(node), str(Path(__file__).with_name("agent_ui_transfer_imports.mjs"))], workspace=workspace,
            run_id="agent-ui-transfer-inspection:" + uuid4().hex, cwd=source, env=command_environment(),
            stdin=stream, capture_output=True, text=True, timeout=30, check=False,
        )
    if completed.returncode != 0:
        raise ValueError("只读 TypeScript 模块分析失败，请核对设计消费者的现有 Node/TypeScript 环境。")
    return json.loads(completed.stdout)


def _public_module(target: Path, dependency: str, specifier: str) -> bool:
    """同时核对公开 exports 和对应资源存在，通配 export 不能证明任意路径可消费。"""
    root = (target / "node_modules" / dependency).resolve(strict=True)
    metadata = json.loads((root / "package.json").read_text(encoding="utf-8"))
    subpath = "." + specifier[len(dependency):]
    exports = metadata.get("exports", {})
    exported = exports.get(subpath)
    wildcard = ""
    if exported is None:
        for name, value in exports.items():
            if "*" in name:
                prefix, suffix = name.split("*")
                if subpath.startswith(prefix) and subpath.endswith(suffix):
                    wildcard = subpath[len(prefix):len(subpath) - len(suffix) if suffix else None]
                    exported = value
                    break
    path = exported
    if isinstance(exported, dict):
        path = exported.get("import") or exported.get("types")
    return isinstance(path, str) and safe_path(root, path.replace("*", wildcard).removeprefix("./")).is_file()


def rewrite_files(source: Path, target: Path, mapping: dict[str, str], roots: list[tuple[str, str]],
                  workspace: Path, *, rsbuild_adapter: bool = False) -> tuple[dict[str, dict[str, Any]], list[str], list[dict[str, str]]]:
    """精确重写静态引用；未转入依赖必须有公开包或目标工具的明确 owner。"""
    contents: dict[str, str] = {}
    for name in mapping:
        path = safe_path(source, name)
        if path.stat().st_size > 200_000:
            raise ValueError("单个设计文件过大，请缩小转入范围。")
        contents[name] = path.read_text(encoding="utf-8")
    if sum(len(code.encode()) for code in contents.values()) > 1_000_000:
        raise ValueError("转入源码超过审阅大小限制。")
    scanned = scan_imports(source, contents, workspace)
    package = json.loads(safe_path(target, "package.json").read_text(encoding="utf-8"))
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    vite_target = current_dependency_layout(target, package)["bundler"] == "vite"
    blockers: list[str] = []
    deferred: list[dict[str, str]] = []
    result: dict[str, dict[str, Any]] = {}
    for name, code in contents.items():
        references = scanned.get(name, {"imports": [], "errors": []})
        blockers.extend(f"{name}: {error}" for error in references["errors"])
        if rsbuild_adapter:
            blockers.extend(f"{name}: {error}" for error in references.get("adapterErrors", []))
        adjustments: list[dict[str, Any]] = []
        if PurePosixPath(name).suffix == ".css":
            # CSS 只支持静态本地 @import；复杂资源需要独立适配，不能悄悄遗漏。
            masked = re.sub(r"/\*[\s\S]*?\*/", lambda match: " " * len(match[0]), code)
            if re.search(r"url\s*\(", masked, re.I):
                blockers.append(f"{name}: CSS url 资源需要单独审阅适配")
            references["imports"] = [{"specifier": match[3], "kind": "module", "start": match.start(1), "end": match.end(1)}
                for match in re.finditer(r'''@import\s+((['"])(.*?)\2)''', masked)]
        for reference in references["imports"]:
            specifier = reference["specifier"]
            if not specifier.startswith("."):
                if "\\" in specifier or any(part in {".", "..", ""} for part in specifier.split("/")):
                    blockers.append(f"{name}: 非法包模块路径")
                    continue
                parts = specifier.split("/")
                dependency = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
                if dependency not in dependencies or specifier.startswith(("node:", "http:", "https:", "/")):
                    blockers.append(f"{name}: 目标未声明可消费模块 {specifier}")
                elif dependency.startswith("@za38.03/agent-ui-"):
                    if not _public_module(target, dependency, specifier):
                        blockers.append(f"{name}: 包未公开模块 {specifier}")
                continue
            base, separator, query = specifier.partition("?")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), base))
            if resolved.startswith("../") or "\\" in resolved:
                blockers.append(f"{name}: 模块路径越界")
                continue
            candidates = [resolved, *(resolved + suffix for suffix in (".ts", ".tsx", ".js", ".jsx", ".json", ".css")),
                          resolved + "/index.ts", resolved + "/index.tsx"]
            matched = next((item for item in candidates if item in mapping), None)
            destination = mapping.get(matched or "")
            if matched:
                extension = matched[len(resolved):]
                destination = destination[:-len(extension)] if extension else destination
            else:
                for origin, output in roots:
                    if resolved.startswith(origin + "/"):
                        relative = resolved[len(origin) + 1:]
                        candidate = next((item[len(origin) + 1:] for item in candidates
                            if item.startswith(origin + "/") and item[len(origin) + 1:] in GENERATED), None)
                        foundation = False
                        source_lock = safe_path(source, ".agent-ui/source-lock.json")
                        if source_lock.is_file():
                            baseline = {filename: data.get("sha256")
                                for item in json.loads(source_lock.read_text()).get("items", {}).values()
                                for filename, data in item.get("files", {}).items()}
                            for item in candidates:
                                if not item.startswith(origin + "/"):
                                    continue
                                filename = item[len(origin) + 1:]
                                source_file, target_file = safe_path(source, item), safe_path(target, output + "/" + filename)
                                source_hash = sha256(source_file.read_bytes()) if source_file.is_file() else None
                                if (source_hash and (filename in baseline or filename.startswith("services/"))
                                        and (filename not in baseline or source_hash == baseline[filename])
                                        and ((target_file.is_file() and sha256(target_file.read_bytes()) == source_hash)
                                            or not safe_path(target, ".agent-ui/project.json").is_file())):
                                    foundation = True
                                    break
                        if candidate or relative == "plugins" or foundation:
                            destination = output + "/" + relative
                            deferred.append({"sourcePath": name, "reference": specifier,
                                "targetPath": destination, "owner": "target-public-tool"})
                        break
            if destination is None or (reference["kind"] == "glob" and "*" in base):
                blockers.append(f"{name}: 引用不在转入清单或公开生成范围：{specifier}")
                continue
            new_reference = posixpath.relpath(destination, posixpath.dirname(mapping[name]))
            if not new_reference.startswith("."):
                new_reference = "./" + new_reference
            if separator:
                if query != "raw":
                    blockers.append(f"{name}: 不支持模块查询 {query}")
                if not vite_target and not rsbuild_adapter:
                    blockers.append(f"{name}: ?raw/import.meta.glob 需要目标构建器的显式适配")
                new_reference += "?" + query
            if reference["kind"] == "glob" and not vite_target and not rsbuild_adapter:
                blockers.append(f"{name}: import.meta.glob 需要目标构建器的显式适配")
            if new_reference != specifier:
                adjustments.append({**reference, "after": new_reference})
        after = code
        for item in sorted(adjustments, key=lambda item: item["start"], reverse=True):
            after = after[:item["start"]] + json.dumps(item["after"], ensure_ascii=False) + after[item["end"]:]
        result[name] = {"after": after, "sourceSha256": sha256(code.encode()), "importAdjustments": adjustments}
    return result, sorted(set(blockers)), deferred
