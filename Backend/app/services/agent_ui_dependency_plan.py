"""正式目标 Agent UI 依赖与样式的只读计划，不安装、不确认设计。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tarfile
import unicodedata
from typing import Any

import yaml

from app.services.agent_ui_dependency_layout import current_dependency_layout, rsbuild_postcss_changes, validation_stages
from app.services.agent_ui_source_dependencies import prepare_source_dependencies

REPOSITORY = Path(__file__).resolve().parents[3]
RUNTIME_NAMES = tuple(f"@za38.03/agent-ui-{name}" for name in ("core", "renderer", "plugins", "runtime"))
LOCKFILES = {"pnpm": "pnpm-lock.yaml", "npm": "package-lock.json"}
STYLE_FILE = "src/agent-ui-package.css"
STYLE_TEXT = ('@import "@za38.03/agent-ui-plugins/styles.css";\n'
              '@import "@za38.03/agent-ui-runtime/ui/styles.css";\n')
POSTCSS_TEXT = 'export default { plugins: { "@tailwindcss/postcss": {} } }\n'
_IGNORED = {"node_modules", ".git", "dist", "build", ".next", ".cache", "coverage"}


def sha256(data: bytes) -> str:
    """计算真实字节摘要。"""
    return hashlib.sha256(data).hexdigest()


def digest(value: Any) -> str:
    """为服务端派生记录生成确定性摘要。"""
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def safe_path(root: Path, relative: str) -> Path:
    """拒绝跨平台绝对路径、穿越、链接及大小写冲突。"""
    logical = PurePosixPath(relative)
    if (not relative or "\\" in relative or logical.is_absolute() or PureWindowsPath(relative).drive
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("目标必须使用无穿越的相对逻辑路径。")
    current = root
    if root.is_symlink():
        raise ValueError("目标不能使用链接。")
    for part in logical.parts:
        if (re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((" ", "."))
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError("目标路径包含 Windows 保留名称或非法字符。")
        folded = unicodedata.normalize("NFC", part).casefold()
        if current.is_dir() and any(unicodedata.normalize("NFC", child.name).casefold() == folded and child.name != part
                                    for child in current.iterdir()):
            raise ValueError("目标路径有大小写冲突。")
        current = current / part
        if current.is_symlink():
            raise ValueError("目标路径不能包含链接。")
    return current


def read_object(path: Path) -> dict[str, Any]:
    """读取普通 JSON 对象，坏文件不能被当成默认配置。"""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"配置不是普通文件：{path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"配置必须是对象：{path.name}")
    return value


def target_root(workspace: str | Path) -> tuple[Path, Path]:
    """仅接受当前应用的独立 frontend，拒绝祖先 workspace。"""
    raw = Path(workspace).expanduser().absolute()
    if not raw.is_dir() or raw.resolve() != raw:
        raise ValueError("工作区必须为无链接的实际目录。")
    target = safe_path(raw, "frontend")
    if not target.is_dir():
        raise ValueError("正式 frontend 目标目录不存在。")
    for parent in (target, *target.parents):
        policy = parent / "pnpm-workspace.yaml"
        if policy.exists() or policy.is_symlink():
            if policy.is_symlink():
                raise ValueError("pnpm 配置不能使用链接。")
            if parent != target or not policy.is_file():
                raise ValueError("当前安装服务不支持 workspace 目标。")
            try:
                config = yaml.safe_load(policy.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ValueError("pnpm 配置不是有效 YAML。") from exc
            # pnpm 的当前包构建策略可单独存放于此；不能据文件名推断为多包工程。
            if not isinstance(config, dict) or config.get("packages") != ["."]:
                raise ValueError("当前安装服务只支持 packages: ['.'] 的单包 pnpm 配置。")
        package = parent / "package.json"
        if package.exists() and "workspaces" in read_object(package):
            raise ValueError("当前安装服务不支持 workspace 目标。")
    return raw, target


def target_snapshot(target: Path) -> dict[str, str]:
    """绑定目标源码、配置和 ownership，不读取依赖/构建或敏感文件内容。"""
    result: dict[str, str] = {}
    package_path = target / "package.json"
    # 静态声明的自定义产物目录同样不属于源码；所有调用方共用当前构建布局。
    output = (safe_path(target, current_dependency_layout(target, read_object(package_path))["outputDir"])
              if package_path.is_file() else None)
    def visit(directory: Path) -> None:
        """逐目录拒绝链接与跨平台大小写重复，只保存字节摘要。"""
        seen: set[str] = set()
        for path in sorted(directory.iterdir()):
            if path == output or path.name in _IGNORED or path.name.endswith(".tsbuildinfo"):
                continue
            folded = unicodedata.normalize("NFC", path.name).casefold()
            if folded in seen or path.is_symlink():
                raise ValueError("目标包含链接或大小写重复路径。")
            seen.add(folded)
            if path.is_dir():
                visit(path)
            elif path.is_file():
                result[path.relative_to(target).as_posix()] = sha256(path.read_bytes())
            else:
                raise ValueError("目标包含非普通文件。")
    visit(target)
    return result


def _manager(target: Path, package: dict[str, Any]) -> tuple[str, str]:
    """按精确 packageManager 或唯一锁选择实际管理器，不改写声明。"""
    declaration = package.get("packageManager", "")
    match = re.fullmatch(r"(npm|pnpm)@(\d+\.\d+\.\d+)", declaration)
    existing = [name for name, filename in LOCKFILES.items() if (target / filename).exists()]
    if (target / "yarn.lock").exists() or len(existing) > 1:
        raise ValueError("当前仅支持独立 npm/pnpm 且只能有一份锁文件。")
    if declaration and not match:
        raise ValueError("packageManager 必须是精确 npm/pnpm 版本。")
    manager = match[1] if match else existing[0] if existing else ""
    if not manager or (existing and existing[0] != manager):
        raise ValueError("请明确目标 packageManager，并保持与锁文件一致。")
    return manager, match[2] if match else ""


def _packages(target: Path, source: str) -> list[dict[str, str]]:
    """只选择已核对的发布组合；本地 tarball 例外明确留在计划身份中。"""
    if source not in {"registry", "local-vendor"}:
        raise ValueError("未知包来源。")
    manifest = read_object(REPOSITORY / "vendor/agent-ui/manifest.json")
    records = manifest.get("packages", [])
    selected: list[dict[str, str]] = []
    for name in RUNTIME_NAMES:
        items = [item for item in records if item.get("name") == name]
        if len(items) != 1:
            raise ValueError("发布物组合缺失或重复。")
        item = items[0]
        if not re.fullmatch(r"\d+\.\d+\.\d+", item["version"]):
            raise ValueError("发布物版本必须精确。")
        artifact = safe_path(REPOSITORY / "vendor/agent-ui", item["file"])
        data = artifact.read_bytes()
        if sha256(data) != item["sha256"] or len(data) != item["bytes"]:
            raise ValueError("发布物摘要不符。")
        with tarfile.open(artifact) as archive:
            metadata = archive.extractfile("package/package.json")
            if metadata is None:
                raise ValueError("发布物缺少公开 package.json。")
            metadata_digest = sha256(metadata.read())
        specifier = item["version"] if source == "registry" else "file:" + Path(os.path.relpath(artifact, target)).as_posix()
        selected.append({"name": name, "version": item["version"], "specifier": specifier,
                         "tarballSha256": item["sha256"],
                         "packageJsonSha256": metadata_digest,
                         "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()})
    return selected


def formal_snapshot(root: Path) -> dict[str, str | None]:
    """绑定应用配置及当前正式规划产物，执行前后都须保持一致。"""
    result: dict[str, str | None] = {}
    names = ["application.json"] + [f"{name}.{extension}" for name in (
        "specs/requirement-spec", "specs/ui-designs", "plans/product-plan", "plans/technical-plan")
        for extension in ("json", "md")]
    for name in names:
        path = safe_path(root, ".devagentstudio/" + name)
        result[name] = sha256(path.read_bytes()) if path.is_file() else None
    return result


def _local_resolution(target: Path, packages: list[dict[str, str]], manager: str) -> dict[str, str]:
    """只映射本地发布组合的依赖，不通过全局 override 改写 peer 范围。"""
    if manager == "npm":
        raise ValueError("本地 tgz 组合目前仅支持独立 pnpm；npm 需要可安装的 registry 发行。")
    mapping = {item["name"]: item["specifier"] for item in packages}
    hook = ("const path = require('node:path')\nconst selected = " + json.dumps(mapping, indent=2) + "\n\n"
            "/** 只映射已批准发布物的普通依赖，保留 peer 声明。 */\n"
            "function readPackage(pkg) {\n"
            "  for (const field of ['dependencies', 'optionalDependencies']) {\n"
            "    for (const name of Object.keys(pkg[field] || {})) {\n"
            "      if (selected[name] && !pkg[field][name].startsWith('file:')) pkg[field][name] = 'file:' + path.resolve(__dirname, selected[name].slice(5))\n"
            "    }\n  }\n  return pkg\n}\n\nmodule.exports = { hooks: { readPackage } }\n")
    existing = safe_path(target, ".pnpmfile.cjs")
    if existing.exists() and existing.read_text(encoding="utf-8") != hook:
        raise ValueError("已有 pnpm hook 需要定向审阅，不能自动覆盖。")
    return {".pnpmfile.cjs": hook}


def _supported_postcss(path: Path, package: dict[str, Any]) -> bool:
    """只读识别当前模板的静态 Tailwind 4 配置，保留 autoprefixer，不执行 JS。"""
    if path.name not in {"postcss.config.js", "postcss.config.mjs"} or (
        path.suffix == ".js" and package.get("type") != "module"
    ):
        return False
    name = r'''(?:'@tailwindcss/postcss'|"@tailwindcss/postcss"|autoprefixer|'autoprefixer'|"autoprefixer")'''
    item = rf"{name}\s*:\s*\{{\s*\}}"
    # 仅接受空选项和已知插件的对象字面量，拒绝重复键、展开、调用和附加语句。
    match = re.fullmatch(
        rf"\s*export\s+default\s*\{{\s*plugins\s*:\s*\{{\s*"
        rf"(?P<plugins>{item}(?:\s*,\s*{item})*\s*,?)\s*\}}\s*,?\s*\}}\s*;?\s*",
        path.read_text(encoding="utf-8"),
    )
    if match is None:
        return False
    names = [value.strip("'\"") for value in re.findall(name, match["plugins"])]
    return "@tailwindcss/postcss" in names and len(names) == len(set(names))


def _styles(target: Path, package: dict[str, Any], entry: str) -> dict[str, str]:
    """仅添加公共样式入口和必要 Tailwind 4 PostCSS，拒绝接管已有未知配置。"""
    dependency_fields = [package.setdefault(field, {}) for field in ("dependencies", "devDependencies")]
    for dependencies in dependency_fields:
        if not isinstance(dependencies, dict):
            raise ValueError("依赖声明必须是对象。")
        for name in ("tailwindcss", "@tailwindcss/postcss", "@tailwindcss/vite"):
            version = dependencies.get(name)
            if version and not re.match(r"^[~^]?4\.", str(version)):
                raise ValueError("现有 Tailwind 主版本需独立审阅，不能自动升级。")
    entries = [path for pattern in ("postcss.config.*", ".postcssrc*") for path in target.glob(pattern)]
    if len(entries) > 1 or (entries and not _supported_postcss(entries[0], package)) or "postcss" in package:
        raise ValueError("已有 PostCSS 配置需要定向审阅，不能自动覆盖。")
    vite = target / "vite.config.ts"
    vite_adapter = (vite.is_file() and "@tailwindcss/vite" in vite.read_text(encoding="utf-8")
                    and any("@tailwindcss/vite" in deps for deps in dependency_fields))
    if any(target.glob("vite.config.*")) and not vite_adapter:
        raise ValueError("Vite 目标需先审阅并配置 Tailwind 4 的官方 Vite 适配器，不能套用 PostCSS 导致源码路径错误。")
    changes: dict[str, str] = {}
    if not vite_adapter:
        if not entries:
            changes["postcss.config.mjs"] = POSTCSS_TEXT
        for name in ("tailwindcss", "@tailwindcss/postcss"):
            if not any(name in deps for deps in dependency_fields):
                package["devDependencies"][name] = "4.3.3"
    style = safe_path(target, STYLE_FILE)
    if style.exists() and style.read_text(encoding="utf-8") != STYLE_TEXT:
        raise ValueError("已有 Agent UI 样式入口被定制，请先审阅。")
    changes[STYLE_FILE] = STYLE_TEXT
    entry_path = safe_path(target, entry)
    content = entry_path.read_text(encoding="utf-8")
    relative = Path(os.path.relpath(style, entry_path.parent)).as_posix()
    relative = relative if relative.startswith(".") else "./" + relative
    statement = f"import '{relative}'"
    if statement not in content and f'import "{relative}"' not in content:
        changes[entry] = statement + "\n" + content
    return changes


def prepare_dependency_plan(workspace: str | Path, *, source: str = "registry", entry: str = "",
                            review_id: str = "") -> dict[str, Any]:
    """生成逐文件 diff 与保护摘要；确认时必须由服务端重新计算。"""
    root, target = target_root(workspace)
    package = read_object(target / "package.json")
    checks = validation_stages(package)
    before = target_snapshot(target)
    manager, version = _manager(target, package)
    packages = _packages(target, source)
    dependencies = package.setdefault("dependencies", {})
    for name in ("react", "react-dom"):
        if not re.fullmatch(r"[~^]?(18|19)\.\d+\.\d+", str(dependencies.get(name, ""))):
            raise ValueError("目标需已有 React 18/19，不能自动选择或升级宿主栈。")
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        if any("creator" in name or "operator" in name for name in package.get(field, {})):
            raise ValueError("Creator/Operator 工具不能进入正式目标依赖。")
    for item in packages:
        old = dependencies.get(item["name"])
        if old and old not in {item["specifier"], item["version"]}:
            raise ValueError("已有运行包声明需单独审阅，不能隐式升级。")
        if any(item["name"] in package.get(field, {}) for field in ("devDependencies", "optionalDependencies")):
            raise ValueError("运行包只能由生产 dependencies 声明。")
        dependencies[item["name"]] = item["specifier"]
    changes = _local_resolution(target, packages, manager) if source == "local-vendor" else {}
    source_dependencies = prepare_source_dependencies(package)
    layout = current_dependency_layout(target, package, entry)
    entry = layout["entry"]
    changes.update(_styles(target, package, entry))
    if layout["bundler"] == "rsbuild":
        changes.update(rsbuild_postcss_changes(target))
    changes["package.json"] = json.dumps(package, ensure_ascii=False, indent=2) + "\n"
    if review_id and not re.fullmatch(r"[a-f0-9]{32}", review_id):
        raise ValueError("安装审阅身份无效。")
    plan = {"workspace": str(root), "target": "frontend", "source": source, **layout, "reviewId": review_id,
            "manager": manager, "managerVersion": version, "lockfile": LOCKFILES[manager],
            "packages": packages, "sourceDependencies": source_dependencies, "before": before, "formal": formal_snapshot(root),
            "changes": [{"path": path, "beforeSha256": before.get(path), "after": content,
                         "afterSha256": sha256(content.encode())} for path, content in sorted(changes.items())
                        if before.get(path) != sha256(content.encode())],
            "checks": checks,
            "recovery": "失败保留文件、锁和命令记录；声明恢复不代表依赖或脚本副作用已恢复。"}
    return {**plan, "planId": digest(plan)}
