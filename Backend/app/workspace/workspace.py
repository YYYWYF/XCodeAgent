from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.middleware.approvals import ApprovalGrant, approval_store, operation_fingerprint
from app.services.workspace_process_registry import workspace_process_registry


DEFAULT_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
}

SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
}

HIGH_RISK_COMMANDS = {
    "rm",
    "rmdir",
    "sudo",
    "su",
    "chmod",
    "chown",
    "dd",
    "mkfs",
    "diskutil",
    "shutdown",
    "reboot",
    "kill",
    "killall",
    "pkill",
    "cmd",
    "powershell",
    "pwsh",
}

HIGH_RISK_GIT_SUBCOMMANDS = {
    "reset",
    "clean",
    "restore",
    "checkout",
    "rebase",
}

MEDIUM_RISK_PACKAGE_SUBCOMMANDS = {
    "add",
    "install",
    "remove",
    "uninstall",
    "update",
}

CODE_CHANGE_DIFF_LIMIT = 50000


class WorkspaceRequest(BaseModel):
    workspace_root: Optional[str] = Field(
        default=None,
        description="Absolute workspace root selected by the desktop app. Defaults to the backend cwd.",
    )


class WorkspacePathRequest(WorkspaceRequest):
    path: str = Field(default=".", description="Workspace-relative path.")


class ListFilesRequest(WorkspacePathRequest):
    recursive: bool = Field(default=False)
    include_hidden: bool = Field(default=False)
    limit: int = Field(default=200, ge=1, le=2000)


class TreeRequest(WorkspacePathRequest):
    max_depth: int = Field(default=3, ge=1, le=8)
    include_hidden: bool = Field(default=False)
    limit: int = Field(default=500, ge=1, le=3000)


class ReadFileRequest(WorkspacePathRequest):
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=400, ge=1, le=5000)
    max_chars: int = Field(default=20000, ge=200, le=200000)
    allow_sensitive: bool = Field(default=False)
    approval: Optional[ApprovalGrant] = Field(default=None)


class WriteFileRequest(WorkspacePathRequest):
    content: str
    create_dirs: bool = Field(default=True)
    overwrite: bool = Field(default=True)
    dry_run: bool = Field(default=False)
    allow_sensitive: bool = Field(default=False)
    approval: Optional[ApprovalGrant] = Field(default=None)


class FileEdit(BaseModel):
    old_text: str = Field(min_length=1)
    new_text: str = Field(default="")
    replace_all: bool = Field(default=False)


class PatchFileRequest(WorkspacePathRequest):
    edits: List[FileEdit] = Field(min_length=1)
    expected_sha256: Optional[str] = Field(default=None)
    dry_run: bool = Field(default=False)
    allow_sensitive: bool = Field(default=False)
    approval: Optional[ApprovalGrant] = Field(default=None)


class DeleteFileRequest(WorkspacePathRequest):
    dry_run: bool = Field(default=False)
    allow_sensitive: bool = Field(default=False)
    approval: Optional[ApprovalGrant] = Field(default=None)


class SearchFilesRequest(WorkspaceRequest):
    query: str = Field(min_length=1)
    path: str = Field(default=".")
    include_hidden: bool = Field(default=False)
    limit: int = Field(default=200, ge=1, le=2000)


class SearchTextRequest(WorkspaceRequest):
    query: str = Field(min_length=1)
    path: str = Field(default=".")
    regex: bool = Field(default=False)
    case_sensitive: bool = Field(default=False)
    include_hidden: bool = Field(default=False)
    limit: int = Field(default=100, ge=1, le=1000)
    max_chars_per_match: int = Field(default=240, ge=40, le=1000)


class TerminalExecRequest(WorkspaceRequest):
    argv: List[str] = Field(default_factory=list)
    command: Optional[str] = Field(
        default=None,
        description="Optional shell-like command string. It is split with shlex and never run through a shell.",
    )
    cwd: str = Field(default=".")
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    approved: bool = Field(default=False)
    approval: Optional[ApprovalGrant] = Field(default=None)
    max_output_chars: int = Field(default=12000, ge=1000, le=100000)


class GitStatusRequest(WorkspaceRequest):
    porcelain: bool = Field(default=True)


class GitDiffRequest(WorkspaceRequest):
    path: Optional[str] = Field(default=None)
    staged: bool = Field(default=False)
    max_chars: int = Field(default=20000, ge=1000, le=200000)


def capabilities() -> Dict[str, Any]:
    return {
        "workspace": [
            "workspace.info",
            "workspace.list_files",
            "workspace.tree",
        ],
        "file": [
            "file.read",
            "file.write",
            "file.patch",
            "file.delete",
        ],
        "search": [
            "search.files",
            "search.text",
        ],
        "terminal": [
            "terminal.exec",
        ],
        "git": [
            "git.status",
            "git.diff",
        ],
        "safety": {
            "pathSandbox": "All paths are resolved inside workspace_root.",
            "sensitiveFiles": sorted(SENSITIVE_FILE_NAMES),
            "ignoredDirs": sorted(DEFAULT_IGNORED_DIRS),
            "approvals": (
                "Medium/high-risk terminal commands and sensitive or risky file changes return "
                "requires_approval with an approval id. Approvals can be granted once with the "
                "returned token or remembered for the same operation."
            ),
        },
    }


def build_prompt_context() -> str:
    tool_names = []
    for group, names in capabilities().items():
        if isinstance(names, list):
            tool_names.extend(names)

    workspace_context = _default_workspace_prompt_context()
    return (
        "# Local Workspace Tools\n"
        "The desktop backend exposes local workspace tools over HTTP. "
        f"{workspace_context}\n"
        "Use these names when describing tool work to the frontend: "
        f"{', '.join(tool_names)}.\n"
        "Important safety rules: keep all paths inside workspace_root; prefer file.patch over full rewrites; "
        "use file.delete instead of terminal commands when deleting files; "
        "preview diffs before applying risky edits; risky commands and sensitive file changes require "
        "explicit user approval before execution."
    )


def workspace_info(request: WorkspaceRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    return {
        "tool": "workspace.info",
        "workspace": _workspace_payload(root),
        "git": _git_info(root),
    }


def _default_workspace_prompt_context() -> str:
    try:
        root = _workspace_root(None)
        workspace = _workspace_payload(root)
        git = _git_info(root)
    except HTTPException as exc:
        return f"No valid default workspace is configured ({exc.detail})."

    git_text = "not a git repository"
    if git.get("is_repo"):
        git_root = git.get("root") or str(root)
        git_text = f"git repository at {git_root}"

    writable_text = "writable" if workspace["writable"] else "read-only"
    return (
        "The current default workspace is "
        f"{workspace['name']} at {workspace['root']} ({writable_text}; {git_text})."
    )


def list_files(request: ListFilesRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    base = _safe_path(root, request.path)
    if not base.exists():
        _fail(404, f"Path does not exist: {request.path}")
    if not base.is_dir():
        _fail(400, f"Path is not a directory: {request.path}")

    entries: List[Dict[str, Any]] = []
    if request.recursive:
        for dirpath, dirnames, filenames in os.walk(base):
            current = Path(dirpath)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not _should_ignore(current / name, root, include_hidden=request.include_hidden)
            ]
            for name in sorted(filenames):
                path = current / name
                if _should_ignore(path, root, include_hidden=request.include_hidden):
                    continue
                entries.append(_entry_payload(path, root))
                if len(entries) >= request.limit:
                    return _list_response(root, request.path, entries, truncated=True)
    else:
        for child in sorted(base.iterdir(), key=_sort_key):
            if _should_ignore(child, root, include_hidden=request.include_hidden):
                continue
            entries.append(_entry_payload(child, root))
            if len(entries) >= request.limit:
                return _list_response(root, request.path, entries, truncated=True)

    return _list_response(root, request.path, entries, truncated=False)


def workspace_tree(request: TreeRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    base = _safe_path(root, request.path)
    if not base.exists():
        _fail(404, f"Path does not exist: {request.path}")

    counter = {"count": 0, "truncated": False}
    tree = _tree_node(
        base,
        root,
        max_depth=request.max_depth,
        include_hidden=request.include_hidden,
        limit=request.limit,
        counter=counter,
    )
    return {
        "tool": "workspace.tree",
        "workspace": _workspace_payload(root),
        "path": _relative_path(base, root),
        "tree": tree,
        "truncated": counter["truncated"],
    }


def read_file(request: ReadFileRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    path = _safe_path(root, request.path)
    _assert_existing_file(path, root)
    if _is_sensitive_path(path):
        approval_response = _approval_required_response(
            tool="file.read",
            root=root,
            title="读取敏感文件",
            description=f"请求读取敏感文件 {_relative_path(path, root)}。",
            subject=_relative_path(path, root),
            risk={
                "level": "high",
                "reasons": [f"{path.name} may contain secrets or local credentials."],
            },
            operation_payload={
                "workspace_root": str(root),
                "path": _relative_path(path, root),
                "start_line": request.start_line,
                "max_lines": request.max_lines,
                "max_chars": request.max_chars,
            },
            grant=request.approval,
            response_base={
                "tool": "file.read",
                "workspace": _workspace_payload(root),
                "path": _relative_path(path, root),
            },
        )
        if approval_response:
            return approval_response
        _assert_readable_file(path, root, allow_sensitive=True)
    else:
        _assert_readable_file(path, root, allow_sensitive=request.allow_sensitive)

    raw = path.read_bytes()
    if _looks_binary(raw):
        _fail(415, f"File appears to be binary: {request.path}")

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    start_index = request.start_line - 1
    if start_index >= len(lines):
        content = ""
        end_line = request.start_line - 1
        truncated = False
    else:
        selected: List[str] = []
        char_count = 0
        end_index = start_index
        truncated = False
        for index in range(start_index, len(lines)):
            if index - start_index >= request.max_lines:
                truncated = True
                break
            line = lines[index]
            if char_count + len(line) > request.max_chars:
                remaining = max(request.max_chars - char_count, 0)
                selected.append(line[:remaining])
                end_index = index
                truncated = True
                break
            selected.append(line)
            char_count += len(line)
            end_index = index
        content = "".join(selected)
        end_line = end_index + 1

    return {
        "tool": "file.read",
        "workspace": _workspace_payload(root),
        "path": _relative_path(path, root),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "start_line": request.start_line,
        "end_line": end_line,
        "total_lines": len(lines),
        "truncated": truncated,
        "content": content,
    }


def write_file(request: WriteFileRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    path = _safe_path(root, request.path)
    if path.exists() and not path.is_file():
        _fail(400, f"Path is not a file: {request.path}")
    if path.exists() and not request.overwrite:
        _fail(409, f"File already exists: {request.path}")

    existed = path.exists()
    before = path.read_text(encoding="utf-8") if existed else ""
    changed = (not existed) or before != request.content
    diff = _text_diff(before, request.content, fromfile=request.path, tofile=request.path)
    code_change = _code_change_payload(
        tool="file.write",
        root=root,
        path=path,
        before=before,
        after=request.content,
        change_type="modified" if existed else "added",
        executed=False,
    ) if changed else None
    risk = _classify_file_write(path, root, before, request.content, existed=existed)
    if not request.dry_run:
        approval_response = _approval_required_response(
            tool="file.write",
            root=root,
            title="写入工作区文件",
            description=f"请求写入 {_relative_path(path, root)}。",
            subject=_relative_path(path, root),
            risk=risk,
            operation_payload={
                "workspace_root": str(root),
                "path": _relative_path(path, root),
                "content_sha256": hashlib.sha256(request.content.encode("utf-8")).hexdigest(),
                "overwrite": request.overwrite,
                "create_dirs": request.create_dirs,
            },
            grant=request.approval,
            details=_truncate(diff, 8000) if diff else None,
            response_base={
                "tool": "file.write",
                "workspace": _workspace_payload(root),
                "path": _relative_path(path, root),
                "dry_run": request.dry_run,
                "changed": changed,
                "diff": diff,
                "code_change": code_change,
            },
        )
        if approval_response:
            return approval_response

    if _is_sensitive_path(path):
        _assert_not_sensitive(path, allow_sensitive=True)
    else:
        _assert_not_sensitive(path, allow_sensitive=request.allow_sensitive)
    if not path.parent.exists():
        if not request.create_dirs:
            _fail(404, f"Parent directory does not exist: {_relative_path(path.parent, root)}")
        if not request.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
    if not request.dry_run:
        path.write_text(request.content, encoding="utf-8")

    return {
        "tool": "file.write",
        "workspace": _workspace_payload(root),
        "path": _relative_path(path, root),
        "dry_run": request.dry_run,
        "requires_approval": False,
        "executed": not request.dry_run,
        "changed": changed,
        "diff": diff,
        "code_change": _mark_code_change_executed(code_change, executed=not request.dry_run),
        "sha256": hashlib.sha256(request.content.encode("utf-8")).hexdigest(),
    }


def patch_file(request: PatchFileRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    path = _safe_path(root, request.path)
    _assert_existing_file(path, root)
    if _is_sensitive_path(path):
        _assert_readable_file(path, root, allow_sensitive=True)
    else:
        _assert_readable_file(path, root, allow_sensitive=request.allow_sensitive)

    raw = path.read_bytes()
    if _looks_binary(raw):
        _fail(415, f"File appears to be binary: {request.path}")
    before = raw.decode("utf-8", errors="replace")
    before_hash = hashlib.sha256(raw).hexdigest()
    if request.expected_sha256 and request.expected_sha256 != before_hash:
        _fail(409, "File changed since it was read; expected_sha256 does not match.")

    after = before
    applied: List[Dict[str, Any]] = []
    for index, edit in enumerate(request.edits):
        count = after.count(edit.old_text)
        if count == 0:
            _fail(409, f"Edit {index + 1} old_text was not found in {request.path}.")
        replacements = count if edit.replace_all else 1
        after = after.replace(edit.old_text, edit.new_text, replacements)
        applied.append(
            {
                "index": index,
                "matches": count,
                "replacements": replacements,
                "replace_all": edit.replace_all,
            }
        )

    diff = _text_diff(before, after, fromfile=request.path, tofile=request.path)
    code_change = _code_change_payload(
        tool="file.patch",
        root=root,
        path=path,
        before=before,
        after=after,
        change_type="modified",
        executed=False,
    ) if before != after else None
    risk = _classify_file_patch(path, root, before != after)
    if not request.dry_run:
        approval_response = _approval_required_response(
            tool="file.patch",
            root=root,
            title="修改工作区文件",
            description=f"请求修改 {_relative_path(path, root)}。",
            subject=_relative_path(path, root),
            risk=risk,
            operation_payload={
                "workspace_root": str(root),
                "path": _relative_path(path, root),
                "before_sha256": before_hash,
                "after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
                "edits": [_model_payload(edit) for edit in request.edits],
            },
            grant=request.approval,
            details=_truncate(diff, 8000) if diff else None,
            response_base={
                "tool": "file.patch",
                "workspace": _workspace_payload(root),
                "path": _relative_path(path, root),
                "dry_run": request.dry_run,
                "changed": before != after,
                "applied": applied,
                "before_sha256": before_hash,
                "after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
                "diff": diff,
                "code_change": code_change,
            },
        )
        if approval_response:
            return approval_response

    if not request.dry_run:
        path.write_text(after, encoding="utf-8")

    return {
        "tool": "file.patch",
        "workspace": _workspace_payload(root),
        "path": _relative_path(path, root),
        "dry_run": request.dry_run,
        "requires_approval": False,
        "executed": not request.dry_run,
        "changed": before != after,
        "applied": applied,
        "before_sha256": before_hash,
        "after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
        "diff": diff,
        "code_change": _mark_code_change_executed(code_change, executed=not request.dry_run),
    }


def delete_file(request: DeleteFileRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    path = _safe_path(root, request.path)
    _assert_existing_file(path, root)
    if _is_sensitive_path(path):
        _assert_readable_file(path, root, allow_sensitive=True)
    else:
        _assert_readable_file(path, root, allow_sensitive=request.allow_sensitive)

    raw = path.read_bytes()
    before_hash = hashlib.sha256(raw).hexdigest()
    binary = _looks_binary(raw)
    before = "" if binary else raw.decode("utf-8", errors="replace")
    diff = "" if binary else _text_diff(before, "", fromfile=request.path, tofile=request.path)
    code_change = _code_change_payload(
        tool="file.delete",
        root=root,
        path=path,
        before=before,
        after="",
        change_type="deleted",
        executed=False,
        binary=binary,
    )
    risk = _classify_file_delete(path, root)
    if not request.dry_run:
        approval_response = _approval_required_response(
            tool="file.delete",
            root=root,
            title="删除工作区文件",
            description=f"请求删除 {_relative_path(path, root)}。",
            subject=_relative_path(path, root),
            risk=risk,
            operation_payload={
                "workspace_root": str(root),
                "path": _relative_path(path, root),
                "before_sha256": before_hash,
            },
            grant=request.approval,
            details=_truncate(diff, 8000) if diff else "Binary or empty file deletion.",
            response_base={
                "tool": "file.delete",
                "workspace": _workspace_payload(root),
                "path": _relative_path(path, root),
                "dry_run": request.dry_run,
                "changed": True,
                "binary": binary,
                "before_sha256": before_hash,
                "diff": diff,
                "code_change": code_change,
            },
        )
        if approval_response:
            return approval_response

    if not request.dry_run:
        path.unlink()

    return {
        "tool": "file.delete",
        "workspace": _workspace_payload(root),
        "path": _relative_path(path, root),
        "dry_run": request.dry_run,
        "requires_approval": False,
        "executed": not request.dry_run,
        "changed": True,
        "binary": binary,
        "before_sha256": before_hash,
        "diff": diff,
        "code_change": _mark_code_change_executed(code_change, executed=not request.dry_run),
    }


def search_files(request: SearchFilesRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    base = _safe_path(root, request.path)
    if not base.exists():
        _fail(404, f"Path does not exist: {request.path}")
    if not base.is_dir():
        _fail(400, f"Path is not a directory: {request.path}")

    query = request.query.lower()
    matches: List[Dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not _should_ignore(current / name, root, include_hidden=request.include_hidden)
        ]
        for name in sorted(filenames):
            path = current / name
            if _should_ignore(path, root, include_hidden=request.include_hidden):
                continue
            rel = _relative_path(path, root)
            if query in name.lower() or fnmatch.fnmatch(rel.lower(), query):
                matches.append(_entry_payload(path, root))
                if len(matches) >= request.limit:
                    return _search_response(root, request.query, matches, truncated=True)

    return _search_response(root, request.query, matches, truncated=False)


def search_text(request: SearchTextRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    base = _safe_path(root, request.path)
    if not base.exists():
        _fail(404, f"Path does not exist: {request.path}")

    if shutil.which("rg"):
        return _search_text_with_rg(request, root, base)
    return _search_text_with_python(request, root, base)


def terminal_exec(request: TerminalExecRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    cwd = _safe_path(root, request.cwd)
    if not cwd.exists() or not cwd.is_dir():
        _fail(400, f"cwd is not a directory: {request.cwd}")

    argv = _command_argv(request)
    risk = _classify_command(argv)
    approval_response = _approval_required_response(
        tool="terminal.exec",
        root=root,
        title="执行终端命令",
        description=f"请求在 {_relative_path(cwd, root)} 执行命令。",
        subject=" ".join(argv),
        risk=risk,
        operation_payload={
            "workspace_root": str(root),
            "cwd": _relative_path(cwd, root),
            "argv": argv,
            "timeout_seconds": request.timeout_seconds,
            "max_output_chars": request.max_output_chars,
        },
        grant=request.approval,
        response_base={
            "tool": "terminal.exec",
            "workspace": _workspace_payload(root),
            "cwd": _relative_path(cwd, root),
            "argv": argv,
            "risk": risk,
        },
    )
    if approval_response:
        return approval_response

    try:
        completed = workspace_process_registry.run(
            argv,
            workspace=root,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=request.timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        _fail(404, f"Command not found: {argv[0]}")
    except subprocess.TimeoutExpired as exc:
        return {
            "tool": "terminal.exec",
            "workspace": _workspace_payload(root),
            "cwd": _relative_path(cwd, root),
            "argv": argv,
            "risk": risk,
            "requires_approval": False,
            "executed": True,
            "timed_out": True,
            "returncode": None,
            "stdout": _truncate(exc.stdout or "", request.max_output_chars),
            "stderr": _truncate(exc.stderr or "", request.max_output_chars),
        }

    return {
        "tool": "terminal.exec",
        "workspace": _workspace_payload(root),
        "cwd": _relative_path(cwd, root),
        "argv": argv,
        "risk": risk,
        "requires_approval": False,
        "executed": True,
        "timed_out": False,
        "returncode": completed.returncode,
        "stdout": _truncate(completed.stdout, request.max_output_chars),
        "stderr": _truncate(completed.stderr, request.max_output_chars),
    }


def git_status(request: GitStatusRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    _assert_git_repo(root)
    args = ["git", "status", "--short"] if request.porcelain else ["git", "status"]
    completed = _run_git(args, root, max_chars=30000)
    return {
        "tool": "git.status",
        "workspace": _workspace_payload(root),
        "porcelain": request.porcelain,
        **completed,
    }


def git_diff(request: GitDiffRequest) -> Dict[str, Any]:
    root = _workspace_root(request.workspace_root)
    _assert_git_repo(root)
    args = ["git", "diff"]
    if request.staged:
        args.append("--staged")
    if request.path:
        path = _safe_path(root, request.path)
        args.extend(["--", _relative_path(path, root)])
    completed = _run_git(args, root, max_chars=request.max_chars)
    return {
        "tool": "git.diff",
        "workspace": _workspace_payload(root),
        "staged": request.staged,
        "path": request.path,
        **completed,
    }


def _workspace_root(value: Optional[str]) -> Path:
    configured = value or os.getenv("XCODEAGENT_WORKSPACE_ROOT") or os.getcwd()
    root = Path(configured).expanduser().resolve()
    if not root.exists():
        _fail(404, f"Workspace root does not exist: {root}")
    if not root.is_dir():
        _fail(400, f"Workspace root is not a directory: {root}")
    return root


def _safe_path(root: Path, value: Optional[str]) -> Path:
    raw = Path(value or ".").expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve(strict=False)
    if not _is_relative_to(resolved, root):
        _fail(403, f"Path escapes workspace root: {value}")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _relative_path(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return str(path)
    return "." if str(rel) == "." else str(rel)


def _workspace_payload(root: Path) -> Dict[str, Any]:
    return {
        "root": str(root),
        "name": root.name,
        "writable": os.access(root, os.W_OK),
    }


def _git_info(root: Path) -> Dict[str, Any]:
    if not shutil.which("git"):
        return {"available": False, "is_repo": False}
    completed = workspace_process_registry.run(
        ["git", "rev-parse", "--show-toplevel"],
        workspace=root,
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    return {
        "available": True,
        "is_repo": completed.returncode == 0,
        "root": completed.stdout.strip() if completed.returncode == 0 else None,
    }


def _assert_git_repo(root: Path) -> None:
    info = _git_info(root)
    if not info["available"]:
        _fail(404, "git is not installed or not on PATH.")
    if not info["is_repo"]:
        _fail(400, "Workspace is not inside a git repository.")


def _run_git(args: List[str], root: Path, *, max_chars: int) -> Dict[str, Any]:
    completed = workspace_process_registry.run(
        args,
        workspace=root,
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return {
        "returncode": completed.returncode,
        "stdout": _truncate(completed.stdout, max_chars),
        "stderr": _truncate(completed.stderr, max_chars),
    }


def _assert_readable_file(path: Path, root: Path, *, allow_sensitive: bool) -> None:
    _assert_not_sensitive(path, allow_sensitive=allow_sensitive)
    _assert_existing_file(path, root)


def _assert_existing_file(path: Path, root: Path) -> None:
    if not path.exists():
        _fail(404, f"File does not exist: {_relative_path(path, root)}")
    if not path.is_file():
        _fail(400, f"Path is not a file: {_relative_path(path, root)}")


def _assert_not_sensitive(path: Path, *, allow_sensitive: bool) -> None:
    if allow_sensitive:
        return
    if _is_sensitive_path(path):
        _fail(403, f"Refusing to access sensitive file without allow_sensitive=true: {path.name}")


def _is_sensitive_path(path: Path) -> bool:
    return path.name in SENSITIVE_FILE_NAMES


def _approval_required_response(
    *,
    tool: str,
    root: Path,
    title: str,
    description: str,
    subject: str,
    risk: Dict[str, Any],
    operation_payload: Dict[str, Any],
    grant: Optional[ApprovalGrant],
    response_base: Dict[str, Any],
    details: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not _risk_requires_approval(risk):
        return None

    operation_key = operation_fingerprint(tool, operation_payload)
    if approval_store.is_operation_approved(tool=tool, operation_key=operation_key):
        return None
    if grant is not None:
        approval_store.consume(tool=tool, operation_key=operation_key, grant=grant)
        return None
    if approval_store.consume_approved_once(tool=tool, operation_key=operation_key):
        return None

    approval = approval_store.request(
        tool=tool,
        operation_key=operation_key,
        title=title,
        description=description,
        subject=subject,
        risk=risk,
        details=details,
    )
    response = dict(response_base)
    code_change = response.get("code_change")
    if isinstance(code_change, dict):
        response["code_change"] = {**code_change, "approvalId": approval.get("id")}
    return {
        **response,
        "risk": risk,
        "requires_approval": True,
        "executed": False,
        "approval": approval,
    }


def _risk_requires_approval(risk: Dict[str, Any]) -> bool:
    return risk.get("level") in {"medium", "high"}


def _classify_file_write(
    path: Path,
    root: Path,
    before: str,
    after: str,
    *,
    existed: bool,
) -> Dict[str, Any]:
    reasons: List[str] = []
    level = "low"
    if existed and before == after:
        return {"level": level, "reasons": reasons}

    if _is_sensitive_path(path):
        level = "high"
        reasons.append(f"{path.name} may contain secrets or local credentials.")
    elif path.exists():
        level = "medium"
        reasons.append(f"Writing {_relative_path(path, root)} will overwrite an existing file.")
    else:
        level = "medium"
        reasons.append(f"Writing {_relative_path(path, root)} will create a new file.")

    return {"level": level, "reasons": reasons}


def _classify_file_patch(path: Path, root: Path, changed: bool) -> Dict[str, Any]:
    reasons: List[str] = []
    level = "low"
    if not changed:
        return {"level": level, "reasons": reasons}

    if _is_sensitive_path(path):
        level = "high"
        reasons.append(f"{path.name} may contain secrets or local credentials.")
    else:
        level = "medium"
        reasons.append(f"Patching {_relative_path(path, root)} will modify an existing file.")

    return {"level": level, "reasons": reasons}


def _classify_file_delete(path: Path, root: Path) -> Dict[str, Any]:
    if _is_sensitive_path(path):
        return {
            "level": "high",
            "reasons": [f"{path.name} may contain secrets or local credentials."],
        }
    return {
        "level": "medium",
        "reasons": [f"Deleting {_relative_path(path, root)} will remove an existing file."],
    }


def _model_payload(value: BaseModel) -> Dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump()
    return value.dict()


def _looks_binary(raw: bytes) -> bool:
    sample = raw[:4096]
    return b"\0" in sample


def _entry_payload(path: Path, root: Path) -> Dict[str, Any]:
    stat = path.lstat()
    if path.is_symlink():
        kind = "symlink"
    elif path.is_dir():
        kind = "directory"
    elif path.is_file():
        kind = "file"
    else:
        kind = "other"

    return {
        "path": _relative_path(path, root),
        "name": path.name,
        "kind": kind,
        "size": stat.st_size if kind == "file" else None,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _sort_key(path: Path) -> tuple[int, str]:
    return (0 if path.is_dir() else 1, path.name.lower())


def _should_ignore(path: Path, root: Path, *, include_hidden: bool) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    for part in parts:
        if part in DEFAULT_IGNORED_DIRS:
            return True
        if not include_hidden and part.startswith("."):
            return True
    return False


def _list_response(root: Path, path: str, entries: List[Dict[str, Any]], *, truncated: bool) -> Dict[str, Any]:
    return {
        "tool": "workspace.list_files",
        "workspace": _workspace_payload(root),
        "path": path,
        "entries": entries,
        "count": len(entries),
        "truncated": truncated,
    }


def _tree_node(
    path: Path,
    root: Path,
    *,
    max_depth: int,
    include_hidden: bool,
    limit: int,
    counter: Dict[str, Any],
) -> Dict[str, Any]:
    counter["count"] += 1
    if counter["count"] > limit:
        counter["truncated"] = True
        return {"path": _relative_path(path, root), "name": path.name, "kind": "truncated"}

    node = _entry_payload(path, root)
    if max_depth <= 1 or not path.is_dir():
        return node

    children: List[Dict[str, Any]] = []
    for child in sorted(path.iterdir(), key=_sort_key):
        if _should_ignore(child, root, include_hidden=include_hidden):
            continue
        if counter["count"] >= limit:
            counter["truncated"] = True
            break
        children.append(
            _tree_node(
                child,
                root,
                max_depth=max_depth - 1,
                include_hidden=include_hidden,
                limit=limit,
                counter=counter,
            )
        )
    node["children"] = children
    return node


def _text_diff(before: str, after: str, *, fromfile: str, tofile: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{fromfile}",
            tofile=f"b/{tofile}",
        )
    )


def _diff_stats(diff: str) -> Dict[str, int]:
    additions = 0
    deletions = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return {"additions": additions, "deletions": deletions}


def _code_change_payload(
    *,
    tool: str,
    root: Path,
    path: Path,
    before: str,
    after: str,
    change_type: str,
    executed: bool,
    binary: bool = False,
) -> Dict[str, Any]:
    rel = _relative_path(path, root)
    diff = "" if binary else _text_diff(before, after, fromfile=rel, tofile=rel)
    stats = _diff_stats(diff)
    digest = hashlib.sha256(f"{tool}:{rel}:{diff}:{change_type}".encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"{tool}:{rel}:{digest}",
        "tool": tool,
        "path": rel,
        "changeType": change_type,
        "additions": stats["additions"],
        "deletions": stats["deletions"],
        "diff": _truncate(diff, CODE_CHANGE_DIFF_LIMIT),
        "truncated": len(diff) > CODE_CHANGE_DIFF_LIMIT,
        "binary": binary,
        "executed": executed,
    }


def _mark_code_change_executed(
    code_change: Optional[Dict[str, Any]],
    *,
    executed: bool,
) -> Optional[Dict[str, Any]]:
    if code_change is None:
        return None
    return {**code_change, "executed": executed}


def _search_response(
    root: Path,
    query: str,
    matches: List[Dict[str, Any]],
    *,
    truncated: bool,
) -> Dict[str, Any]:
    return {
        "tool": "search.files",
        "workspace": _workspace_payload(root),
        "query": query,
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
    }


def _search_text_with_rg(request: SearchTextRequest, root: Path, base: Path) -> Dict[str, Any]:
    args = [
        "rg",
        "--line-number",
        "--column",
        "--no-heading",
        "--color",
        "never",
        "--max-count",
        str(request.limit),
    ]
    if not request.regex:
        args.append("--fixed-strings")
    if not request.case_sensitive:
        args.append("--ignore-case")
    if request.include_hidden:
        args.append("--hidden")
    for ignored in sorted(DEFAULT_IGNORED_DIRS):
        args.extend(["--glob", f"!{ignored}/**"])
    args.extend([request.query, _relative_path(base, root)])

    completed = workspace_process_registry.run(
        args,
        workspace=root,
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode not in {0, 1}:
        _fail(502, completed.stderr.strip() or "ripgrep search failed.")

    matches: List[Dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        if len(matches) >= request.limit:
            break
        parsed = _parse_rg_line(line)
        if parsed:
            parsed["text"] = _truncate(parsed["text"], request.max_chars_per_match)
            matches.append(parsed)

    return {
        "tool": "search.text",
        "workspace": _workspace_payload(root),
        "query": request.query,
        "matches": matches,
        "count": len(matches),
        "truncated": len(matches) >= request.limit,
        "engine": "rg",
    }


def _parse_rg_line(line: str) -> Optional[Dict[str, Any]]:
    parts = line.split(":", 3)
    if len(parts) != 4:
        return None
    path, line_number, column, text = parts
    try:
        return {
            "path": path,
            "line": int(line_number),
            "column": int(column),
            "text": text,
        }
    except ValueError:
        return None


def _search_text_with_python(request: SearchTextRequest, root: Path, base: Path) -> Dict[str, Any]:
    matcher = _text_matcher(request.query, regex=request.regex, case_sensitive=request.case_sensitive)
    matches: List[Dict[str, Any]] = []

    paths = [base] if base.is_file() else _walk_files(base, root, include_hidden=request.include_hidden)
    for path in paths:
        if _should_ignore(path, root, include_hidden=request.include_hidden):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if _looks_binary(raw):
            continue
        text = raw.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            column = matcher(line)
            if column is None:
                continue
            matches.append(
                {
                    "path": _relative_path(path, root),
                    "line": line_number,
                    "column": column,
                    "text": _truncate(line, request.max_chars_per_match),
                }
            )
            if len(matches) >= request.limit:
                return _search_text_response(root, request.query, matches, truncated=True, engine="python")

    return _search_text_response(root, request.query, matches, truncated=False, engine="python")


def _walk_files(base: Path, root: Path, *, include_hidden: bool) -> List[Path]:
    paths: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not _should_ignore(current / name, root, include_hidden=include_hidden)
        ]
        for name in sorted(filenames):
            path = current / name
            if not _should_ignore(path, root, include_hidden=include_hidden):
                paths.append(path)
    return paths


def _text_matcher(query: str, *, regex: bool, case_sensitive: bool) -> Callable[[str], Optional[int]]:
    if regex:
        import re

        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query, flags)

        def match_regex(line: str) -> Optional[int]:
            match = pattern.search(line)
            return match.start() + 1 if match else None

        return match_regex

    needle = query if case_sensitive else query.lower()

    def match_literal(line: str) -> Optional[int]:
        haystack = line if case_sensitive else line.lower()
        index = haystack.find(needle)
        return index + 1 if index >= 0 else None

    return match_literal


def _search_text_response(
    root: Path,
    query: str,
    matches: List[Dict[str, Any]],
    *,
    truncated: bool,
    engine: str,
) -> Dict[str, Any]:
    return {
        "tool": "search.text",
        "workspace": _workspace_payload(root),
        "query": query,
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
        "engine": engine,
    }


def _command_argv(request: TerminalExecRequest) -> List[str]:
    """优先使用结构化 argv，并按宿主系统规则兼容解析旧 command 字段。"""

    argv = request.argv or []
    if request.command:
        argv = shlex.split(request.command, posix=os.name != "nt")
        if os.name == "nt":
            argv = [_strip_windows_argument_quotes(part) for part in argv]
    argv = [part for part in argv if part]
    if not argv:
        _fail(400, "terminal.exec requires argv or command.")
    if any("\x00" in part for part in argv):
        _fail(400, "Command arguments cannot contain null bytes.")
    return argv


def _strip_windows_argument_quotes(value: str) -> str:
    """移除 Windows shlex 保留的成对外层引号，同时保留参数内部内容。"""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _classify_command(argv: List[str]) -> Dict[str, Any]:
    executable = Path(argv[0]).name.lower()
    if os.name == "nt":
        executable = Path(executable).stem
    reasons: List[str] = []
    level = "low"

    if executable in HIGH_RISK_COMMANDS:
        level = "high"
        reasons.append(f"{executable} can modify or control the host system.")

    subcommand = argv[1].lower() if len(argv) > 1 else ""
    if executable == "git" and subcommand in HIGH_RISK_GIT_SUBCOMMANDS:
        level = "high"
        reasons.append(f"git {subcommand} can discard or rewrite workspace changes.")

    if executable in {"npm", "pnpm", "yarn"} and subcommand in MEDIUM_RISK_PACKAGE_SUBCOMMANDS:
        if level != "high":
            level = "medium"
        reasons.append(f"{executable} {subcommand} changes dependencies or may access the network.")

    return {
        "level": level,
        "reasons": reasons,
    }


def _truncate(value: Any, max_chars: int) -> str:
    text = value if isinstance(value, str) else str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n... [truncated]"


def _fail(status_code: int, message: str) -> None:
    raise HTTPException(status_code=status_code, detail=message)
