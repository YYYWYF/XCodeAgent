"""七模块专用 Local 规则，公共候选校验器只做分派。"""

from collections.abc import Mapping, Sequence

from app.services.planning_issues import ValidationIssue
from app.services.runtime_agent_dag import agent_task_manifest
from app.services.runtime_agent_module_policy import module_task_errors
from app.services.unit_generation_contracts import UnitGenerationContext


def validate_agent_module_candidate(context: UnitGenerationContext, tasks: Sequence[Mapping]) -> list[ValidationIssue]:
    """核对精确模块集合、串行边、唯一 capability 与模板路径，沿公共 Local Retry 返回问题。"""

    manifest = {item["task_id"]: item for item in agent_task_manifest(context)}
    issues = []

    def append(message: str, task_ids: tuple[str, ...]) -> None:
        """把当前 Unit 的模型内容错误归属到原生 Local Retry，不另建错误协议。"""
        issues.append(ValidationIssue(
            code="AGENT_MODULE_CONTRACT_INVALID", level="unit", category="generation",
            unit_ids=(context.unit_id,), task_ids=task_ids, retry_unit_ids=(context.unit_id,),
            retryable=True, message=message,
        ))

    actual_ids = [value for task in tasks if isinstance((value := task.get("id")), str)]
    if set(actual_ids) != set(manifest) or len(actual_ids) != len(manifest):
        append("Agent 候选必须精确覆盖当前缺项模块。", tuple(manifest))
    for task in tasks:
        task_id = task.get("id")
        if not isinstance(task_id, str) or task_id not in manifest:
            continue
        expected = manifest[task_id]
        errors = module_task_errors(task, context.constraints.get("agent_template_policy", {}))
        dependencies = task.get("dependencies")
        if not isinstance(dependencies, (list, tuple)) or list(dependencies) != expected["dependencies"]:
            errors.append("模块依赖必须保持平台固定的增量串行顺序。")
        raw = task.get("deliverables")
        declared = [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, (list, tuple)) else []
        if (len(declared) != 1 or declared[0].get("kind") != "agent.runtime"
                or declared[0].get("provides") not in ([expected["requirement_id"]], (expected["requirement_id"],))):
            errors.append("模块必须且只能交付其自身唯一 capability。")
        for error in errors:
            append(error, (task_id,))
    return issues
