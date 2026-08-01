from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


INSPECTOR_SCHEMA_VERSION = "1.0.0"
MAX_FILE_BYTES = 256_000
MAX_FILES = 4_000

IGNORED_DIRS = {
    ".git",
    ".xcodeagent",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "out",
}

SOURCE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".less",
    ".css",
    ".html",
}

# Matches frontend source roots for both the XcodeAgent's own Electron frontend
# (Frontend/src/) and user applications scaffolded under frontend/src/.
# Exposing user-app frontend files in the WorkspaceSnapshot lets the build-task
# planner and Frontend Agent see the real place frontend code must live, instead
# of only the XcodeAgent development workspace.
FRONTEND_SRC_RE = re.compile(r"^(?:Frontend/src/|frontend/src/)")


class CodeGraphProvider(Protocol):
    """Optional semantic graph extension point for future workspace inspection."""

    def available(self) -> bool:
        """Return whether a code graph index can serve this workspace."""

    def inspect(self, workspace_root: Path, files: list[str]) -> dict[str, Any]:
        """Return additional graph facts keyed by stable schema fields."""


class NullCodeGraphProvider:
    """Default provider used until a full code graph index is integrated."""

    def available(self) -> bool:
        return False

    def inspect(self, workspace_root: Path, files: list[str]) -> dict[str, Any]:
        return {
            "provider": "none",
            "available": False,
            "facts": {},
        }


def inspect_workspace(
    workspace_root: Path,
    *,
    cache_root: Path,
    code_graph_provider: CodeGraphProvider | None = None,
) -> tuple[dict[str, Any], str, bool]:
    """Build or load a deterministic, cacheable workspace snapshot."""

    workspace_root = workspace_root.resolve()
    files = _list_workspace_files(workspace_root)
    revision = _workspace_revision(workspace_root, files)
    cache_path = (
        cache_root
        / "workspace-snapshots"
        / f"{revision}.{INSPECTOR_SCHEMA_VERSION}.json"
    )
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8")), str(cache_path), True

    snapshot = _build_snapshot(
        workspace_root,
        revision=revision,
        files=files,
        code_graph_provider=code_graph_provider or NullCodeGraphProvider(),
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return snapshot, str(cache_path), False


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _list_workspace_files(workspace_root: Path) -> list[str]:
    rg_result = _run(["rg", "--files", *_rg_ignore_globs()], cwd=workspace_root)
    if rg_result and rg_result.returncode == 0:
        return sorted(
            path
            for path in rg_result.stdout.splitlines()
            if path and not _is_ignored_path(path)
        )[:MAX_FILES]

    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(workspace_root):
        if len(files) >= MAX_FILES:
            break
        current = Path(dirpath)
        # 原地剪枝：被忽略的目录不进入，避免先枚举 target/build/node_modules
        # 等大型构建产物目录里的全部文件再做过滤。
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not _is_ignored_path(
                Path((current / name).relative_to(workspace_root)).as_posix()
            )
        ]
        for filename in sorted(filenames):
            if len(files) >= MAX_FILES:
                break
            relative = Path((current / filename).relative_to(workspace_root)).as_posix()
            if _is_ignored_path(relative):
                continue
            files.append(relative)
    return sorted(files)


def _workspace_revision(workspace_root: Path, files: list[str]) -> str:
    parts: list[str] = [f"schema:{INSPECTOR_SCHEMA_VERSION}"]
    git_head = _run(["git", "rev-parse", "HEAD"], cwd=workspace_root)
    if git_head and git_head.returncode == 0:
        parts.append(f"head:{git_head.stdout.strip()}")
        for args, label in (
            (["git", "diff", "--cached", "--binary"], "staged"),
            (["git", "diff", "--binary"], "unstaged"),
        ):
            result = _run(args, cwd=workspace_root, timeout=20)
            if result is not None:
                parts.append(f"{label}:{_hash_text(result.stdout)}")
        untracked = _run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=workspace_root,
            timeout=20,
        )
        if untracked is not None:
            parts.append(f"untracked:{_untracked_files_hash(workspace_root, untracked.stdout)}")
    else:
        manifest = "\n".join(files)
        parts.append(f"manifest:{_hash_text(manifest)}")

    for name in (
        "package.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
    ):
        path = workspace_root / name
        if path.is_file():
            parts.append(f"{name}:{_hash_file(path)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]


def _build_snapshot(
    workspace_root: Path,
    *,
    revision: str,
    files: list[str],
    code_graph_provider: CodeGraphProvider,
) -> dict[str, Any]:
    package_json = _read_json(workspace_root / "package.json")
    frontend_package_path = next(
        (
            candidate
            for candidate in (
                workspace_root / "Frontend" / "package.json",
                workspace_root / "frontend" / "package.json",
            )
            if candidate.is_file()
        ),
        workspace_root / "Frontend" / "package.json",
    )
    frontend_package_json = _read_json(frontend_package_path)
    frontend_cwd = frontend_package_path.parent.name
    pyproject = _read_text(workspace_root / "pyproject.toml")
    requirements = _read_text(workspace_root / "requirements.txt")

    backend_files = [
        path
        for path in files
        if path.startswith(("Backend/", "backend/"))
    ]
    frontend_files = [path for path in files if FRONTEND_SRC_RE.match(path)]
    source_files = [
        path
        for path in files
        if Path(path).suffix in SOURCE_SUFFIXES and _can_read(workspace_root / path)
    ]
    graph_facts = (
        code_graph_provider.inspect(workspace_root, source_files)
        if code_graph_provider.available()
        else NullCodeGraphProvider().inspect(workspace_root, source_files)
    )

    return {
        "schema_version": INSPECTOR_SCHEMA_VERSION,
        "workspace_revision": revision,
        "generated_at": datetime.now(UTC).isoformat(),
        "project_roots": _project_roots(files),
        "tech_stack": _tech_stack(
            package_json,
            frontend_package_json,
            pyproject=pyproject,
            requirements=requirements,
            files=files,
        ),
        "entrypoints": _entrypoints(files),
        "build_commands": _build_commands(
            package_json,
            frontend_package_json,
            frontend_cwd=frontend_cwd,
        ),
        "test_commands": _test_commands(
            package_json,
            frontend_package_json,
            frontend_cwd=frontend_cwd,
        ),
        "backend": _backend_facts(workspace_root, backend_files),
        "frontend": _frontend_facts(workspace_root, frontend_files),
        "shared_contracts": _shared_contracts(files),
        "high_value_files": _high_value_files(files),
        "file_manifest": {
            "total_files_indexed": len(files),
            "source_files_indexed": len(source_files),
            "truncated": len(files) >= MAX_FILES,
        },
        "code_graph": graph_facts,
        "risk_notes": _risk_notes(files, graph_facts),
    }


def _project_roots(files: Iterable[str]) -> list[dict[str, str]]:
    roots = []
    known = {
        "Backend/app/": ("backend", "FastAPI backend application"),
        "backend/app/": ("backend", "FastAPI backend application"),
        "Frontend/src/": ("frontend", "React/Electron renderer and app source"),
        "Frontend/src/main/": ("electron_main", "Electron main process"),
    }
    for prefix, (kind, description) in known.items():
        if any(path.startswith(prefix) for path in files):
            roots.append({"path": prefix.rstrip("/"), "kind": kind, "description": description})
    # Dynamically recognise user-application frontend roots (frontend/src/)
    # so the build-task planner and Frontend Agent see the real directory where
    # generated frontend code must live, not just the XcodeAgent dev workspace.
    # 直接平铺到根目录，不再嵌套 apps/<app_name>/
    if any(path.startswith("frontend/src/") for path in files):
        roots.append({
            "path": "frontend/src",
            "kind": "frontend",
            "description": "React frontend source for application",
        })
    return roots


def _tech_stack(
    package_json: dict[str, Any],
    frontend_package_json: dict[str, Any],
    *,
    pyproject: str,
    requirements: str,
    files: list[str],
) -> list[str]:
    dependencies = {
        **_dependencies(package_json),
        **_dependencies(frontend_package_json),
    }
    stack: set[str] = set()
    if "fastapi" in requirements.lower() or "fastapi" in pyproject.lower():
        stack.add("FastAPI")
    if any(path.endswith(".tsx") for path in files) or "react" in dependencies:
        stack.add("React")
    if "vite" in dependencies or any("vite.config" in path for path in files):
        stack.add("Vite")
    if "electron" in dependencies or any(
        path.startswith(("Frontend/src/main/", "frontend/src/main/"))
        for path in files
    ):
        stack.add("Electron")
    if "antd" in dependencies or "@ant-design/icons" in dependencies:
        stack.add("Ant Design")
    if "@ag-ui/client" in dependencies or "@ag-ui/core" in dependencies:
        stack.add("AG-UI")
    if any("langgraph" in path.lower() for path in files):
        stack.add("LangGraph")
    return sorted(stack)


def _entrypoints(files: list[str]) -> list[dict[str, str]]:
    candidates = {
        "Backend/app/main.py": "backend_api",
        "Backend/app/graph/workflow.py": "workflow_graph",
        "backend/app/main.py": "backend_api",
        "backend/app/graph/workflow.py": "workflow_graph",
        "Frontend/src/main/index.ts": "electron_main",
        "Frontend/src/renderer/src/main.tsx": "frontend_renderer",
        "Frontend/src/renderer/src/App.tsx": "frontend_app",
        "Frontend/vite.config.ts": "frontend_vite",
    }
    return [
        {"path": path, "kind": kind}
        for path, kind in candidates.items()
        if path in files
    ]


def _build_commands(
    package_json: dict[str, Any],
    frontend_package_json: dict[str, Any],
    *,
    frontend_cwd: str,
) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    if "build" in _scripts(frontend_package_json):
        commands.append({"cwd": frontend_cwd, "command": "pnpm build", "kind": "frontend_build"})
    if "build" in _scripts(package_json):
        commands.append({"cwd": ".", "command": "pnpm build", "kind": "workspace_build"})
    commands.append({"cwd": ".", "command": "curl -sS http://127.0.0.1:8000/health", "kind": "backend_health"})
    return commands


def _test_commands(
    package_json: dict[str, Any],
    frontend_package_json: dict[str, Any],
    *,
    frontend_cwd: str,
) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for cwd, package in ((".", package_json), (frontend_cwd, frontend_package_json)):
        scripts = _scripts(package)
        for name in ("test", "lint", "typecheck"):
            if name in scripts:
                commands.append({"cwd": cwd, "command": f"pnpm {name}", "kind": name})
    return commands


def _backend_facts(workspace_root: Path, files: list[str]) -> dict[str, Any]:
    routes = []
    models = []
    workflow_nodes = []
    agent_factories = []
    for relative in files:
        if not relative.endswith(".py"):
            continue
        content = _read_text(workspace_root / relative)
        if not content:
            continue
        routes.extend(_find_fastapi_routes(relative, content))
        models.extend(_find_python_classes(relative, content, "BaseModel"))
        workflow_nodes.extend(_find_workflow_nodes(relative, content))
        if "create_agent_bundle" in content or "create_react_agent" in content:
            agent_factories.append({"path": relative})
    return {
        "api_routes": routes,
        "models": models,
        "workflow_nodes": workflow_nodes,
        "agent_factories": agent_factories,
        "dir_structure": _dir_structure(files)
    }


def _dir_structure(files: list[str]) -> str:
    """把一组相对路径渲染成树形目录结构字符串。

    内部节点为目录（以 / 结尾），叶子为文件；用于 backend.dirStructure，
    让规划模型直观看到后端目录布局。
    """

    root: dict[str, Any] = {}
    for rel in sorted(files):
        if not rel:
            continue
        node = root
        for part in rel.split("/"):
            node = node.setdefault(part, {})
    lines: list[str] = []
    _render_tree_entries(root, "", lines)
    return "\n".join(lines)


def _render_tree_entries(
    entries: dict[str, Any],
    prefix: str,
    lines: list[str],
) -> None:
    keys = sorted(entries)
    for index, key in enumerate(keys):
        children = entries[key]
        is_last = index == len(keys) - 1
        connector = "└── " if is_last else "├── "
        label = f"{key}/" if children else key
        lines.append(f"{prefix}{connector}{label}")
        child_prefix = prefix + ("    " if is_last else "│   ")
        _render_tree_entries(children, child_prefix, lines)


def _frontend_facts(workspace_root: Path, files: list[str]) -> dict[str, Any]:
    components = []
    api_clients = []
    ipc_calls = []
    ag_ui_usage = []
    pages = []
    for relative in files:
        if Path(relative).suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        content = _read_text(workspace_root / relative)
        if not content:
            continue
        components.extend(_find_react_components(relative, content))
        if re.search(r"\b(fetch|axios)\s*\(", content):
            api_clients.append({"path": relative, "kind": "http_client"})
        if "ipcRenderer" in content or ".invoke(" in content:
            ipc_calls.append({"path": relative})
        if "@ag-ui/" in content or "AG-UI" in content:
            ag_ui_usage.append({"path": relative})
        if "pages/" in relative or relative.endswith("Page.tsx"):
            pages.append({"path": relative})
    return {
        "components": components,
        "pages": pages,
        "api_clients": api_clients,
        "ipc_calls": ipc_calls,
        "ag_ui_usage": ag_ui_usage,
    }


def _shared_contracts(files: list[str]) -> list[dict[str, str]]:
    markers = ("typings", "types", "contracts", "protocols", "domain")
    return [
        {"path": path}
        for path in files
        if any(marker in path.lower() for marker in markers)
        and Path(path).suffix in {".py", ".ts", ".tsx"}
    ][:200]


def _high_value_files(files: list[str]) -> list[dict[str, str]]:
    names = {
        "Backend/app/main.py",
        "Backend/app/graph/workflow.py",
        "Backend/app/graph/state.py",
        "Frontend/src/main/index.ts",
        "Frontend/src/renderer/src/pages/AppEntryPage.tsx",
        "Frontend/package.json",
        "package.json",
    }
    return [{"path": path} for path in files if path in names]


def _risk_notes(files: list[str], graph_facts: dict[str, Any]) -> list[str]:
    notes = []
    if not graph_facts.get("available"):
        notes.append("Code graph provider is not configured; snapshot uses deterministic file and pattern scanning only.")
    if len(files) >= MAX_FILES:
        notes.append(f"Workspace file listing reached the {MAX_FILES} file cap.")
    return notes


def _find_fastapi_routes(path: str, content: str) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    pattern = re.compile(
        r"@(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]",
        re.MULTILINE,
    )
    for match in pattern.finditer(content):
        routes.append({"path": path, "method": match.group(1).upper(), "route": match.group(2)})
    return routes


def _find_python_classes(path: str, content: str, base_name: str) -> list[dict[str, str]]:
    pattern = re.compile(rf"^class\s+([A-Za-z_][A-Za-z0-9_]*)\([^)]*{base_name}[^)]*\):", re.MULTILINE)
    return [{"path": path, "name": match.group(1)} for match in pattern.finditer(content)]


def _find_workflow_nodes(path: str, content: str) -> list[dict[str, str]]:
    if "builder.add_node" not in content:
        return []
    pattern = re.compile(r"builder\.add_node\(\s*['\"]([^'\"]+)['\"]")
    return [{"path": path, "name": match.group(1)} for match in pattern.finditer(content)]


def _find_react_components(path: str, content: str) -> list[dict[str, str]]:
    patterns = (
        re.compile(r"export\s+(?:default\s+)?function\s+([A-Z][A-Za-z0-9_]*)"),
        re.compile(r"const\s+([A-Z][A-Za-z0-9_]*)\s*[:=]"),
    )
    components = []
    for pattern in patterns:
        components.extend({"path": path, "name": match.group(1)} for match in pattern.finditer(content))
    return components[:50]


def _dependencies(package_json: dict[str, Any]) -> dict[str, Any]:
    return {
        **(package_json.get("dependencies") or {}),
        **(package_json.get("devDependencies") or {}),
    }


def _scripts(package_json: dict[str, Any]) -> dict[str, Any]:
    return package_json.get("scripts") or {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _read_text(path: Path) -> str:
    if not _can_read(path):
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _can_read(path: Path) -> bool:
    return path.is_file() and path.stat().st_size <= MAX_FILE_BYTES


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _untracked_files_hash(workspace_root: Path, manifest: str) -> str:
    entries = []
    for relative in sorted(path for path in manifest.splitlines() if path):
        if _is_ignored_path(relative):
            continue
        entries.append(f"{relative}:{_hash_file(workspace_root / relative)}")
    return _hash_text("\n".join(entries))

def _rg_ignore_globs() -> list[str]:
    """生成传给 ripgrep 的排除 glob，让大型构建产物目录直接在命令层被剪枝。

    这些 glob 与 _is_scoped_ignored_path/_is_ignored_path 的忽略范围保持一致，
    使 rg 在扫描阶段就跳过 backend/target、frontend/build、frontend/node_modules
    以及全局构建产物目录，避免先列出全部文件再在 Python 侧过滤。
    """

    return [
        "-g", "!backend/target/**",
        "-g", "!frontend/build/**",
        "-g", "!frontend/node_modules/**",
    ]

def _is_ignored_path(path: str) -> bool:
    if _is_scoped_ignored_path(path):
        return True
    parts = Path(path).parts
    return any(part in IGNORED_DIRS for part in parts)


def _is_scoped_ignored_path(path: str) -> bool:
    """命中用户应用构建产物的限根忽略规则。

    只忽略 backend/target（Maven 构建输出）、frontend/build 与
    frontend/node_modules（前端构建产物与依赖），避免工作区扫描枚举这些大目录。
    """

    parts = Path(path).parts
    if len(parts) < 2:
        return False
    root, child = parts[0], parts[1]
    if root.lower() == "backend":
        return child in {"target", ".idea"}
    if root.lower() == "frontend":
        return child in {"build", "node_modules"}
    return False
