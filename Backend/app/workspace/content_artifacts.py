"""工作区内按 SHA-256 保存不可变 JSON 资料；引用不授予任何执行权限。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR

MATERIAL_FIELDS = frozenset({
    "source_refs", "endpoint_designs", "mapping_flows", "business_descriptions",
    "acceptance_checks", "engineering_acceptance_checks", "business_acceptance_checks",
    "build_context", "workspace_snapshot", "diff", "code_diff",
})
MATERIAL_INLINE_BYTES = 4096


def artifact_root(workspace: str | Path) -> Path:
    """引用只能解析到当前工作区的固定资料目录，不能提交任意路径。"""
    return Path(workspace).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR / "artifacts" / "sha256"


def artifact_bytes(value: Any) -> bytes:
    """使用确定的 UTF-8 JSON 字节计算内容地址，不把运行时间写入资料身份。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def put_artifact(workspace: str | Path, value: Any) -> dict[str, Any]:
    """先原子发布不可变内容，再返回 hash 引用；并发写同一 hash 不覆盖现有文件。"""
    content = artifact_bytes(value)
    digest = hashlib.sha256(content).hexdigest()
    root = artifact_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{digest}.json"
    if target.exists():
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("内容寻址资料已损坏，不能覆盖。")
    else:
        descriptor, name = tempfile.mkstemp(prefix=".artifact-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(name, target)
            except FileExistsError:
                if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ValueError("并发发布的内容寻址资料无效。")
        finally:
            Path(name).unlink(missing_ok=True)
    return {"artifactId": digest, "byteLength": len(content), "mediaType": "application/json"}


def read_artifact(workspace: str | Path, reference: dict[str, Any], *, max_bytes: int | None = None) -> Any:
    """核对 hash、字节数与读量上限，缺失或损坏时显式失败。"""
    digest = reference.get("artifactId")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("资料引用的 hash 无效。")
    target = artifact_root(workspace) / f"{digest}.json"
    if target.is_symlink() or not target.is_file():
        raise ValueError("引用的资料不存在或不是普通文件。")
    size = target.stat().st_size
    if max_bytes is not None and size > max_bytes:
        raise ValueError("所选资料超出单次读取预算。")
    if reference.get("byteLength") != size:
        raise ValueError("引用资料的大小校验失败。")
    with target.open("rb") as stream:
        content = stream.read(size + 1)
    if len(content) != size:
        raise ValueError("引用资料在读取期间发生变化。")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("引用资料的内容校验失败。")
    return json.loads(content)


def pack_materials(workspace: str | Path, value: Any) -> Any:
    """递归提取重复执行材料；相同对象只处理一次，正文不在每个 Task 重复序列化。"""
    memo: dict[tuple[int, bool], Any] = {}

    def visit(item: Any, field: str = "") -> Any:
        """只外置明确的 JSON 材料字段，其余 Graph 对象交给原 checkpoint 序列化器。"""
        if not isinstance(item, (dict, list, tuple)):
            if field in MATERIAL_FIELDS and isinstance(item, str) and len(item.encode("utf-8")) >= MATERIAL_INLINE_BYTES:
                return {"$artifact": put_artifact(workspace, item)}
            return item
        identity = (id(item), field in MATERIAL_FIELDS)
        if identity in memo:
            return memo[identity]
        if isinstance(item, dict) and set(item) == {"$artifact"}:
            return item
        transformed = ({key: visit(child, key) for key, child in item.items()} if isinstance(item, dict)
                       else [visit(child) for child in item])
        if field in MATERIAL_FIELDS and len(artifact_bytes(item)) >= MATERIAL_INLINE_BYTES:
            transformed = {"$artifact": put_artifact(workspace, transformed)}
        elif isinstance(item, tuple):
            transformed = tuple(transformed)
        memo[identity] = transformed
        return transformed

    return visit(value)


def expand_materials(workspace: str | Path, value: Any) -> Any:
    """仅在后端业务读取时解析资料，同一 hash 在一次读取中复用同一对象。"""
    resolved: dict[str, Any] = {}
    active: set[str] = set()

    def visit(item: Any) -> Any:
        """校验并展开当前引用树，拒绝循环或丢失引用，不读取旧存储版本。"""
        if isinstance(item, dict) and set(item) == {"$artifact"}:
            reference = item["$artifact"]
            if not isinstance(reference, dict):
                raise ValueError("资料引用必须为对象。")
            digest = str(reference.get("artifactId") or "")
            if digest in active:
                raise ValueError("资料引用形成循环。")
            if digest not in resolved:
                active.add(digest)
                resolved[digest] = visit(read_artifact(workspace, reference))
                active.remove(digest)
            return resolved[digest]
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            result = [visit(child) for child in item]
            return tuple(result) if isinstance(item, tuple) else result
        return item

    return visit(value)
