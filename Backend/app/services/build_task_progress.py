"""构建任务 DAG 生成阶段的紧凑进度投影。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from langgraph.config import get_stream_writer

from app.services.build_task_planner import tasks_from_build_task_plan


ProgressWriter = Callable[[dict[str, Any]], None]

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
        self._artifacts: list[dict[str, str]] = []

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
        artifacts: list[dict[str, str]] | None = None,
    ) -> None:
        """完成指定阶段，并可同步更新紧凑任务及产物投影。"""

        if isinstance(build_task_plan, dict):
            self._build_task_plan = build_task_plan
        if artifacts is not None:
            self._artifacts = artifacts
        self._update_stage(stage_id, status="completed", detail=detail)
        self._emit()

    def fail(
        self,
        stage_id: str,
        detail: str,
        *,
        build_task_plan: dict[str, Any] | None = None,
    ) -> None:
        """把当前阶段标记为失败，同时保留之前阶段和完整任务注册表。"""

        if isinstance(build_task_plan, dict):
            self._build_task_plan = build_task_plan
        self._update_stage(stage_id, status="failed", detail=detail)
        self._emit()

    def snapshot(self) -> dict[str, Any]:
        """返回可持久化和发送给前端的安全紧凑快照。"""

        tasks = _project_tasks(self._build_task_plan)
        return {
            "stages": deepcopy(self._stages),
            "tasks": tasks,
            "summary": _project_summary(self._build_task_plan, tasks),
            "artifacts": deepcopy(self._artifacts),
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


def build_task_artifacts(markdown_path: str) -> list[dict[str, str]]:
    """构造面向用户的产物摘要，隐藏内部 JSON 文件路径与正文。"""

    return [
        {
            "id": "build_task_plan",
            "name": "内部 Build Task Plan",
            "kind": "internal",
            "status": "saved",
        },
        {
            "id": "build_task_dag",
            "name": "BUILD_TASK_DAG.md",
            "kind": "markdown",
            "status": "saved",
            "path": _compact_text(markdown_path, 1_000),
        },
    ]


def _project_tasks(build_task_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """按任务图顺序裁剪前端展示所需字段，不暴露模型原文或内部状态。"""

    projected: list[dict[str, Any]] = []
    for task in tasks_from_build_task_plan(build_task_plan):
        task_id = _compact_text(task.get("id") or task.get("task_id"), 240)
        if not task_id:
            continue
        dependencies = task.get("dependencies") or task.get("dependsOn") or []
        acceptance = task.get("acceptance_criteria") or task.get("acceptanceCriteria") or []
        projected.append(
            {
                "id": task_id,
                "title": _compact_text(
                    task.get("title") or task.get("description") or task_id,
                    500,
                ),
                "owner": _compact_text(task.get("owner"), 80),
                "status": _task_status(task.get("status")),
                "dependencies": _compact_strings(dependencies, item_limit=200, text_limit=240),
                "changePaths": _change_paths(task),
                "acceptanceCriteria": _compact_strings(
                    acceptance,
                    item_limit=100,
                    text_limit=1_000,
                ),
            }
        )
    return projected


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
    """从 change_scope 或 targetFiles 提取去重后的安全路径列表。"""

    values: list[Any] = []
    change_scope = task.get("change_scope") or task.get("changeScope")
    if isinstance(change_scope, list):
        values.extend(
            item.get("path") or item.get("file") if isinstance(item, dict) else item
            for item in change_scope
        )
    if not values:
        target_files = task.get("targetFiles") or task.get("target_files")
        values = target_files if isinstance(target_files, list) else []
    return _compact_strings(values, item_limit=200, text_limit=1_000)


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
