"""EntitySourceBinding 外置文件持久化。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workflow_artifact_root


def hydrate_external_detail_designs(
    project_plan_path: str | Path,
    project_plan: dict[str, Any],
) -> dict[str, Any]:
    """从实体 source_binding 引用加载当前 EntitySourceBinding 产物。"""

    plan_path = Path(project_plan_path).expanduser()
    hydrated = deepcopy(project_plan)
    bindings: list[dict[str, Any]] = []
    for entity in _dict_items(hydrated.get("entities")):
        entity_id = str(entity.get("id") or "").strip()
        if not entity_id:
            continue
        reference = entity.get("source_binding")
        reference = reference if isinstance(reference, dict) else {}
        binding_path = _resolve_binding_path(
            plan_path,
            reference,
            fallback_stem=_safe_file_stem(entity_id, prefix="entity--"),
        )
        binding = _read_json_object(binding_path) if binding_path else None
        if isinstance(binding, dict):
            bindings.append(binding)
    if bindings:
        hydrated["entity_detail_plans"] = bindings
    return hydrated


def externalize_detail_designs(
    state: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """写出 EntitySourceBinding，并返回只保留绑定引用的 TechnicalPlan。"""

    from app.workspace.plan_documents import render_entity_detail_markdown

    compact_plan = deepcopy(plan)
    bindings = _dict_items(plan.get("entity_detail_plans"))
    if bindings:
        directory = workflow_artifact_root(state) / "plans" / "entities"
        directory.mkdir(parents=True, exist_ok=True)
        for binding in bindings:
            entity_id = str(binding.get("entity_id") or "").strip()
            if not entity_id:
                continue
            stem = _safe_file_stem(entity_id, prefix="entity--")
            json_path = directory / f"{stem}.json"
            markdown_path = directory / f"{stem}.md"
            sha256 = _write_json_atomically(json_path, binding)
            _write_markdown_atomically(
                markdown_path,
                render_entity_detail_markdown(binding),
            )
            reference = {
                "status": str(binding.get("status") or "pending_user_confirmation"),
                "json_path": _workspace_relative_path(state, json_path),
                "markdown_path": _workspace_relative_path(state, markdown_path),
                "sha256": sha256,
                "confirmed_at": binding.get("confirmed_at"),
            }
            compact_plan["entities"] = [
                {
                    **entity,
                    "source_binding": reference,
                    "source_binding_status": reference["status"],
                }
                if isinstance(entity, dict)
                and str(entity.get("id") or "") == entity_id
                else entity
                for entity in _dict_items(compact_plan.get("entities"))
            ]
    compact_plan.pop("entity_detail_plans", None)
    return compact_plan


def write_compact_project_plan(
    state: dict[str, Any],
    path: Path,
    plan: dict[str, Any],
) -> None:
    """原子写入仅内嵌 TechnicalPlan 与实体绑定引用的当前计划。"""

    payload = externalize_detail_designs(state, plan)
    content = f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _resolve_binding_path(
    project_plan_path: Path,
    reference: dict[str, Any],
    *,
    fallback_stem: str,
) -> Path | None:
    """按正式 source_binding 引用定位实体绑定 JSON。"""

    raw_path = str(reference.get("json_path") or "").strip()
    candidates: list[Path] = []
    if raw_path:
        indexed = Path(raw_path).expanduser()
        if indexed.is_absolute():
            candidates.append(indexed)
        else:
            workspace_root = project_plan_path.parent.parent.parent
            candidates.extend([workspace_root / indexed, project_plan_path.parent / indexed])
    candidates.append(project_plan_path.parent / "entities" / f"{fallback_stem}.json")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """读取实体绑定 JSON，非法内容按缺失处理。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> str:
    """原子写入实体绑定 JSON 并返回内容哈希。"""

    content = f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_markdown_atomically(path: Path, content: str) -> None:
    """原子写入实体绑定 Markdown。"""

    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _workspace_relative_path(state: dict[str, Any], path: Path) -> str:
    """生成工作区相对路径，供 TechnicalPlan 建立稳定引用。"""

    workspace = Path(str(state.get("workspace") or "")).expanduser()
    if workspace.is_dir():
        try:
            return str(path.relative_to(workspace.resolve()))
        except ValueError:
            pass
    return str(path)


def _safe_file_stem(value: Any, *, prefix: str) -> str:
    """把实体 id 转换为安全文件名。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "")).strip("-_")
    return f"{prefix}{normalized or 'unknown'}"


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """过滤列表中的非字典输入。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
