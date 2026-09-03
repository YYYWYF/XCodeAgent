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
    "planning_stage_entry": "进入规划阶段",
    "technical_planning": "技术规划",
    "application_revision": "正式产物二次修改",
    "development_readiness_gate": "开发前置检查",
    "entity_source_binding": "实体数据源绑定",
    "project_planning": "技术规划调整",
    "inspect_workspace": "扫描工作区代码",
    "prepare_build_tasks": "构建任务 DAG 生成",
    "authorization_bootstrap": "权限数据库初始化",
    "build": "代码生成与构建协调",
    "unit_test": "开发阶段单元测试",
    "unit_test_repair": "单元测试局部修复",
    "test_phase_confirmation": "开发完成与测试阶段确认",
    "integration_test": "集成测试与质量门禁",
    "small_task_repair": "局部修复任务",
    "review_phase_confirmation": "测试完成与审查阶段确认",
    "code_review": "前后端代码审查",
    "acceptance_phase_confirmation": "验收阶段确认",
    "launch_project": "启动本地预览",
    "acceptance_review": "用户验收",
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
    "ui_confirmation": ["planning_stage_entry"],
    "planning_stage_entry": ["technical_planning"],
    "technical_planning": [],
    "application_revision": ["inspect_workspace"],
    "inspect_workspace": ["prepare_build_tasks"],
    "prepare_build_tasks": ["authorization_bootstrap", "build", "handle_failure"],
    "authorization_bootstrap": ["build", "handle_failure"],
    "build": ["unit_test"],
    "unit_test": ["unit_test_repair", "test_phase_confirmation"],
    "unit_test_repair": ["unit_test"],
    "test_phase_confirmation": ["integration_test"],
    "integration_test": ["review_phase_confirmation", "small_task_repair"],
    "review_phase_confirmation": ["code_review"],
    "code_review": ["acceptance_phase_confirmation", "handle_failure"],
    "acceptance_phase_confirmation": ["acceptance"],
    "small_task_repair": ["integration_test"],
    # 验收子图内部节点仍通过稳定的 nodeName 公开启动进度，下一节点是
    # acceptance_review；主图门面 acceptance 再负责交回 finalize_project。
    "launch_project": ["acceptance_review"],
    "acceptance_review": [],
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
    "unit_test_report_path",
    "unit_test_repair_task_plan_path",
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
                "retry_code_review": (
                    "仅在 codeReviewRetry.available=true 时恢复失败的审查扫描或修复模型请求；"
                    "修复重试复用原问题快照和失败前轮次。"
                ),
                "build_task_plan_confirmation": (
                    "通过 clarificationAnswers 提交只读 Build 任务计划的 confirm 或 regenerate 动作。"
                ),
                "test_phase_confirmation": (
                    "通过 clarificationAnswers.test_phase_confirmation 提交结构化 confirm 动作；"
                    "确认后恢复 test_phase_confirmation 并进入 integration_test。"
                ),
                "review_phase_confirmation": (
                    "通过 clarificationAnswers.review_phase_confirmation 提交结构化 confirm 动作；"
                    "确认后恢复 review_phase_confirmation 并进入 code_review。"
                ),
                "code_review_repair_confirmation": (
                    "通过 clarificationAnswers.code_review_repair_confirmation 提交结构化 repair_all 动作；"
                    "确认后恢复 code_review 子图并执行受限代码修复。"
                ),
                "acceptance_phase_confirmation": (
                    "通过 clarificationAnswers.acceptance_phase_confirmation 提交结构化 confirm 动作；"
                    "确认后恢复验收阶段确认并进入 acceptance 子图。"
                ),
                "start_revision": (
                    "批准当前 lifecycle 绑定的 workbench_plan_revision impact 后，"
                    "固定进入隔离正式草稿节点。"
                ),
                "start_technical_revision": (
                    "批准 workbench_plan_revision impact 后恢复原 planning checkpoint 的 technical_planning 节点，重新生成 TechnicalPlan。"
                ),
                "submit_revision_interaction": (
                    "提交绑定 changeId、lifecycle revision、interaction、artifact 和 draft hash "
                    "的 confirm/save/revise/discard 动作；客户端不能指定 Graph 节点。"
                ),
                "continue_revision_build": (
                    "消费任一 formal revision 的 TechnicalPlan 确认后签发的一次性 token，"
                    "固定进入开发前置检查，通过后进入工作区扫描与 Build DAG；token 绑定 application/change/thread/"
                    "TechnicalPlan hash/lifecycle revision/target；独立 application_planning 分支直接创建开发 execution，"
                    "主 Workflow 分支如携带有效来源 execution 则执行原子替换。"
                ),
                "start_entity_binding": (
                    "引用开发门禁登记的 continuation，在独立 thread 启动缺失实体的 "
                    "EntitySourceBinding execution。"
                ),
                "continue_after_entity_binding": (
                    "消费实体确认后由后端签发的一次性 token，校验原 execution、目标与 "
                    "TechnicalPlan 哈希后恢复原开发 thread，并重新执行开发前置检查。"
                ),
            },
            "clientNodeSelectionAllowed": False,
        },
        "clarificationModes": {
            "unit_test_confirmation": {
                "answerField": "clarificationAnswers.unit_test_confirmation",
                "answer": {"selected": ["run"], "values": ["run", "skip"]},
                "lifecycleInteraction": "unit_test_confirmation",
            },
            "frontend_performance_confirmation": {
                "answerField": "clarificationAnswers.frontend_performance_confirmation",
                "answer": {"selected": ["run"], "values": ["run", "skip"]},
                "lifecycleInteraction": "frontend_performance_confirmation",
            },
            "test_phase_confirmation": {
                "answerField": "clarificationAnswers.test_phase_confirmation",
                "answer": {"action": "confirm"},
                "testTarget": {
                    "type": "page|endpoint|data_source|application",
                    "id": "稳定目标 ID",
                    "label": "显示名称",
                },
            },
            "review_phase_confirmation": {
                "answerField": "clarificationAnswers.review_phase_confirmation",
                "answer": {"action": "confirm"},
                "lifecycleInteraction": "review_phase_confirmation",
            },
            "code_review_repair_confirmation": {
                "answerField": "clarificationAnswers.code_review_repair_confirmation",
                "answer": {"action": "repair_all"},
                "lifecycleInteraction": "code_review_repair_confirmation",
            },
            "acceptance_phase_confirmation": {
                "answerField": "clarificationAnswers.acceptance_phase_confirmation",
                "answer": {"action": "confirm"},
                "lifecycleInteraction": "acceptance_phase_confirmation",
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
                "code_review.repair",
                "code_review.build_checks",
                "agent-process",
                "workflow.run.finished",
                "workflow.run.failed",
                "application-revision",
            ],
            "revisionProjection": {
                "impactMode": "revision_impact_confirmation",
                "draftMode": "revision_draft_confirmation",
                "continuationField": "revisionContinuation",
                "completionEvent": "application-revision",
            },
            "agentProcess": {
                "name": PROCESS_EVENT_NAME,
                "optionalFields": {
                    "nodeName": "Stable workflow node name.",
                    "attempt": "One-based execution attempt; omitted by legacy sessions.",
                    "iterationKind": (
                        "initial_build|initial_unit_test|initial_unit_repair|initial_test|"
                        "repair_build|unit_retest|unit_repair_retest|retest|initial"
                    ),
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
                        "description": "Incremental unit-test or integration-test check snapshot.",
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
