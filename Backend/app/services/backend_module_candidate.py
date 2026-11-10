"""把模型的精简文件计划绑定到平台拥有的后端模块任务和交付物。"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from app.services.unit_generation_contracts import UnitGenerationContext
from app.services.unit_task_manifest import BACKEND_STAGE_LABELS, backend_stage_manifest


def _required_text(value: Any, field: str) -> str:
    """要求模型提供精确非空文本，保留原始路径或说明。"""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field} 必须是非空字符串。")
    return value


def _file_path(value: Any, stage: str, sources: list[str]) -> str:
    """限制模块阶段可计划的文件类型和根目录。"""
    path = _required_text(value, "path")
    parts = path.split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or any(char in path for char in "\\:*?[]")
        or not path.startswith(("backend/src/main/java/", "backend/src/main/resources/"))
    ):
        raise ValueError(f"文件 {path} 必须是 backend/src/main 下的精确相对路径。")
    suffix = PurePosixPath(path).suffix
    if path.startswith((
        "backend/src/main/java/com/cmbchina/backend/common/",
        "backend/src/main/java/com/cmbchina/backend/auth/",
        "backend/src/main/resources/mapper/auth/",
    )) or path.endswith("/Application.java"):
        raise ValueError(f"文件 {path} 属于模板公共基础设施。")
    if stage == "controller" and (suffix != ".java" or not path.startswith("backend/src/main/java/")):
        raise ValueError("Controller 任务只允许 Java 路由文件。")
    if suffix != ".java":
        client_config = (
            stage == "foundation" and "external_api" in sources
            and suffix in {".yml", ".yaml", ".properties"} and path.startswith("backend/src/main/resources/")
        )
        mapper_xml = (
            stage == "business" and "database" in sources
            and suffix == ".xml" and path.startswith("backend/src/main/resources/mapper/")
        )
        if not client_config and not mapper_xml:
            raise ValueError(f"{stage} 阶段不能计划文件 {path}。")
    return path


def _materialize_task(context: UnitGenerationContext, record: dict, draft: dict, prior_paths: set[str]) -> dict:
    """编译一个阶段的文件变更、交付物与固定任务字段。"""
    stage = record["stage"]
    label = BACKEND_STAGE_LABELS[stage]
    name = _required_text(draft.get("title"), f"{record['key']}.title")
    title = name if name.startswith(label) else f"{label} · {name}"
    files = draft.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError(f"{record['key']} 缺少文件计划。")
    changes: list[dict] = []
    path_keys: set[str] = set()
    specifications = record["deliverables"]
    coverage: dict[int, list[str]] = {index: [] for index in range(1, len(specifications) + 1)}
    for file in files:
        if not isinstance(file, dict) or set(file) != {"path", "operation", "description", "covers"}:
            raise ValueError("files 的每项必须包含 path、operation、description、covers。")
        path = _file_path(file["path"], stage, record["data_source_types"])
        if path.casefold() in path_keys:
            raise ValueError(f"{record['key']} 重复计划文件 {path}。")
        path_keys.add(path.casefold())
        if file["operation"] not in ("add", "modify"):
            raise ValueError(f"{path} 的 operation 必须是 add 或 modify。")
        description = _required_text(file["description"], "files.description")
        # 保留模型按职责、方法及约束生成的换行，供确认卡直接分行展示。
        covers = file["covers"]
        if (
            not isinstance(covers, list) or not covers
            or any(type(index) is not int or index not in coverage for index in covers)
            or len(covers) != len(set(covers))
        ):
            raise ValueError(f"{path} 的 covers 必须列出该阶段的有效职责序号。")
        for index in covers:
            coverage[index].append(path)
        # 同一模块前序任务可能先创建该文件。后续阶段只计划增量修改。
        operation = "modify" if path.casefold() in prior_paths else file["operation"]
        changes.append({"path": path, "operation": operation, "description": description})
    prior_paths.update(path_keys)
    if missing := [index for index, paths in coverage.items() if not paths]:
        raise ValueError(f"{record['key']} 的职责 {missing} 未分配文件。")

    task_id = record["task_id"]
    paths = [change["path"] for change in changes]
    return {
        "id": task_id, "unit_id": context.unit_id, "owner": "backend", "task_type": "backend.code",
        "title": title, "description": "\n".join(
            f"{index}. {item['description']}（{item['path']}）"
            for index, item in enumerate(changes, start=1)
        ),
        "dependencies": [],
        "target_files": paths, "allowed_paths": list(paths), "change_scope": changes,
        "deliverables": [
            {**spec, "id": f"{task_id}::deliverable:{index}", "paths": coverage[index]}
            for index, spec in enumerate(specifications, start=1)
        ],
        "impact_scope": {
            "summary": title, "affected_modules": [record["api_contract_id"]],
            "public_contracts": record["endpoint_ids"] if stage == "controller" else [],
            "risks": [],
        },
        "can_run_in_parallel": True,
        "parallel_reason": "平台按模块和实际文件锁安排批次。",
        "status": "pending",
    }


def materialize_backend_candidate(context: UnitGenerationContext, drafts: list[Any]) -> list[dict]:
    """完整匹配阶段清单后才产出标准 Candidate，拒绝多余或缺失的阶段。"""
    manifest = backend_stage_manifest(context)
    by_key: dict[str, dict] = {}
    allowed_keys = {record["key"] for record in manifest}
    for draft in drafts:
        if not isinstance(draft, dict) or set(draft) != {"key", "title", "files"}:
            raise ValueError("后端模块的每个任务必须且只能包含 key、title、files。")
        key = _required_text(draft["key"], "key")
        if key not in allowed_keys or key in by_key:
            raise ValueError(f"后端模块任务 key {key} 未被清单授权或重复。")
        by_key[key] = draft
    if missing := allowed_keys - by_key.keys():
        raise ValueError(f"后端模块候选缺少任务：{', '.join(sorted(missing))}。")
    prior_paths: set[str] = set()
    return [_materialize_task(context, record, by_key[record["key"]], prior_paths) for record in manifest]
