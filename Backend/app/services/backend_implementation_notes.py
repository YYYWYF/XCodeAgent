"""以真实 Java 符号验证辅助说明，原子合并模块 Markdown 并按需读取。"""

from __future__ import annotations

from hashlib import sha256
import base64
import json
from pathlib import Path
import threading
from urllib.parse import quote
from uuid import uuid4

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.api_design_fingerprint import api_design_content
from app.services.backend_note_symbols import symbol_evidence
from app.services.unit_task_manifest import BACKEND_MODULE_PREFIX
from app.workspace.json_documents import write_json_atomic

_LOCK = threading.RLock()
_MARKER = "<!-- implementation-notes:"


def _digest(value: str) -> str:
    """生成辅助内容和源码符号的稳定摘要。"""
    return sha256(value.encode("utf-8")).hexdigest()


def _path(root: Path, relative: str) -> Path:
    """限制平台说明读写在当前工作区内，拒绝路径穿越和符号链接逃逸。"""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("实现说明路径超出工作区。")
    return path


def module_notes_path(contract: str) -> str:
    """编码模块身份，形成 Windows 下也安全且无冲突的文档名。"""
    slug = quote(contract, safe="")[:120]
    return f"backend/docs/implementation/module-{slug}-{_digest(contract)[:12]}.md"


def _locator(value: dict) -> dict:
    """裁剪模型符号定位字段，禁止任意结构随说明持久化。"""
    if not isinstance(value, dict):
        raise ValueError("实现说明的符号定位必须为对象。")
    path, type_name = value.get("path"), value.get("type_name")
    method, parameters = value.get("method", ""), value.get("parameter_types", [])
    if (
        not isinstance(path, str) or not isinstance(type_name, str) or not type_name
        or not isinstance(method, str) or not isinstance(parameters, list)
        or any(not isinstance(item, str) or not item for item in parameters)
        or (not method and parameters)
    ):
        raise ValueError("实现说明缺少精确类型或方法签名。")
    return {"path": path, "type_name": type_name, "method": method, "parameter_types": parameters}


def _signature(locator: dict) -> str:
    """显示类型全名和方法重载参数。"""
    signature = locator["type_name"]
    if locator["method"]:
        signature += f".{locator['method']}(" + ", ".join(locator["parameter_types"]) + ")"
    return signature


def _formal_digest(design: dict) -> str:
    """绑定说明使用的正式接口正文，重新确认时间不会单独让说明失效。"""
    return _digest(json.dumps(api_design_content(design), ensure_ascii=False, sort_keys=True))


def _render(contract: str, entries: list[dict]) -> str:
    """按基础、共享能力、接口业务和 Controller 分区渲染人可读说明。"""
    lines = [f"# {contract} 模块实现说明", "", "本文件是源码导航；正式契约与真实源码优先。", ""]
    for stage, title in (("foundation", "模块基础骨架"), ("business", "接口业务与共享能力"), ("controller", "Controller")):
        lines.extend([f"## {title}", ""])
        records = [item for item in entries if item["stage"] == stage]
        if not records:
            lines.extend(["尚无已核验记录。", ""])
        by_endpoint: dict[str, list[dict]] = {}
        for record in records:
            by_endpoint.setdefault(record["endpoint_id"], []).append(record)
        for endpoint, methods in by_endpoint.items():
            lines.extend([f"### {endpoint} · {methods[0]['http_identity']}", ""])
            for entry in methods:
                locator = entry["symbol"]
                signature = _signature(locator)
                if entry.get("return_type"):
                    signature += f" → {entry['return_type']}"
                lines.extend([
                    f"#### `{signature}`", "", f"源码：`{locator['path']}`", "", entry["summary"], "",
                    f"来源任务：`{entry['task_id']}`", "",
                ])
                for dependency in entry.get("dependencies", []):
                    ref = dependency["symbol"]
                    lines.append(f"- 依赖：`{ref['path']}` · `{_signature(ref)}`")
                lines.append("")
    body = "\n".join(lines)
    payload = {"contract": contract, "entries": entries, "body_sha256": _digest(body)}
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    return body + _MARKER + encoded + " -->\n"


def _read(root: Path, contract: str) -> list[dict]:
    """读取未被手动改写的说明；内容摘要不符时禁止作为可信上下文。"""
    path = _path(root, module_notes_path(contract))
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    body, marker = text.rsplit(_MARKER, 1)
    if not marker.endswith(" -->\n"):
        raise ValueError("实现说明的受管内容已被手动修改。")
    payload = json.loads(base64.b64decode(marker.removesuffix(" -->\n"), validate=True))
    if payload["contract"] != contract or payload["body_sha256"] != _digest(body):
        raise ValueError("实现说明已被手动修改，请以源码为准。")
    return payload["entries"]


def _fresh(root: Path, entry: dict, cache: dict) -> bool:
    """同时检查主符号及直接依赖，源码变化后停用该说明。"""
    try:
        return all(symbol_evidence(root, item["symbol"], cache)["source_sha256"] == item["source_sha256"]
                   for item in [entry, *entry.get("dependencies", [])])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return False


def read_implementation_context(workspace: str | None, task: dict) -> list[dict]:
    """只向当前任务注入相关接口和有效共享能力摘要，读取失败时由执行器回读源码。"""
    if not workspace or not str(task.get("unit_id", "")).startswith(BACKEND_MODULE_PREFIX):
        return []
    contract = task["unit_id"].removeprefix(BACKEND_MODULE_PREFIX)
    endpoints = set((task.get("source_refs") or {}).get("endpoint_ids", []))
    refs = task.get("source_refs") or {}
    relevant_paths = set(task.get("target_files", [])) | set(refs.get("foundation_paths", []))
    try:
        root = Path(workspace).resolve()
        formal = {item["endpointId"]: _formal_digest(item) for item in refs.get("endpoint_designs", [])}
        cache: dict = {}
        context = []
        for item in _read(root, contract):
            if item["endpoint_id"] not in endpoints and item["symbol"]["path"] not in relevant_paths:
                continue
            digest = formal.get(item["endpoint_id"])
            if digest and item["stage"] != "foundation" and digest != item.get("formal_sha256"):
                continue
            if _fresh(root, item, cache):
                context.append({
                    "endpoint_id": item["endpoint_id"], "symbol": item["symbol"],
                    "summary": item["summary"], "dependencies": [dep["symbol"] for dep in item["dependencies"]],
                })
        return context
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return []


def _write_atomic(path: Path, text: str) -> None:
    """通过同目录临时文件原子替换说明，避免并发读取半份文档。"""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _pending_path(root: Path, contract: str) -> Path:
    """将待合并的验证记录保存在工作区内部运行目录。"""
    filename = Path(module_notes_path(contract)).with_suffix(".json").name
    return _path(root, (WORKSPACE_ARTIFACT_DIR / "runtime" / "implementation-notes" / filename).as_posix())


def _pending_entries(path: Path) -> list[dict]:
    """读取已经过源码验证的待合并记录。"""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _merge_records(*groups: list[dict]) -> list[dict]:
    """按接口与精确符号合并，多次写入同一事实保持幂等。"""
    entries = {(item["endpoint_id"], item["key"]): item for group in groups for item in group}
    return [entries[key] for key in sorted(entries)]


def _validated_entries(root: Path, task: dict, notes: list, cache: dict) -> list[dict]:
    """检查主符号归属、精确签名与直接依赖，关联已确认接口和源任务。"""
    refs = task["source_refs"]
    endpoints = {design["endpointId"]: design for design in refs["endpoint_designs"]}
    paths = set(task.get("target_files", []))
    entries = []
    for note in notes:
        if not isinstance(note, dict):
            raise ValueError("实现说明必须为对象。")
        endpoint = note.get("endpoint_id")
        summary = note.get("summary")
        if not isinstance(endpoint, str) or endpoint not in endpoints or not isinstance(summary, str) or not summary.strip():
            raise ValueError("实现说明缺少当前接口身份或用途。")
        locator = _locator(note)
        if locator["path"] not in paths:
            raise ValueError("主符号不属于任务目标文件。")
        if refs["backend_stage"] == "foundation" and locator["method"]:
            raise ValueError("基础骨架说明只登记类型和注入结构。")
        dependencies = []
        for item in note.get("dependencies", []):
            dependency = _locator(item)
            dependencies.append({"symbol": dependency, **symbol_evidence(root, dependency, cache)})
        design = endpoints[endpoint]
        expected = design.get("endpointContract") or {}
        entries.append({
            "key": json.dumps(locator, ensure_ascii=False, sort_keys=True),
            "endpoint_id": endpoint, "http_identity": f"{expected.get('method', '')} {expected.get('path', '')}".strip(),
            "stage": refs["backend_stage"], "task_id": task["id"], "symbol": locator,
            "formal_sha256": _formal_digest(design),
            **symbol_evidence(root, locator, cache), "dependencies": dependencies, "summary": summary.strip()[:1600],
        })
    return entries


def _publish_pending(root: Path, contract: str, pending: Path, cache: dict) -> bool:
    """写入前复验缓存记录的源码，文档已被手动编辑时保留用户内容。"""
    pending_entries = [entry for entry in _pending_entries(pending) if _fresh(root, entry, cache)]
    entries = _merge_records(_read(root, contract), pending_entries)
    verified = [entry for entry in entries if _fresh(root, entry, cache)]
    relative = module_notes_path(contract)
    if verified:
        _write_atomic(_path(root, relative), _render(contract, verified))
    pending.unlink(missing_ok=True)
    return bool(pending_entries)


def retry_implementation_document(workspace: str, contract: str) -> dict | None:
    """复用前次验证记录重试文档合并，不重新调用模型或执行代码。"""
    root = Path(workspace).resolve()
    relative = module_notes_path(contract)
    try:
        with _LOCK:
            pending = _pending_path(root, contract)
            if not pending.exists():
                return None
            if not _publish_pending(root, contract, pending, {}):
                return {"status": "unavailable", "path": relative, "summary": "待合并说明对应的源码已变化。"}
        return {"status": "updated", "path": relative, "summary": "模块实现说明已同步。"}
    except Exception as exc:
        return {"status": "unavailable", "path": relative, "summary": f"实现说明仍未更新：{exc}。"}


def merge_implementation_notes(workspace: str, task: dict, result: dict) -> dict:
    """成功任务的说明先验证再持久化，写入失败不改变代码结果。"""
    contract = task["unit_id"].removeprefix(BACKEND_MODULE_PREFIX)
    root = Path(workspace).resolve()
    relative = module_notes_path(contract)
    try:
        with _LOCK:
            notes = result.get("implementation_notes")
            if not isinstance(notes, list) or not notes:
                raise ValueError("任务未提供可核验的实现说明")
            cache: dict = {}
            entries = _validated_entries(root, task, notes, cache)
            pending = _pending_path(root, contract)
            write_json_atomic(pending, _merge_records(_pending_entries(pending), entries))
            _publish_pending(root, contract, pending, cache)
        return {"status": "updated", "path": relative, "summary": "模块实现说明已与真实源码同步。"}
    except Exception as exc:
        return {"status": "unavailable", "path": relative, "summary": f"实现说明未更新：{exc}。后续任务将读取源码。"}
