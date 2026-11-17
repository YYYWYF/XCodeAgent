"""解析 DataSource Agent 的阶段、来源和必读 Skill 路由。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.services.builtin_skills import (
    BUILTIN_SKILLS_VIRTUAL_ROOT,
    SPRINGBOOT_BACKEND_BOOTSTRAP_SKILL_NAME,
    SPRINGBOOT_BACKEND_BUSINESS_SKILL_NAME,
    SPRINGBOOT_BACKEND_CONTROLLER_SKILL_NAME,
    SPRINGBOOT_BACKEND_FOUNDATION_SKILL_NAME,
)
from app.services.unit_task_manifest import BACKEND_STAGE_LABELS


_BOOTSTRAP_UNIT_ID = "backend:bootstrap"
_SOURCE_ORDER = ("database", "external_api")
_STAGE_SKILL_NAMES = {
    "bootstrap": SPRINGBOOT_BACKEND_BOOTSTRAP_SKILL_NAME,
    "foundation": SPRINGBOOT_BACKEND_FOUNDATION_SKILL_NAME,
    "business": SPRINGBOOT_BACKEND_BUSINESS_SKILL_NAME,
    "controller": SPRINGBOOT_BACKEND_CONTROLLER_SKILL_NAME,
}


@dataclass(frozen=True)
class DataSourceExecutionConfig:
    """表示单个任务唯一确定的执行阶段与必读 Skill 集合。"""

    task_id: str
    kind: str
    stage: str
    source_types: tuple[str, ...]
    skill_name: str
    entry_path: str
    instruction_paths: tuple[str, ...]


def is_bootstrap_task(task: dict[str, Any]) -> bool:
    """按稳定 Unit 身份判断 Bootstrap，避免依赖任务标题或模型输出。"""

    unit_id = str(task.get("unit_id") or "").strip()
    return unit_id == _BOOTSTRAP_UNIT_ID or unit_id.startswith(f"{_BOOTSTRAP_UNIT_ID}::")


def task_endpoint_designs(task: dict[str, Any]) -> list[dict[str, Any]]:
    """读取单个任务由平台裁剪并携带的已确认 Endpoint API 设计快照。"""

    source_refs = task.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    designs = source_refs.get("endpoint_designs")
    return (
        [dict(item) for item in designs if isinstance(item, dict)]
        if isinstance(designs, list)
        else []
    )


def task_data_source_types(tasks: list[dict[str, Any]]) -> set[str]:
    """从已确认 Endpoint 设计快照提取当前任务的物理来源集合。"""

    return {
        str(snapshot.get("sourceType") or "").strip()
        for task in tasks
        for design in task_endpoint_designs(task)
        for snapshot in _dict_items(design.get("sourceSnapshots"))
        if str(snapshot.get("sourceType") or "").strip()
    }


def task_execution_config(task: dict[str, Any]) -> DataSourceExecutionConfig:
    """按 Bootstrap 或模块阶段解析当前任务唯一可用的执行配置。"""

    stage = _task_stage(task)
    available_sources = task_data_source_types([task])
    source_types = tuple(
        source
        for source in _SOURCE_ORDER
        if source in available_sources
    )
    unsupported = available_sources - set(_SOURCE_ORDER)
    if unsupported:
        raise ValueError(
            f"DataSource 后端任务 {task.get('id') or '<unknown>'} 包含非法数据源类型："
            f"{', '.join(sorted(unsupported))}。"
        )

    skill_name = _STAGE_SKILL_NAMES[stage]
    skill_root = f"{BUILTIN_SKILLS_VIRTUAL_ROOT}{skill_name}/"
    entry_path = f"{skill_root}SKILL.md"
    references: list[str] = [entry_path]
    if stage == "bootstrap":
        references.extend(
            f"{skill_root}references/bootstrap-{source.replace('_', '-')}.md"
            for source in source_types
        )
    elif stage == "foundation":
        references.extend(
            f"{skill_root}references/foundation-{source.replace('_', '-')}.md"
            for source in source_types
        )
    elif stage == "business":
        references.extend(
            f"{skill_root}references/business-{source.replace('_', '-')}.md"
            for source in source_types
        )
        references.append(f"{skill_root}references/business-error-handling.md")
    else:
        # Controller 仅在已确认端点声明资源点时读取权限参考。
        source_refs = task.get("source_refs") or {}
        authorization = source_refs.get("authorization")
        if isinstance(authorization, dict) and any(
            endpoint.get("operationResourceKeys")
            for endpoint in _dict_items(authorization.get("endpoints"))
        ):
            references.append(
                f"{skill_root}references/controller-resource-authorization.md"
            )

    return DataSourceExecutionConfig(
        task_id=str(task.get("id") or ""),
        kind="bootstrap" if stage == "bootstrap" else "endpoint",
        stage=stage,
        source_types=source_types,
        skill_name=skill_name,
        entry_path=entry_path,
        instruction_paths=tuple(_dedupe(references)),
    )


def task_required_skill_paths(task: dict[str, Any]) -> list[str]:
    """返回当前任务阶段入口 Skill 的虚拟路径。"""

    return [task_execution_config(task).entry_path]


def task_required_instruction_paths(task: dict[str, Any]) -> list[str]:
    """按固定顺序返回阶段入口、来源参考和阶段公共参考。"""

    return list(task_execution_config(task).instruction_paths)


def batch_required_builtin_skill_names(
    tasks: list[dict[str, Any]],
) -> tuple[str, ...]:
    """按任务顺序去重当前批次需要挂载的阶段 Skill 目录名。"""

    return tuple(
        _dedupe(task_execution_config(task).skill_name for task in tasks)
    )


def _task_stage(task: dict[str, Any]) -> str:
    """读取 Bootstrap 稳定身份或其他任务的平台编译阶段。"""

    if is_bootstrap_task(task):
        return "bootstrap"
    source_refs = task.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    stage = source_refs.get("backend_stage")
    if stage not in BACKEND_STAGE_LABELS:
        raise ValueError(f"后端模块任务 {task.get('id', '<unknown>')} 缺少有效阶段。")
    return str(stage)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """把不可信列表规整为字典列表。"""

    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    """按首次出现顺序去重非空路径或名称。"""

    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result
