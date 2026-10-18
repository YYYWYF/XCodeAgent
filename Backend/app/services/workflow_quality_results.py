"""新一轮代码或正式产物修改使旧质量结果失效，不改变工作流路由与准入。"""

from typing import Any

CODE_MUTATION_PHASES = {"prepare_build_tasks", "build", "execute_frontend", "execute_backend"}


def pending_quality_results(extensions: dict[str, Any]) -> dict[str, Any]:
    """保留无关扩展，把旧测试、审查和验收结果标记为本轮待执行。"""
    return {
        **extensions,
        "testExecutionStatus": "pending",
        "reviewStatus": "pending",
        "acceptanceStatus": "pending",
    }
