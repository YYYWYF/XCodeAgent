"""定义失败位置对应的业务恢复边界，供解析、事务校验和持久化共同使用。"""

from app.domain.execution_recovery import DurableExecutionRecord


def failed_reentry_target(source: DurableExecutionRecord) -> str:
    """统一失败后的业务恢复边界，测试局部修复只作为测试阶段内部步骤。"""

    return failed_node_reentry_target(source.current_node or "", source.execution_kind, source.workflow_scope)


def failed_node_reentry_target(node: str, execution_kind: str = "workbench", workflow_scope: str | None = None) -> str:
    """按统一业务边界解析失败节点，供原生恢复及结束后继续共同使用。"""
    node = str(node).strip()
    # 仅改变失败重试入口，保留真实失败节点用于诊断；暂停续接和单测修复不受影响。
    if execution_kind == "workbench" and workflow_scope != "conversation" and node == "small_task_repair":
        return "integration_test"
    return node
