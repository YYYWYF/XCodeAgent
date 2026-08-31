"""读取并校验前端项目已经具备的单元测试依赖能力。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypedDict


class FrontendTestCapabilities(TypedDict):
    """描述前端测试生成可复用的现有 npm 包和内部路径别名。"""

    manifest_found: bool
    manifest_path: str | None
    available_packages: list[str]
    internal_alias_prefixes: list[str]


_MODULE_SPECIFIER_RE: re.Pattern[str] = re.compile(
    r"""(?:\b(?:import|export)\s+(?:type\s+)?(?:[^;\n]*?\s+from\s+)?|\b(?:require|import|require\.resolve|jest\.(?:mock|doMock|requireActual)|vi\.(?:mock|doMock))\s*\(\s*)["']([^"']+)["']"""
)
_NODE_BUILTIN_PACKAGES: frozenset[str] = frozenset(
    {
        "assert",
        "buffer",
        "child_process",
        "crypto",
        "events",
        "fs",
        "http",
        "https",
        "module",
        "os",
        "path",
        "process",
        "querystring",
        "stream",
        "string_decoder",
        "timers",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "worker_threads",
        "zlib",
    }
)


def load_frontend_test_capabilities(workspace: str) -> FrontendTestCapabilities:
    """从当前项目清单读取测试可直接复用的 npm 包和 TypeScript 路径别名。"""

    root = Path(workspace).expanduser().resolve()
    frontend_root = _frontend_root(root)
    manifest_path = frontend_root / "package.json" if frontend_root else None
    manifest = _read_json_object(manifest_path) if manifest_path else None
    if frontend_root is None or manifest_path is None or manifest is None:
        return {
            "manifest_found": False,
            "manifest_path": None,
            "available_packages": [],
            "internal_alias_prefixes": ["@/", "~/"],
        }

    packages: set[str] = set()
    for key in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        values = manifest.get(key)
        if isinstance(values, dict):
            packages.update(str(name).strip() for name in values if str(name).strip())
    return {
        "manifest_found": True,
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "available_packages": sorted(packages),
        "internal_alias_prefixes": _typescript_alias_prefixes(frontend_root),
    }


def unavailable_frontend_test_imports(
    content: str,
    capabilities: FrontendTestCapabilities,
) -> list[str]:
    """返回测试正文中未由当前项目清单声明的第三方 npm 包。"""

    available = set(capabilities["available_packages"])
    aliases = tuple(capabilities["internal_alias_prefixes"])
    unavailable: list[str] = []
    for match in _MODULE_SPECIFIER_RE.finditer(content):
        specifier = match.group(1).strip()
        if _is_internal_or_builtin_specifier(specifier, aliases):
            continue
        package_name = _package_name(specifier)
        if (
            package_name
            and package_name not in available
            and package_name not in unavailable
        ):
            unavailable.append(package_name)
    return unavailable


def _frontend_root(root: Path) -> Path | None:
    """解析当前工作区实际使用的前端工程目录。"""

    for name in ("frontend", "Frontend"):
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def _read_json_object(path: Path | None) -> dict[str, Any] | None:
    """读取 JSON 对象；文件缺失或内容损坏时返回空结果。"""

    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _typescript_alias_prefixes(frontend_root: Path) -> list[str]:
    """从常见 TypeScript 配置中提取内部模块别名前缀。"""

    aliases = {"@/", "~/"}
    for name in ("tsconfig.json", "tsconfig.app.json", "tsconfig.node.json"):
        payload = _read_json_object(frontend_root / name)
        compiler_options = payload.get("compilerOptions") if payload else None
        paths = compiler_options.get("paths") if isinstance(compiler_options, dict) else None
        if not isinstance(paths, dict):
            continue
        for value in paths:
            prefix = str(value).split("*", 1)[0].strip()
            if prefix:
                aliases.add(prefix)
    return sorted(aliases)


def _is_internal_or_builtin_specifier(
    specifier: str,
    aliases: tuple[str, ...],
) -> bool:
    """判断模块引用是否属于相对路径、项目别名、虚拟模块或 Node 内置模块。"""

    return (
        not specifier
        or specifier.startswith((".", "/", "node:", "virtual:", "#"))
        or specifier in _NODE_BUILTIN_PACKAGES
        or any(specifier.startswith(alias) for alias in aliases)
    )


def _package_name(specifier: str) -> str:
    """把 npm 子路径引用归一为对应的包名。"""

    parts = specifier.split("/")
    if specifier.startswith("@"):
        return "/".join(parts[:2]) if len(parts) >= 2 else specifier
    return parts[0]
