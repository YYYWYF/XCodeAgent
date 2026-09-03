"""独立数据源目录的分文件存储实现。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


DATA_SOURCE_DIRECTORY_NAME = "datasource"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class DataSourceStorageError(ValueError):
    """表示独立数据源存储文件损坏或无法安全访问。"""


def data_sources_directory(workspace_root: str | Path, *, create: bool = True) -> Path:
    """返回工作区的独立数据源目录，并按需创建目录。"""

    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise DataSourceStorageError("当前工作区不存在或不是目录。")
    agent_root = root / ".xcodeagent"
    if agent_root.is_symlink():
        raise DataSourceStorageError("工作区 .xcodeagent 不允许使用符号链接。")
    if create:
        agent_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    elif not agent_root.exists():
        return agent_root / DATA_SOURCE_DIRECTORY_NAME
    if not agent_root.is_dir():
        raise DataSourceStorageError("工作区 .xcodeagent 不可用。")
    directory = agent_root / DATA_SOURCE_DIRECTORY_NAME
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise DataSourceStorageError("独立数据源目录不可用。")
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def data_sources_index_file(workspace_root: str | Path, *, create: bool = True) -> Path:
    """返回独立数据源索引文件路径。"""

    return data_sources_directory(workspace_root, create=create) / "index.json"


def read_sources(
    workspace_root: str | Path,
    *,
    detail: bool = True,
    source_id: str | None = None,
    operation_id: str | None = None,
) -> list[dict[str, Any]]:
    """按需读取数据源；列表默认只读取索引，详情再读取目标资源文件。"""

    index_path = data_sources_index_file(workspace_root)
    if not index_path.exists():
        return []
    index = _read_json(index_path, "数据源索引")
    entries = index.get("sources") if isinstance(index, dict) else None
    if not isinstance(entries, list):
        raise DataSourceStorageError("数据源索引必须包含 sources 数组。")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    matched_source = False
    for entry in entries:
        if not isinstance(entry, dict):
            raise DataSourceStorageError("数据源索引包含无效资源引用。")
        current_source_id = _require_id(entry.get("id"), "数据源")
        source_type = entry.get("type")
        if source_type not in {"database", "external_api"}:
            raise DataSourceStorageError("数据源索引包含无效资源类型。")
        if current_source_id in seen:
            raise DataSourceStorageError("数据源索引包含重复 ID。")
        seen.add(current_source_id)
        if source_id is not None and source_id != current_source_id:
            result.append(entry)
            continue
        matched_source = True
        if not detail:
            result.append(entry)
            continue
        if source_type == "database":
            source_path = data_sources_directory(workspace_root) / "databases" / f"{current_source_id}.json"
            source = _read_json(source_path, "数据库数据源")
        else:
            source_path = (
                data_sources_directory(workspace_root) / "external-apis" / current_source_id / "source.json"
            )
            source = _read_json(source_path, "外部 API 域名")
            source = _assemble_external_source(
                workspace_root,
                source,
                summary_source=entry,
                operation_id=operation_id,
            )
        if source.get("id") != current_source_id or source.get("type") != source_type:
            raise DataSourceStorageError("数据源文件与索引引用不一致。")
        result.append(source)
    if source_id is not None and not matched_source:
        raise DataSourceStorageError("目标数据源不存在。")
    return result


def write_sources(workspace_root: str | Path, sources: list[dict[str, Any]]) -> None:
    """将完整候选目录拆分写入，只替换变化文件并清理失效文件。"""

    directory = data_sources_directory(workspace_root)
    desired = _build_file_payloads(sources, directory)
    desired_paths = set(desired)
    existing_paths = _managed_paths(directory)
    touched = existing_paths | desired_paths
    snapshots = {path: path.read_bytes() for path in existing_paths}
    index_path = directory / "index.json"
    try:
        for path, payload in desired.items():
            # 索引最后发布，前面的资源文件全部准备完成后才更新引用。
            if path == index_path:
                continue
            if path in existing_paths and path.read_bytes() == payload:
                continue
            _write_bytes_atomically(path, payload)
        for path in sorted(existing_paths - desired_paths, key=lambda item: len(item.parts), reverse=True):
            if path.exists():
                path.unlink()
        if index_path not in existing_paths or index_path.read_bytes() != desired[index_path]:
            _write_bytes_atomically(index_path, desired[index_path])
        _remove_empty_directories(directory)
    except Exception as exc:
        _restore_paths(touched, snapshots, directory)
        raise DataSourceStorageError("数据源目录写入失败，已恢复本次修改前的文件。") from exc


def _build_file_payloads(sources: list[dict[str, Any]], root_dir: Path) -> dict[Path, bytes]:
    """把完整数据源对象转换为索引、资源和接口文件内容。"""

    payloads: dict[Path, bytes] = {}
    index_entries: list[dict[str, Any]] = []
    for source in sources:
        source_id = _require_id(source.get("id"), "数据源")
        source_type = source.get("type")
        if source_type not in {"database", "external_api"}:
            raise DataSourceStorageError("数据源类型无效。")
        index_entries.append(_index_entry(source))
        if source_type == "database":
            path = root_dir / "databases" / f"{source_id}.json"
            payloads[path] = _encode(source)
            continue
        source_copy = dict(source)
        directories = []
        for directory_item in source_copy.pop("directories", []) or []:
            directory_id = _require_id(directory_item.get("id"), "目录")
            operation_ids: list[str] = []
            for operation in directory_item.get("operations", []) or []:
                operation_id = _require_id(operation.get("id"), "接口")
                operation_ids.append(operation_id)
                operation_path = (
                    root_dir / "external-apis" / source_id
                    / "operations"
                    / f"{operation_id}.json"
                )
                payloads[operation_path] = _encode(operation)
            directories.append({"id": directory_id, "name": directory_item.get("name", ""), "operationIds": operation_ids})
        source_copy["directories"] = directories
        source_path = root_dir / "external-apis" / source_id / "source.json"
        payloads[source_path] = _encode(source_copy)
    index_path = root_dir / "index.json"
    payloads[index_path] = _encode({"sources": index_entries})
    return payloads


def _index_entry(source: dict[str, Any]) -> dict[str, Any]:
    """生成列表所需的轻量索引摘要，不写入密码和接口详情。"""

    source_type = source.get("type")
    if source_type == "database":
        return {
            key: value
            for key, value in {
                "id": source.get("id"),
                "type": "database",
                "name": source.get("name"),
                "mode": source.get("mode"),
                "hasPassword": bool(source.get("passwordCiphertext")),
            }.items()
            if value is not None
        }
    directories = []
    for directory in source.get("directories", []) or []:
        operations = []
        for operation in directory.get("operations", []) or []:
            operations.append(
                {
                    "id": operation.get("id"),
                    "name": operation.get("name"),
                    "method": operation.get("method"),
                    "path": operation.get("path"),
                }
            )
        directories.append(
            {
                "id": directory.get("id"),
                "name": directory.get("name"),
                "operations": operations,
            }
        )
    return {
        "id": source.get("id"),
        "type": "external_api",
        "name": source.get("name"),
        "baseUrl": source.get("baseUrl"),
        "directories": directories,
    }


def _assemble_external_source(
    workspace_root: str | Path,
    source: dict[str, Any],
    *,
    summary_source: dict[str, Any] | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """根据域名文件中的接口 ID 读取全部或指定接口详情。"""

    assembled = dict(source)
    directories = []
    base = data_sources_directory(workspace_root) / "external-apis" / _require_id(source.get("id"), "外部 API") / "operations"
    summary_directories = {
        str(directory.get("id")): directory
        for directory in (summary_source or {}).get("directories") or []
        if isinstance(directory, dict)
    }
    operation_found = operation_id is None
    for directory in source.get("directories") or []:
        directory_item = {key: value for key, value in directory.items() if key != "operationIds"}
        operations = []
        for referenced_operation_id in directory.get("operationIds") or []:
            safe_operation_id = _require_id(referenced_operation_id, "接口")
            if operation_id is not None and safe_operation_id != operation_id:
                summary_directory = summary_directories.get(str(directory.get("id")), {})
                operation = next(
                    (
                        item
                        for item in summary_directory.get("operations") or []
                        if str(item.get("id")) == safe_operation_id
                    ),
                    None,
                )
                if not isinstance(operation, dict):
                    raise DataSourceStorageError("索引缺少接口摘要。")
            else:
                operation = _read_json(base / f"{safe_operation_id}.json", "接口")
                operation_found = operation_found or safe_operation_id == operation_id
            if operation.get("id") != safe_operation_id:
                raise DataSourceStorageError("接口文件与目录引用不一致。")
            operations.append(operation)
        directory_item["operations"] = operations
        directories.append(directory_item)
    assembled["directories"] = directories
    if not operation_found:
        raise DataSourceStorageError("目标接口不存在。")
    return assembled


def _managed_paths(directory: Path) -> set[Path]:
    """收集当前独立目录下的文件并拒绝符号链接。"""

    if not directory.exists():
        return set()
    paths: set[Path] = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise DataSourceStorageError("独立数据源目录不可使用符号链接。")
        if path.is_file():
            paths.add(path)
    return paths


def _read_json(path: Path, label: str) -> dict[str, Any]:
    """读取一个受控 JSON 对象文件并转换为明确的存储错误。"""

    if not path.is_file() or path.is_symlink():
        raise DataSourceStorageError(f"{label}文件缺失或不可用。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataSourceStorageError(f"{label}文件损坏或格式无效。") from exc
    if not isinstance(value, dict):
        raise DataSourceStorageError(f"{label}文件必须是 JSON 对象。")
    return value


def _require_id(value: Any, label: str) -> str:
    """校验可安全用于文件名和引用的稳定 ID。"""

    identifier = str(value or "")
    if not _SAFE_ID.fullmatch(identifier):
        raise DataSourceStorageError(f"{label} ID 只能包含字母、数字、点、下划线和短横线。")
    return identifier


def _encode(value: Any) -> bytes:
    """使用稳定格式编码存储 JSON。"""

    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    """在目标目录中原子替换单个文件。"""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _restore_paths(paths: set[Path], snapshots: dict[Path, bytes], directory: Path) -> None:
    """恢复多文件写入失败前的文件集合。"""

    for path in paths:
        if path in snapshots:
            _write_bytes_atomically(path, snapshots[path])
        elif path.exists() and path.is_file():
            path.unlink()
    _remove_empty_directories(directory)


def _remove_empty_directories(directory: Path) -> None:
    """删除拆分存储留下的空子目录，保留 datasource 根目录。"""

    for path in sorted(directory.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and path != directory:
            try:
                path.rmdir()
            except OSError:
                pass
