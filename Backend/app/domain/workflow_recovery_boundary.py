"""定义失败位置对应的业务恢复边界，供解析、事务校验和持久化共同使用。"""

from app.domain.execution_recovery import DurableExecutionRecord


def failed_reentry_target(source: DurableExecutionRecord) -> str:
    """统一失败后的业务恢复边界，测试局部修复只作为测试阶段内部步骤。"""

    node = str(source.current_node or "").strip()
    # 仅改变失败重试入口，保留真实失败节点用于诊断；暂停续接和单测修复不受影响。
    if source.execution_kind == "workbench" and source.workflow_scope != "conversation" and node == "small_task_repair":
        return "integration_test"
    return node
