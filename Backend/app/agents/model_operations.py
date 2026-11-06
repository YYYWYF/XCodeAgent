"""由调用方的明确业务动作绑定模型操作，不读取提示词或修改共享模型实例。"""

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps

from app.agents.model_business_context import current_model_context, model_business_context


@contextmanager
def model_operation(
    operation: str | None, *, business_stage: str | None = None, target: dict | None = None,
) -> Iterator[None]:
    """为当前同步或异步执行范围绑定操作，退出和异常时恢复原业务上下文。"""
    value = current_model_context({})
    if operation is not None:
        value["business_operation"] = operation
    if business_stage is not None:
        value["business_stage"] = business_stage
    if target is not None:
        value["target"] = target
    with model_business_context(value):
        yield


def observe_model_operation(operation: str, *, business_stage: str | None = None):
    """为固定业务用途的普通函数或协程绑定上下文，保留函数签名和返回值。"""
    def decorate(action):
        """按原函数类型创建包装，不改变调用预算、重试或业务确认流程。"""
        if inspect.iscoroutinefunction(action):
            @wraps(action)
            async def async_call(*args, **kwargs):
                """在本次协程内隔离操作上下文，取消时同样恢复。"""
                with model_operation(operation, business_stage=business_stage):
                    return await action(*args, **kwargs)
            return async_call

        @wraps(action)
        def sync_call(*args, **kwargs):
            """让函数内部的多轮模型请求继承同一个明确的业务用途。"""
            with model_operation(operation, business_stage=business_stage):
                return action(*args, **kwargs)
        return sync_call
    return decorate


_MAPPING_OPERATIONS = {
    "database": {
        "operation": "database_classify_operation",
        "query": "database_generate_query_mapping",
        "write": "database_generate_write_mapping",
        "return": "database_generate_response_mapping",
        "description": "database_generate_mapping_description",
        "rule": "database_refine_rule",
    },
    "external_api": {
        "request": "external_generate_request_mapping",
        "return": "external_generate_response_mapping",
        "description": "external_generate_mapping_description",
        "rule": "external_refine_rule",
    },
    "static": {
        "return": "static_generate_response_mapping",
        "rule": "static_refine_rule",
        "mock": "static_generate_mock_data",
    },
}


def mapping_operation(context: dict, step: str) -> str | None:
    """只按已校验的来源类型和明确的生成步骤选择接口操作。"""
    source = context.get("selection", {}).get("sourceType")
    return _MAPPING_OPERATIONS.get(source, {}).get(step)


def unit_planning_operation(context, *, repairing: bool = False) -> str | None:
    """按冻结 Unit 的稳定身份区分任务生成职责及校验修复轮次。"""
    identifier = context.unit_id
    if identifier == "backend:bootstrap":
        kind = "backend_bootstrap"
    elif identifier.startswith("backend:endpoint:"):
        kind = "backend_endpoint"
    elif identifier == "frontend:api-client":
        kind = "frontend_api"
    elif identifier == "frontend:data:static":
        kind = "frontend_static_data"
    elif identifier.startswith("page:"):
        kind = "frontend_page"
    else:
        return None
    return f"{'repair' if repairing else 'generate'}_{kind}_tasks"


def layer_operation(layers, *, purpose: str) -> str | None:
    """按显式源码或任务归属区分前端、后端及同次请求联合处理两端的用途。"""
    sides = set(layers) & {"frontend", "backend"}
    if not sides:
        return None
    side = "fullstack" if len(sides) == 2 else next(iter(sides))
    patterns = {
        "unit_tests": "generate_{}_unit_tests",
        "unit_repair": "repair_{}_unit_test_code",
    }
    return patterns[purpose].format(side)


def build_task_operation(owner: str, tasks: list[dict]) -> str | None:
    """按已登记的任务 owner 和 repair 标记区分代码生成与局部修复。"""
    repairing = any(task.get("kind") == "repair" for task in tasks)
    suffix = {"frontend": "frontend_code", "backend": "backend_code",
              "database": "database_change_plan"}.get(owner)
    if suffix is None:
        return None
    return f"{'repair' if repairing else 'generate'}_{suffix}"


def small_task_operation(packet: dict) -> tuple[str | None, str | None]:
    """只按任务包的明确来源和 owner 绑定小任务操作，不猜测文件内容或提示词。"""
    owner, source = packet.get("owner"), packet.get("source")
    if source == "free_chat":
        return {"frontend": "modify_frontend_code", "backend": "modify_backend_code"}.get(owner), "conversation"
    if source == "unit_test.small_task":
        layers = {"frontend", "backend"} if owner == "fullstack" else {owner}
        return layer_operation(layers, purpose="unit_repair"), "unit_test"
    if source == "integration_test.small_task":
        return {"frontend": "repair_frontend_integration_code",
                "backend": "repair_backend_integration_code",
                "database": "repair_integration_database_changes"}.get(owner), "integration_test"
    if source == "conversation.direct_modification_repair":
        return {"frontend": "repair_frontend_modification",
                "backend": "repair_backend_modification"}.get(owner), "conversation"
    return None, None


def technical_plan_operation(requirement_spec: dict, existing_plan: dict | None = None) -> str:
    """用独立保存的系统反馈和用户修改要求区分技术方案生成、修订和修复。"""
    if requirement_spec.get("technical_plan_validation_feedback"):
        return "repair_technical_plan"
    if existing_plan and requirement_spec.get("planning_adjustment_request"):
        return "revise_technical_plan"
    inherited = current_model_context({}).get("business_operation")
    if inherited in {"revise_technical_plan", "repair_technical_plan"}:
        return inherited
    return "generate_technical_plan"
