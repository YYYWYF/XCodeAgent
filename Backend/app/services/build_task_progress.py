"""构建任务 DAG 生成阶段的紧凑进度投影。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from langgraph.config import get_stream_writer

from app.services.build_task_planner import tasks_from_build_task_plan


ProgressWriter = Callable[[dict[str, Any]], None]

MAX_STAGE_RECORDS = 200
MAX_STAGE_EDGES = 500

DAG_GENERATION_STAGES: tuple[tuple[str, str], ...] = (
    ("unit_skeleton", "生成 Unit DAG 骨架"),
    ("build_context", "解析目标构建上下文"),
    ("contract_validation", "校验页面依赖与 API 契约"),
    ("model_planning", "生成候选构建任务"),
    ("task_compilation", "编译任务注册表与依赖"),
    ("dag_validation", "校验任务 DAG"),
    ("artifact_persistence", "保存 DAG 产物"),
)


class BuildTaskProgressTracker:
    """维护完整阶段快照，并把瞬态更新写入 LangGraph custom stream。"""

    def __init__(self, writer: ProgressWriter | None = None) -> None:
        """初始化固定顺序的阶段列表，直接单测时允许无 writer 运行。"""

        self._writer = writer
        self._stages = [
            {"id": stage_id, "name": name, "status": "pending", "detail": ""}
            for stage_id, name in DAG_GENERATION_STAGES
        ]
        self._build_task_plan: dict[str, Any] = {}
        self._artifacts: list[dict[str, Any]] = []
        self._stage_outputs: dict[str, dict[str, Any]] = {}

    def start(self, stage_id: str, detail: str) -> None:
        """将指定阶段标记为运行中并立即发送完整快照。"""

        self._update_stage(stage_id, status="running", detail=detail)
        self._emit()

    def complete(
        self,
        stage_id: str,
        detail: str,
        *,
        build_task_plan: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        output: dict[str, Any] | None = None,
    ) -> None:
        """完成指定阶段，并冻结该阶段的结构化产物投影。"""

        if isinstance(build_task_plan, dict):
            self._build_task_plan = deepcopy(build_task_plan)
        if artifacts is not None:
            self._artifacts = deepcopy(artifacts)
        if isinstance(output, dict):
            self._stage_outputs[stage_id] = deepcopy(output)
        self._update_stage(stage_id, status="completed", detail=detail)
        self._emit()

    def fail(
        self,
        stage_id: str,
        detail: str,
        *,
        build_task_plan: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
    ) -> None:
        """把当前阶段标记为失败，同时保留阶段产物和完整任务注册表。"""

        if isinstance(build_task_plan, dict):
            self._build_task_plan = deepcopy(build_task_plan)
        if isinstance(output, dict):
            self._stage_outputs[stage_id] = deepcopy(output)
        self._update_stage(stage_id, status="failed", detail=detail)
        self._emit()

    def snapshot(self) -> dict[str, Any]:
        """返回可持久化和发送给前端的安全紧凑快照。"""

        tasks = _project_tasks(self._build_task_plan)
        stages = []
        for stage in self._stages:
            projected_stage = deepcopy(stage)
            output = self._stage_outputs.get(str(stage["id"]))
            if output is not None:
                projected_stage["output"] = deepcopy(output)
            stages.append(projected_stage)
        return {
            "stages": stages,
            "tasks": tasks,
            "summary": _project_summary(self._build_task_plan, tasks),
            "artifacts": deepcopy(self._artifacts),
            "confirmationStatus": _compact_text(
                self._build_task_plan.get("confirmation_status"), 40
            )
            or None,
            "scope": deepcopy(self._build_task_plan.get("build_execution_scope") or {}),
        }

    def _update_stage(self, stage_id: str, *, status: str, detail: str) -> None:
        """更新一个已声明阶段，未知标识视为编程错误。"""

        stage = next((item for item in self._stages if item["id"] == stage_id), None)
        if stage is None:
            raise ValueError(f"Unknown DAG generation stage: {stage_id}")
        stage["status"] = status
        stage["detail"] = _compact_text(detail, 1_000)

    def _emit(self) -> None:
        """通过 custom stream 发送完整快照，避免前端自行拼接部分状态。"""

        if self._writer is None:
            return
        running_stage = next(
            (stage for stage in self._stages if stage["status"] == "running"),
            None,
        )
        failed_stage = next(
            (stage for stage in self._stages if stage["status"] == "failed"),
            None,
        )
        active_stage = failed_stage or running_stage
        self._writer(
            {
                "type": "prepare_build_tasks.progress",
                "node_name": "prepare_build_tasks",
                "status": "running",
                "message": (
                    str(active_stage.get("detail") or active_stage.get("name"))
                    if active_stage
                    else "构建任务 DAG 进度已更新。"
                ),
                "dag_generation": self.snapshot(),
            }
        )


def create_build_task_progress_tracker() -> BuildTaskProgressTracker:
    """在 LangGraph 上下文中创建进度追踪器，直接调用节点时安全降级。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        writer = None
    return BuildTaskProgressTracker(writer)


def build_task_artifacts(build_task_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """构造 JSON DAG 产物摘要，隐藏内部文件路径并暴露确认状态。"""

    return [
        {
            "id": "build_task_plan",
            "name": "build-task-plan.json",
            "kind": "json",
            "status": "saved",
            "confirmationStatus": _compact_text(
                build_task_plan.get("confirmation_status"), 40
            )
            or "pending",
            "scope": deepcopy(build_task_plan.get("build_execution_scope") or {}),
        },
    ]


def _project_tasks(build_task_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """按任务图顺序裁剪前端展示所需字段，不暴露模型原文或内部状态。"""

    projected: list[dict[str, Any]] = []
    for task in tasks_from_build_task_plan(build_task_plan)[:MAX_STAGE_RECORDS]:
        task_id = _compact_text(task.get("id"), 240)
        if not task_id:
            continue
        dependencies = task.get("dependencies") or []
        acceptance = task.get("acceptance_criteria") or []
        projected.append(
            {
                "id": task_id,
                "title": _compact_text(
                    task.get("title") or task.get("description") or task_id,
                    500,
                ),
                "description": _compact_text(task.get("description"), 2_000),
                "owner": _compact_text(task.get("owner"), 80),
                "unitId": _compact_text(task.get("unit_id"), 240),
                "status": _task_status(task.get("status")),
                "dependencies": _compact_strings(dependencies, item_limit=200, text_limit=240),
                "changePaths": _change_paths(task),
                "allowedPaths": _compact_strings(
                    task.get("allowed_paths") or [],
                    item_limit=200,
                    text_limit=1_000,
                ),
                "changeScope": _project_change_scope(task.get("change_scope")),
                "acceptanceCriteria": _compact_strings(
                    acceptance,
                    item_limit=100,
                    text_limit=1_000,
                ),
                "acceptanceChecks": _project_acceptance_checks(task.get("acceptance_checks")),
            }
        )
    return projected


def project_unit_skeleton_output(build_task_plan: dict[str, Any]) -> dict[str, Any]:
    """投射 Unit 骨架、依赖边和校验结果，供步骤详情安全展示。"""

    units = build_task_plan.get("build_units")
    units = units if isinstance(units, dict) else {}
    unit_graph = build_task_plan.get("unit_graph")
    unit_graph = unit_graph if isinstance(unit_graph, dict) else {}
    unit_records = []
    for unit_id, unit in list(units.items())[:MAX_STAGE_RECORDS]:
        unit = unit if isinstance(unit, dict) else {}
        unit_records.append(
            {
                "id": _compact_text(unit_id, 240),
                "kind": _compact_text(unit.get("kind"), 80) or "unknown",
                "status": _compact_text(unit.get("status"), 80) or "not_prepared",
                "taskCount": len(unit.get("task_ids") or [])
                if isinstance(unit.get("task_ids"), list)
                else 0,
            }
        )
    validation = unit_graph.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    skeleton = build_task_plan.get("unit_skeleton")
    skeleton = skeleton if isinstance(skeleton, dict) else {}
    return {
        "kind": "unit_graph",
        "schemaVersion": _compact_text(unit_graph.get("schema_version"), 80),
        "reused": skeleton.get("reused") is True,
        "units": [item for item in unit_records if item["id"]],
        "edges": _project_edges(unit_graph.get("edges")),
        "validation": _project_validation(validation),
    }


def project_build_context_output(
    build_context: dict[str, Any],
    build_task_plan: dict[str, Any],
) -> dict[str, Any]:
    """投射构建目标及其关联 Unit、Endpoint、契约和数据源标识。"""

    target = build_context.get("target")
    target = target if isinstance(target, dict) else {}
    reusable = build_context.get("reusable_tasks_by_unit")
    reusable_ids = []
    if isinstance(reusable, dict):
        for values in reusable.values():
            if isinstance(values, list):
                reusable_ids.extend(values)
    required_units = build_context.get("required_unit_ids")
    if not isinstance(required_units, list):
        units = build_task_plan.get("build_units")
        required_units = list(units.keys()) if isinstance(units, dict) else []
    return {
        "kind": "build_context",
        "target": {
            "type": _compact_text(target.get("type"), 80) or "application",
            "id": _compact_text(target.get("id"), 240) or "application",
        },
        "requiredUnitIds": _compact_strings(required_units, item_limit=200, text_limit=240),
        "endpointIds": _compact_strings(build_context.get("endpoint_ids"), item_limit=200, text_limit=240),
        "apiContractIds": _compact_strings(
            {
                str(detail.get("api_contract_id") or "")
                for detail in build_context.get("direct_endpoint_contracts") or []
                if isinstance(detail, dict) and detail.get("api_contract_id")
            },
            item_limit=200,
            text_limit=240,
        ),
        "dataSourceIds": _compact_strings(
            {
                str(design.get("data_source_type") or "")
                for design in build_context.get("entity_designs") or []
                if isinstance(design, dict) and design.get("data_source_type")
            },
            item_limit=200,
            text_limit=240,
        ),
        "entityIds": _compact_strings(
            build_context.get("entity_ids"), item_limit=200, text_limit=240
        ),
        "reusableTaskIds": _compact_strings(reusable_ids, item_limit=200, text_limit=240),
    }


def project_contract_validation_output(
    build_context: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """投射契约校验范围、通过状态和受限错误信息。"""

    return {
        "kind": "contract_validation",
        "isValid": not errors,
        "checkedEndpointIds": _compact_strings(
            build_context.get("endpoint_ids"), item_limit=200, text_limit=240
        ),
        "checkedApiContractIds": _compact_strings(
            {
                str(detail.get("api_contract_id") or "")
                for detail in build_context.get("direct_endpoint_contracts") or []
                if isinstance(detail, dict) and detail.get("api_contract_id")
            },
            item_limit=200,
            text_limit=240,
        ),
        "issues": _compact_strings(errors, item_limit=100, text_limit=1_000),
    }


def project_candidate_tasks_output(build_task_plan: dict[str, Any]) -> dict[str, Any]:
    """投射模型规划阶段的候选任务列表和负责人汇总。"""

    tasks = _project_tasks(build_task_plan)
    return {
        "kind": "candidate_tasks",
        "tasks": tasks,
        "summary": _task_owner_summary(tasks),
    }


def project_compiled_tasks_output(build_task_plan: dict[str, Any]) -> dict[str, Any]:
    """投射编译后的拓扑任务、依赖边和负责人汇总。"""

    tasks = _project_tasks(build_task_plan)
    task_graph = build_task_plan.get("task_graph")
    task_graph = task_graph if isinstance(task_graph, dict) else {}
    return {
        "kind": "compiled_tasks",
        "tasks": tasks,
        "edges": _project_edges(task_graph.get("edges")),
        "summary": _task_owner_summary(tasks),
    }


def project_dag_validation_output(build_task_plan: dict[str, Any]) -> dict[str, Any]:
    """投射 DAG 校验结果、拓扑顺序和执行批次。"""

    task_graph = build_task_plan.get("task_graph")
    task_graph = task_graph if isinstance(task_graph, dict) else {}
    validation = task_graph.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    execution = build_task_plan.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    batches = execution.get("batches") or task_graph.get("execution_layers") or []
    projected_batches = []
    if isinstance(batches, list):
        for index, batch in enumerate(batches[:MAX_STAGE_RECORDS]):
            if not isinstance(batch, dict):
                continue
            projected_batches.append(
                {
                    "index": index + 1,
                    "mode": _compact_text(batch.get("mode"), 40) or "serial",
                    "taskIds": _compact_strings(batch.get("tasks"), item_limit=200, text_limit=240),
                }
            )
    return {
        "kind": "dag_validation",
        "isValid": validation.get("is_valid") is True,
        "roots": _compact_strings(task_graph.get("roots"), item_limit=200, text_limit=240),
        "leaves": _compact_strings(task_graph.get("leaves"), item_limit=200, text_limit=240),
        "topologicalOrder": _compact_strings(
            task_graph.get("topological_order"), item_limit=200, text_limit=240
        ),
        "batches": projected_batches,
        "issues": _compact_strings(validation.get("errors"), item_limit=100, text_limit=1_000),
    }


def project_artifact_output(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """投射已保存产物列表，并隐藏内部计划正文。"""

    return {
        "kind": "artifacts",
        "artifacts": deepcopy(artifacts),
        "count": len(artifacts),
    }


def _project_edges(value: Any) -> dict[str, Any]:
    """裁剪依赖边字段并标记超过上限的部分。"""

    if not isinstance(value, list):
        return {"items": [], "truncated": False}
    items = []
    for edge in value[:MAX_STAGE_EDGES]:
        if not isinstance(edge, dict):
            continue
        source = _compact_text(edge.get("from"), 240)
        target = _compact_text(edge.get("to"), 240)
        if source and target:
            items.append(
                {
                    "from": source,
                    "to": target,
                    "type": _compact_text(edge.get("type"), 80) or "depends_on",
                }
            )
    return {"items": items, "truncated": len(value) > MAX_STAGE_EDGES}


def _project_validation(value: dict[str, Any]) -> dict[str, Any]:
    """统一投射图校验结果和错误列表。"""

    return {
        "isValid": value.get("is_valid") is True,
        "issues": _compact_strings(value.get("errors"), item_limit=100, text_limit=1_000),
    }


def _task_owner_summary(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """按任务负责人汇总候选或编译任务数量。"""

    counts = {"frontend": 0, "backend": 0, "database": 0}
    for task in tasks:
        owner = task.get("owner")
        if owner == "frontend":
            counts["frontend"] += 1
        elif owner in {"backend", "data_source"}:
            counts["backend"] += 1
        elif owner == "database":
            counts["database"] += 1
    return counts


def _project_summary(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, int | bool]:
    """汇总 Unit、任务、边和批次数量，供进度卡快速阅读。"""

    build_units = build_task_plan.get("build_units")
    task_graph = build_task_plan.get("task_graph")
    execution = build_task_plan.get("execution")
    summary = build_task_plan.get("summary")
    build_units = build_units if isinstance(build_units, dict) else {}
    task_graph = task_graph if isinstance(task_graph, dict) else {}
    execution = execution if isinstance(execution, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    validation = task_graph.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    return {
        "unitCount": len(build_units),
        "taskCount": len(tasks),
        "edgeCount": len(task_graph.get("edges") or []),
        "batchCount": len(execution.get("batches") or task_graph.get("execution_layers") or []),
        "frontendCount": _integer(summary.get("frontend")),
        "backendCount": _integer(summary.get("backend")),
        "databaseCount": _integer(summary.get("database")),
        "isValid": validation.get("is_valid") is True,
    }


def _change_paths(task: dict[str, Any]) -> list[str]:
    """从 change_scope 或 target_files 提取去重后的安全路径列表。"""

    values: list[Any] = []
    change_scope = task.get("change_scope")
    if isinstance(change_scope, list):
        values.extend(
            item.get("path") or item.get("file") if isinstance(item, dict) else item
            for item in change_scope
        )
    if not values:
        target_files = task.get("target_files")
        values = target_files if isinstance(target_files, list) else []
    return _compact_strings(values, item_limit=200, text_limit=1_000)


def _project_change_scope(value: Any) -> list[dict[str, Any]]:
    """裁剪任务变更范围，保留前端核对操作和路径所需的安全字段。"""

    if not isinstance(value, list):
        return []
    projected: list[dict[str, Any]] = []
    for item in value[:200]:
        if not isinstance(item, dict):
            continue
        entry = {
            key: _compact_text(item.get(key), 80 if key == "operation" else 1_000)
            for key in ("path", "operation")
            if item.get(key)
        }
        if entry:
            projected.append(entry)
    return projected


def _project_acceptance_checks(value: Any) -> list[dict[str, Any]]:
    """只暴露工程检查的名称和检查类型，隐藏实现细节与内部绝对路径。"""

    if not isinstance(value, list):
        return []
    projected: list[dict[str, Any]] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        entry = {
            key: _compact_text(item.get(key), 240)
            for key in ("id", "type", "description", "path")
            if item.get(key)
        }
        if entry:
            projected.append(entry)
    return projected


def _compact_strings(value: Any, *, item_limit: int, text_limit: int) -> list[str]:
    """规范化、裁剪并去重不可信列表字段。"""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _compact_text(item, text_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= item_limit:
            break
    return result


def _compact_text(value: Any, limit: int) -> str:
    """把任意标量转换为单个受限字符串。"""

    return str(value or "").strip()[:limit]


def _task_status(value: Any) -> str:
    """限制任务状态为公开协议支持的四种值。"""

    status = str(value or "pending")
    return status if status in {"pending", "running", "completed", "failed"} else "pending"


def _integer(value: Any) -> int:
    """安全读取非负整数摘要字段。"""

    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
