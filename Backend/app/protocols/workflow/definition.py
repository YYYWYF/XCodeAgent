"""主工作流运行层与映射层共享的稳定协议元数据。"""

from __future__ import annotations

from typing import Any

WORKFLOW_EVENT_PROTOCOL = "xcodeagent.workflow.event.v1"
PROCESS_EVENT_NAME = "agent-process"
PROCESS_DETAIL_LIMIT = 24_000

WORKFLOW_NODE_LABELS = {
    "classify_request_complexity": "判断需求复杂度",
    "requirements": "需求确认与 RequirementSpec",
    "direct_modification": "简单需求直接修改",
    "project_planning": "项目级计划生成",
    "detail_confirmation": "页面细节确认",
    "prepare_build_tasks": "构建任务 DAG 生成",
    "build": "代码生成与构建协调",
    "integration_test": "集成测试与质量门禁",
    "launch_project": "启动本地预览",
    "acceptance": "用户验收",
    "finalize_project": "完成项目",
    "handle_failure": "失败处理",
}

# 仅用于可视化层的兜底预测；实际节点路由始终以 LangGraph 为准。
WORKFLOW_STATIC_NEXT_NODES = {
    "requirements": ["project_planning"],
    "direct_modification": ["integration_test"],
    "project_planning": ["detail_confirmation"],
    "detail_confirmation": ["prepare_build_tasks"],
    "prepare_build_tasks": ["build"],
    "build": ["integration_test"],
    "launch_project": ["acceptance"],
    "acceptance": ["finalize_project"],
}

WORKFLOW_ARTIFACT_FIELDS = (
    "requirement_spec_path",
    "requirement_spec_json_path",
    "project_plan_path",
    "project_plan_json_path",
    "build_task_plan_path",
    "test_report_path",
    "repair_task_plan_path",
)


def workflow_capabilities() -> dict[str, Any]:
    """描述公开的 `/workflow/run` 请求和事件契约。"""

    return {
        "name": "workflow-run",
        "endpoint": "/workflow/run",
        "transport": "ag-ui-sse",
        "eventProtocol": {
            "version": WORKFLOW_EVENT_PROTOCOL,
            "eventTypes": [
                "workflow.run.started",
                "workflow.node.started",
                "workflow.node.completed",
                "agent-process",
                "workflow.run.finished",
                "workflow.run.failed",
            ],
        },
        "phases": [
            {"id": node_id, "label": label}
            for node_id, label in WORKFLOW_NODE_LABELS.items()
        ],
    }
