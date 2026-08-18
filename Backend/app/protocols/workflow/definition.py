"""主工作流运行层与映射层共享的稳定协议元数据。"""

from __future__ import annotations

from typing import Any

WORKFLOW_EVENT_PROTOCOL = "xcodeagent.workflow.event.v1"
PROCESS_EVENT_NAME = "agent-process"
PROCESS_DETAIL_LIMIT = 24_000

WORKFLOW_NODE_LABELS = {
    "detail_confirmation": "页面细节确认",
    "project_planning": "项目规划",
    "inspect_workspace": "扫描工作区代码",
    "inspect_database_context": "数据库上下文检查",
    "prepare_build_tasks": "构建任务 DAG 生成",
    "build": "代码生成与构建协调",
    "integration_test": "集成测试与质量门禁",
    "small_task_repair": "局部修复任务执行",
    "launch_project": "启动本地预览",
    "acceptance": "用户验收",
    "finalize_project": "完成项目",
    "handle_failure": "失败处理",
}

# 仅用于可视化层的兜底预测；实际节点路由始终以 LangGraph 为准。
WORKFLOW_STATIC_NEXT_NODES = {
    "detail_confirmation": ["inspect_workspace"],
    "project_planning": ["detail_confirmation"],
    "inspect_workspace": ["inspect_database_context", "prepare_build_tasks"],
    "inspect_database_context": ["prepare_build_tasks"],
    "prepare_build_tasks": ["build"],
    "build": ["integration_test"],
    "small_task_repair": ["integration_test"],
    "acceptance": ["finalize_project"],
}

WORKFLOW_ARTIFACT_FIELDS = (
    "requirement_spec_path",
    "requirement_spec_json_path",
    "project_plan_path",
    "project_plan_json_path",
    "build_task_plan_path",
    "build_task_dag_path",
    "test_report_path",
    "repair_task_plan_path",
)


def workflow_capabilities() -> dict[str, Any]:
    """描述公开的 `/workflow/run` 请求和事件契约。"""

    return {
        "name": "workflow-run",
        "endpoint": "/workflow/run",
        "transport": "ag-ui-sse",
        "workflowActions": {
            "requestField": "forwardedProps.workflowAction",
            "values": {
                "retry_failed_tasks": (
                    "恢复当前 Build 切片中的失败任务：优先重试 retry 分类的瞬时失败；"
                    "没有瞬时候选时，执行已生成且无需额外确认的 RepairPlanner 修复任务。"
                )
            },
        },
        "acceptanceAdjustments": {
            "requestField": "clarificationAnswers.acceptance_adjustment",
            "values": {
                "local_fix": "仅修改当前已确认范围内的局部实现，不改变产品语义。",
                "page_design_change": "重新生成页面详细设计并再次确认。",
                "endpoint_change": "重新生成接口详细设计并再次确认。",
                "data_source_change": "重新生成实体设计并再次确认，接口详情不依赖数据源。",
                "project_plan_change": "重新生成项目计划，确认后再生成页面/接口详细设计。",
            },
        },
        "skillSelection": {
            "requestField": "forwardedProps.selectedSkillNames",
            "stateField": "selectedSkillNames",
            "semantics": "selected-user-skills-are-force-loaded",
            "emptyBehavior": "all-enabled-user-skills-available-on-demand",
            "frontendBuiltinSkillsRetained": True,
            "forcedAgents": [
                "frontend",
                "data_source",
                "database",
                "repair_planner",
                "small_task",
                "workspace_assistant",
            ],
            "directChatModelNodesLoadSkills": False,
            "maxPromptBytes": 65536,
            "errors": [
                "invalid_selected_skills",
                "selected_skill_unavailable",
                "selected_skill_conflict",
                "selected_skills_context_too_large",
            ],
        },
        "eventProtocol": {
            "version": WORKFLOW_EVENT_PROTOCOL,
            "lifecycleProjection": {
                "customEventName": "application-lifecycle",
                "semantics": "emitted-immediately-after-persisted-revision",
                "mergeKey": "revision",
            },
            "eventTypes": [
                "workflow.run.started",
                "workflow.node.started",
                "workflow.node.progress",
                "workflow.node.completed",
                "agent-process",
                "workflow.run.finished",
                "workflow.run.failed",
            ],
            "nodeCompletedDetail": {
                "projectPlanUpdate": (
                    "Optional read-only Markdown snapshot for the page or endpoint sections "
                    "confirmed by detail_confirmation."
                ),
            },
            "agentProcess": {
                "name": PROCESS_EVENT_NAME,
                "optionalFields": {
                    "nodeName": "Stable workflow node name.",
                    "attempt": "One-based execution attempt; omitted by legacy sessions.",
                    "iterationKind": "initial_build|initial_test|repair_build|retest|initial",
                    "buildExecutionSlice": (
                        "Build-task snapshot attached to the matching build attempt; running "
                        "tasks may carry ephemeral activeToolActivity."
                    ),
                    "dagGeneration": (
                        "Compact prepare-build-tasks snapshot containing ordered generation "
                        "stages, bounded discriminated stage outputs, compatibility task/artifact "
                        "aliases, counts, and safe artifact labels."
                    ),
                    "workspaceInspection": (
                        "Safe inspect-workspace summary containing bounded counts, detected "
                        "stack, relative project roots and entrypoints, cache status, CRG node "
                        "and relation distributions, symbol previews, and warnings."
                    ),
                    "workspaceInspectionProgress": (
                        "Running bounded code-review-graph progress with stage, message, and "
                        "file/symbol/relation counters."
                    ),
                    "checks": {
                        "description": "Incremental integration-test check snapshot.",
                        "item": {
                            "id": "stable check identifier",
                            "name": "display name",
                            "status": "running|passed|skipped|failed",
                            "required": "boolean",
                            "evidence": "brief sanitized evidence",
                        },
                    }
                },
            },
        },
        "scan": {
            "node": "inspect_workspace",
            "label": "扫描工作区代码",
            "progressEvent": "workspace_inspection.progress",
            "fallback": "workspace_search",
        },
        "phases": [
            {"id": node_id, "label": label}
            for node_id, label in WORKFLOW_NODE_LABELS.items()
        ],
    }
