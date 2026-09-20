"""按 Git 版本（tag/commit）只读读取工作区内容。

历史版本回看需要看到"那个版本当时"的文档与源码，而工作区只有一份（最新）。
这里用两个只读原语读取指定版本，**绝不检出**：

- `git ls-tree -r --name-only <rev>` 列出该版本的全部文件
- `git show <rev>:<path>` 读取单个文件内容

`git checkout <rev>` 会把工作区里正在开发的代码整体覆盖掉，因此本模块刻意不使用它，
也不产生任何写操作（工作区、索引与 HEAD 均不受影响）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.services.version_control import VersionControlError, _run_git
from app.workspace.workspace import (
    DEFAULT_IGNORED_DIRS,
    SENSITIVE_FILE_NAMES,
    _fail,
    _looks_binary,
    _workspace_payload,
)


def _resolve_revision(root: Path, revision: str) -> str:
    """确认 revision 是本仓库的合法提交，并返回其提交号。

    先解析再使用：既避免把任意字符串当 Git 选项/路径传入，也让"版本不存在"
    以明确错误暴露，而不是退化成空结果。
    """

    candidate = str(revision or "").strip()
    if not candidate or candidate.startswith("-"):
        _fail(400, f"Invalid revision: {revision}")
    try:
        completed = _run_git(root, ["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"])
    except VersionControlError:
        _fail(400, f"Invalid revision: {revision}")
    commit = completed.stdout.strip()
    if completed.returncode != 0 or not commit:
        _fail(404, f"Revision not found in this repository: {revision}")
    return commit


def _normalize_relative_path(value: str) -> str:
    """把仓库相对路径归一化，并拒绝越界与可能被当成 Git 选项的写法。"""

    raw = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not raw or raw == ".":
        return ""
    if raw.startswith("-"):
        _fail(400, f"Invalid path: {value}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        _fail(403, f"Path escapes workspace root: {value}")
    return "/".join(parts)


def _is_ignored(relative_path: str, *, include_hidden: bool) -> bool:
    """按与工作区读取一致的规则忽略依赖目录与隐藏项。"""

    for part in relative_path.split("/"):
        if part in DEFAULT_IGNORED_DIRS:
            return True
        if not include_hidden and part.startswith("."):
            return True
    return False


def _list_revision_files(root: Path, revision: str) -> list[str]:
    """列出指定版本的全部文件路径（仓库相对、已按忽略规则过滤）。"""

    completed = _run_git(root, ["ls-tree", "-r", "--name-only", "-z", revision])
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        _fail(400, f"Failed to list revision {revision}: {detail}")
    return [entry for entry in completed.stdout.split("\0") if entry]


def _build_tree(
    paths: list[str], *, base: str, root_name: str, max_depth: int, limit: int
) -> tuple[dict[str, Any], bool]:
    """把扁平的文件路径列表组装成与工作区读取同形的嵌套树。

    入参 `paths` 是**已剥掉 base 前缀**的相对路径列表；`root_name` 是根节点的显示名
    （工作区根用目录名，子目录用该目录名），与 `_entry_payload` 的 `path.name` 口径一致。
    """

    node: dict[str, Any] = {
        "path": base or ".",
        "name": root_name,
        "kind": "directory",
        "children": []
    }
    counter = {"count": 1, "truncated": False}

    def child_of(
        parent: dict[str, Any], name: str, path: str, kind: str
    ) -> tuple[dict[str, Any], bool]:
        """取或建子节点；第二个返回值表示是否**新建**（只有新建才计入 limit）。"""

        children = parent.setdefault("children", [])
        for existing in children:
            if existing["name"] == name:
                return existing, False
        created: dict[str, Any] = {"path": path, "name": name, "kind": kind}
        if kind == "directory":
            created["children"] = []
        children.append(created)
        return created, True

    for relative in paths:
        segments = [part for part in relative.split("/") if part]
        if not segments:
            continue
        cursor = node
        for index, segment in enumerate(segments):
            # 深度按"从根数第几层"计；到上限就停止下探，与 workspace_tree 一致
            # （它是静默停止、不标记 truncated，这里保持一致，避免误报截断）。
            if index + 1 > max_depth:
                break
            is_leaf = index == len(segments) - 1
            cursor_path = "/".join([part for part in (base, *segments[: index + 1]) if part])
            # 共享目录只算一次：按路径数计限会让深目录树被过早截断。
            child, created = child_of(
                cursor, segment, cursor_path, "file" if is_leaf else "directory"
            )
            if created:
                if counter["count"] >= limit:
                    counter["truncated"] = True
                    break
                counter["count"] += 1
            cursor = child

    def sort_children(current: dict[str, Any]) -> None:
        children = current.get("children")
        if not children:
            return
        children.sort(key=lambda item: (0 if item["kind"] == "directory" else 1, item["name"]))
        for child in children:
            sort_children(child)

    sort_children(node)
    return node, counter["truncated"]


def revision_tree(
    root: Path,
    revision: str,
    *,
    path: str = ".",
    max_depth: int = 3,
    include_hidden: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    """列出指定版本在 path 下的目录树；只读，不触碰工作区。"""

    commit = _resolve_revision(root, revision)
    base = _normalize_relative_path(path)
    all_files = _list_revision_files(root, commit)
    if base:
        prefix = f"{base}/"
        scoped = [entry[len(prefix):] for entry in all_files if entry.startswith(prefix)]
        if not scoped:
            _fail(404, f"Path does not exist in revision {revision}: {path}")
    else:
        scoped = list(all_files)
    visible = [entry for entry in scoped if not _is_ignored(entry, include_hidden=include_hidden)]
    # 根节点显示工作区目录名（与 workspace_tree 的 path.name 一致），子目录用该目录名。
    root_name = base.rsplit("/", 1)[-1] if base else root.name
    tree, truncated = _build_tree(
        visible, base=base, root_name=root_name, max_depth=max_depth, limit=limit
    )
    return {
        "tool": "workspace.tree",
        "workspace": _workspace_payload(root),
        "path": base or ".",
        "revision": revision,
        "tree": tree,
        "truncated": truncated,
    }


def revision_file(
    root: Path,
    revision: str,
    path: str,
    *,
    start_line: int = 1,
    max_lines: int = 400,
    max_chars: int = 20000,
) -> dict[str, Any]:
    """读取指定版本里的单个文件；只读，不触碰工作区。"""

    commit = _resolve_revision(root, revision)
    relative = _normalize_relative_path(path)
    if not relative:
        _fail(400, f"Path is not a file: {path}")
    # 敏感文件与工作区读取同级拒绝，不因为换了来源就放松。
    if relative.rsplit("/", 1)[-1] in SENSITIVE_FILE_NAMES:
        _fail(403, f"Refusing to read sensitive file: {path}")

    completed = _run_git(root, ["show", f"{commit}:{relative}"])
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        _fail(404, f"File not found in revision {revision}: {path} ({detail})")

    raw = completed.stdout.encode("utf-8", errors="replace")
    if _looks_binary(raw):
        _fail(415, f"File appears to be binary: {path}")

    text = completed.stdout
    lines = text.splitlines(keepends=True)
    start_index = start_line - 1
    if start_index >= len(lines):
        content = ""
        end_line = start_line - 1
        truncated = False
    else:
        selected: list[str] = []
        char_count = 0
        end_index = start_index
        truncated = False
        for index in range(start_index, len(lines)):
            if index - start_index >= max_lines:
                truncated = True
                break
            line = lines[index]
            if char_count + len(line) > max_chars:
                remaining = max(max_chars - char_count, 0)
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
        "path": relative,
        "revision": revision,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": len(lines),
        "truncated": truncated,
        "content": content,
    }
