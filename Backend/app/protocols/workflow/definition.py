"""主工作流运行层与映射层共享的稳定协议元数据。"""

from __future__ import annotations

from typing import Any

WORKFLOW_EVENT_PROTOCOL = "xcodeagent.workflow.event.v1"
PROCESS_EVENT_NAME = "agent-process"
PROCESS_DETAIL_LIMIT = 24_000

WORKFLOW_NODE_LABELS = {
    "design_intent_analysis": "设计变更意图分析",
    "design_chat_response": "设计对话回复",
    "requirements": "需求文档",
    "product_planning": "产品规划",
    "ui_confirmation": "UI 设计",
    "technical_planning": "技术规划",
    "development_readiness_gate": "开发前置检查",
    "entity_source_binding": "实体数据源绑定",
    "project_planning": "技术规划调整",
    "inspect_workspace": "扫描工作区代码",
    "prepare_build_tasks": "构建任务 DAG 生成",
    "build": "代码生成与构建协调",
    "test_phase_confirmation": "开发完成与测试阶段确认",
    "integration_test": "集成测试与质量门禁",
    "small_task_repair": "局部修复任务",
    "launch_project": "启动本地预览",
    "acceptance": "用户验收",
    "finalize_project": "完成项目",
    "handle_failure": "失败处理",
}

# 仅用于可视化层的兜底预测；实际节点路由始终以 LangGraph 为准。
WORKFLOW_STATIC_NEXT_NODES = {
    "design_intent_analysis": ["requirements", "product_planning", "ui_confirmation"],
    "development_readiness_gate": ["inspect_workspace"],
    "entity_source_binding": [],
    "project_planning": ["development_readiness_gate"],
    "requirements": ["product_planning"],
    "product_planning": ["ui_confirmation"],
    "ui_confirmation": ["technical_planning"],
    "technical_planning": [],
    "inspect_workspace": ["prepare_build_tasks"],
    "prepare_build_tasks": ["build", "handle_failure"],
    "build": ["test_phase_confirmation"],
    "test_phase_confirmation": ["integration_test"],
    "small_task_repair": ["integration_test"],
    "acceptance": ["finalize_project"],
}

WORKFLOW_ARTIFACT_FIELDS = (
    "requirement_spec_path",
    "requirement_spec_json_path",
    "product_plan_path",
    "product_plan_json_path",
    "technical_plan_path",
    "technical_plan_json_path",
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
        "workflowActions": {
            "requestField": "forwardedProps.workflowAction",
            "values": {
                "retry_failed_tasks": (
                    "恢复当前 Build 切片中的失败任务：优先重试 retry 分类的瞬时失败；"
                    "没有瞬时候选时，执行已生成且无需额外确认的 RepairPlanner 修复任务。"
                ),
                "build_task_plan_confirmation": (
                    "通过 clarificationAnswers 提交 Build DAG 的 confirm、patch 或 regenerate 动作；"
                    "确认对象只允许修改任务 title 和 description。"
                ),
                "test_phase_confirmation": (
                    "通过 clarificationAnswers.test_phase_confirmation 提交结构化 confirm 动作；"
                    "确认后恢复 test_phase_confirmation 并进入 integration_test。"
                ),
            },
        },
        "clarificationModes": {
            "test_phase_confirmation": {
                "answerField": "clarificationAnswers.test_phase_confirmation",
                "answer": {"action": "confirm"},
                "testTarget": {
                    "type": "page|endpoint|data_source|application",
                    "id": "稳定目标 ID",
                    "label": "显示名称",
                },
            }
        },
        "acceptanceAdjustments": {
            "requestField": "clarificationAnswers.acceptance_adjustment",
            "values": {
                "local_fix": "仅修改当前已确认范围内的局部实现，不改变产品语义。",
                "page_design_change": "页面实现变化按 TechnicalPlan 调整处理。",
                "endpoint_change": "返回 TechnicalPlan 调整并重新确认接口契约。",
                "data_source_change": "进入独立 EntitySourceBinding 并重新确认数据源绑定。",
                "project_plan_change": "重新生成并确认 TechnicalPlan，然后重新执行开发前置检查。",
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
                            "advisory": "boolean; advisory checks never block the quality gate",
                            "evidence": "brief sanitized evidence",
                            "passed_tests": "optional non-negative integer for unit-test checks",
                            "total_tests": "optional non-negative integer for unit-test checks",
                            "performanceScores": (
                                "optional 0-100 Lighthouse category scores for "
                                "frontend_performance"
                            ),
                            "performanceMetrics": (
                                "optional Lighthouse core metrics (fcp/lcp/tbt/cls/si) for "
                                "frontend_performance"
                            ),
                            "reportPath": "optional absolute HTML report path",
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
