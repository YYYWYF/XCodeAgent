from __future__ import annotations

import json
from typing import Any

from app.agents.model_operations import model_operation, observe_model_operation
from app.agents.messages import last_agent_text
from app.config import Settings
from app.services.test_validation import create_repair_task_plan
from app.utils.model_output import extract_json_object


def _test_repair_planning_prompt(
    *,
    test_report: dict[str, Any],
    revision_requests: list[dict[str, Any]],
    build_task_plan: dict[str, Any] | None,
) -> str:
    """构造集成测试 RepairPlanner 提示并约束修复决策边界。"""

    return (
        "You are the RepairPlanner Agent for an app-generation workflow.\n"
        "Review the test report and revision requests. Decide whether bounded "
        "repair is possible for specialist code agents. "
        "Do not mark failed tests as passed. Do not silently change confirmed "
        "requirements, PageImplementationContract, or API contracts. Do not edit files or mutate "
        "workflow state. Base the plan on stdout_tail/stderr_tail or readable virtual "
        "workspace logs. If evidence is unavailable, choose terminal_failure rather "
        "than guessing a root cause.\n\n"
        "Language requirement: write every user-visible natural-language value in Simplified "
        "Chinese, including strategy, reason, failure_handling, repair task title, repair task "
        "description, and change_scope description. Keep file paths, code identifiers, commands, "
        "library names, and verbatim error snippets in their original form when necessary. Do not "
        "return English explanations. 语言要求：所有用户可见的自然语言字段必须使用简体中文；路径、代码标识符、命令和必要的原始错误片段可保留原样。\n\n"
        "For frontend Jest or backend JUnit/Surefire failures, explicitly classify the "
        "failure as test-code (mock, fixture, async wait, expectation, discovery), "
        "production-code behavior, or toolchain before choosing repair. The scoped task "
        "may include only the exact generated test files and their mapped production "
        "sources. Never delete a valid test, weaken an assertion, or add skip/disabled "
        "markers.\n\n"
        "For frontend_install, frontend_build, backend_build, backend_startup, backend_static_check, and "
        "other toolchain failures, choose repair when the fix stays inside the already "
        "authorized project directories (frontend and backend), including package.json, "
        "lockfiles, tsconfig, vite config, pom.xml, and application configuration. Do not "
        "request scope expansion for files inside these directories.\n\n"
        "For backend_startup, inspect the root_cause, exception chain and virtual stdout/stderr "
        "logs. Repair dependency compatibility, startup configuration or production code as "
        "supported by evidence. Never disable the startup check, suppress initialization, or "
        "fake readiness to pass the gate. The backend must be rebuilt and started again.\n\n"
        "Return only one JSON object using this contract:\n"
        "{\n"
        '  "decision": "repair" | "requires_user_confirmation" | "terminal_failure",\n'
        '  "escalationKind": "code_scope" | "formal_revision" | null,\n'
        '  "strategy": "short repair strategy",\n'
        '  "reason": "required for confirmation or terminal failure",\n'
        '  "failure_handling": "how the workflow should handle this failure"\n'
        "}\n\n"
        "Choose requires_user_confirmation only when repair requires expanding code scope, "
        "changing confirmed requirements, or making a product decision. Set escalationKind "
        "to formal_revision only for confirmed product or contract semantics; missing file paths "
        "alone are not formal revision evidence. Choose "
        "terminal_failure when evidence is insufficient or the failure is not "
        "automatically actionable.\n\n"
        f"TestReport:\n{json.dumps(test_report, ensure_ascii=False, indent=2)}\n\n"
        f"RevisionRequests:\n{json.dumps(revision_requests, ensure_ascii=False, indent=2)}\n\n"
        f"CurrentBuildTaskPlan:\n{json.dumps(build_task_plan or {}, ensure_ascii=False, indent=2)}"
    )


def _build_failure_repair_prompt(*, repair_input: dict[str, Any]) -> str:
    """构造包含结构化业务资源扩展协议的构建失败修复提示。"""

    preview_guidance = ""
    if repair_input.get("source") == "preview_startup":
        preview_guidance = (
            "Preview startup diagnosis: failure.diagnostics contains the actual parser/command/process evidence. "
            "Read failure.sourceContext and candidateFiles first; inspect a focused additional window only if needed. "
            "A parser location or compiler location is direct evidence, not a reason to search for the platform's "
            "error-message implementation. Empty logs mean that stage produced no output; do not repeatedly search "
            "empty logs. Use only this attempt's logPaths. Never inspect old repair documents or your own runtime "
            "state for the root cause. Expand to related project files only when current evidence requires it. "
            "Once evidence supports a bounded fix, immediately return the RepairPlan with reason, strategy, exact "
            "files and concrete task descriptions. Do not keep exploring without new evidence. If evidence is "
            "insufficient, return terminal_failure and specify the missing information. Environmental failures "
            "must report the actual missing tool/service/credential, never invent configuration or secrets.\n\n"
        )
    return (
        "You are the RepairPlanner Agent for a build scheduler.\n"
        "This is a planning-only DeepAgent node. Do not edit files, do not run code, "
        "do not mutate scheduler state, and do not rewrite the DAG directly. "
        "Use the provided input to decide whether a bounded repair task can be "
        "created for the failed build task.\n\n"
        "Language requirement: write every user-visible natural-language value in Simplified "
        "Chinese, including strategy, reason, failure_handling, repair task title, repair task "
        "description, and change_scope description. Keep file paths, code identifiers, commands, "
        "library names, and verbatim error snippets in their original form when necessary. Do not "
        "return English explanations. 语言要求：所有用户可见的自然语言字段必须使用简体中文；路径、代码标识符、命令和必要的原始错误片段可保留原样。\n\n"
        "Return only one JSON object using this contract:\n"
        "{\n"
        '  "decision": "repair" | "requires_user_confirmation" | "terminal_failure",\n'
        '  "strategy": "short repair strategy",\n'
        '  "boundaries": {\n'
        '    "change_scope_policy": "must stay within input.change_scope",\n'
        '    "allowed_paths_policy": "must stay within input.change_scope.allowed_paths",\n'
        '    "contract_policy": "do not change confirmed requirements, ProjectPlan, or API contracts",\n'
        '    "requested_add": {"path": "exact workspace-relative Java file", "description": "what type to create and why"} | null,\n'
        '    "requested_resources": [{"type": "page|endpoint|api_contract|data_source", "targetId": "stable-id"}]\n'
        "  },\n"
        '  "repair_tasks": [\n'
        "    {\n"
        '      "title": "repair task title",\n'
        '      "description": "what the code runner should fix",\n'
        '      "change_scope": [{"operation": "add|modify|delete", "path": "exact parent-authorized path", "description": "bounded change"}]\n'
        "    }\n"
        "  ],\n"
        '  "reason": "required when confirmation or terminal failure is selected",\n'
        '  "failure_handling": "how the scheduler should treat this failure"\n'
        "}\n\n"
        "For a backend Endpoint plan_mismatch where the failed task can complete its own responsibility "
        "by creating exactly one missing Java source file, choose requires_user_confirmation and put "
        "only the new file in boundaries.requested_add. Use the exact backend/src/main/java/... path "
        "from workspace evidence, specify the type, field types and responsibility in description, "
        "and provide one repair_tasks item with only the necessary parent-authorized operations. "
        "Leave requested_resources empty for this file-only repair. Set requested_add to null otherwise. "
        "If another Task owns the missing file or a dependency must change, choose terminal_failure "
        "and explain which task dependency or ownership needs correction.\n\n"
        "Choose requires_user_confirmation when repair requires expanding "
        "change_scope, changing confirmed product/API contracts, or making a "
        "user-visible product decision. Choose terminal_failure when the failure "
        "When requesting expansion, list every newly affected stable business resource "
        "in boundaries.requested_resources; never use file paths as resource IDs. "
        "is not actionable with the provided evidence or the repair budget is "
        "exhausted. Choose repair only when the repair can be delegated as one "
        "or more bounded implementation tasks.\n\n"
        "For every repair task, change_scope must list only the exact files this repair "
        "attempt needs to add, modify, or delete inside the parent authorization. Do not "
        "return acceptance_criteria, acceptance_checks, business_acceptance_checks, "
        "verification_commands, or evidence: the platform recompiles attempt-specific "
        "engineering checks and inherits the parent task's business checks exactly.\n\n"
        f"{preview_guidance}RepairPlannerInput:\n{json.dumps(repair_input, ensure_ascii=False, indent=2)}"
    )


def _invoke_repair_planner_agent(
    *,
    prompt: str,
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    diagnostic_scope: dict[str, Any] | None = None,
) -> str:
    """使用本次工作流的技能白名单调用修复规划 Deep Agent。"""

    from app.agents import create_agent_bundle

    agent = create_agent_bundle(workspace, selected_skill_names).repair_planner
    payload = {"messages": [{"role": "user", "content": prompt}]}
    if diagnostic_scope is not None:
        # 每次诊断单独记录证据摘要，不复用缓存 Agent 上次调用的探索进度。
        result = agent.invoke(payload, config={"metadata": {"preview_diagnosis": {**diagnostic_scope, "observations": {}}}})
    else:
        result = agent.invoke(payload)
    return last_agent_text(result)


@observe_model_operation("plan_build_repairs", business_stage="build")
def plan_build_failure_repair_with_repair_planner_agent(
    *,
    repair_input: dict[str, Any],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
) -> dict[str, Any]:
    """在相同技能集合下为构建失败生成受限修复计划。"""

    settings = Settings.from_env()
    agent_note = _invoke_repair_planner_agent(
        prompt=_build_failure_repair_prompt(repair_input=repair_input),
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        diagnostic_scope={"logPaths": repair_input.get("failure", {}).get("logPaths", [])} if repair_input.get("source") == "preview_startup" else None,
    )
    parsed = extract_json_object(agent_note)
    if not parsed:
        parsed = {
            "decision": "terminal_failure",
            "reason": "RepairPlanner did not return a valid JSON RepairPlan.",
            "strategy": "",
            "boundaries": {},
            "repair_tasks": [],
            "failure_handling": "stop_build",
            "raw_agent_note": agent_note,
        }
    parsed["prepared_by"] = {
        **(parsed.get("prepared_by") if isinstance(parsed.get("prepared_by"), dict) else {}),
        "agent": "repair-planner-agent",
        "mode": "deep_agent",
        "model": settings.model_name,
        "requiredSkillsLoaded": list(selected_skill_names or []),
    }
    return parsed


def plan_repairs_with_repair_planner_agent(
    *,
    test_report: dict[str, Any],
    revision_requests: list[dict[str, Any]],
    build_task_plan: dict[str, Any] | None = None,
    build_execution_scope: dict[str, Any] | None = None,
    scoped_tasks: list[dict[str, Any]] | None = None,
    repair_attempt: int = 1,
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    business_stage: str = "integration_test",
) -> dict[str, Any]:
    """在相同技能集合下为集成测试失败生成修复任务。"""

    settings = Settings.from_env()
    if not revision_requests:
        agent_note = "No repair required because all quality gate checks passed."
        planner_decision: dict[str, Any] = {"decision": "terminal_failure"}
    else:
        with model_operation(
            "plan_unit_test_repairs" if business_stage == "unit_test" else "plan_integration_repairs",
            business_stage=business_stage,
        ):
            agent_note = _invoke_repair_planner_agent(
                prompt=_test_repair_planning_prompt(
                    test_report=test_report,
                    revision_requests=revision_requests,
                    build_task_plan=build_task_plan,
                ),
                workspace=workspace,
                selected_skill_names=selected_skill_names,
            )
        planner_decision = extract_json_object(agent_note) or {
            "decision": "repair",
            "strategy": agent_note,
        }

    decision = planner_decision.get("decision")
    if decision in {"requires_user_confirmation", "terminal_failure"}:
        bounded_candidate = create_repair_task_plan(
            revision_requests=revision_requests,
            agent_note=agent_note,
            build_execution_scope=build_execution_scope,
            scoped_tasks=scoped_tasks,
            repair_attempt=repair_attempt,
        )
        return {
            "version": "0.1.0",
            "status": decision,
            "decision": decision,
            "generated_at": test_report.get("generated_at"),
            "source": "integration_test",
            "tasks": [],
            "candidateTasks": bounded_candidate.get("tasks", []),
            "planId": bounded_candidate.get("planId"),
            "requestedPaths": bounded_candidate.get("requestedPaths", []),
            "repair_scope": bounded_candidate.get("repair_scope", {}),
            "summary": {"total": 0, "frontend": 0, "backend": 0, "database": 0},
            "agent_note": agent_note,
            "reason": planner_decision.get("reason", ""),
            "escalationKind": (
                planner_decision.get("escalationKind")
                if planner_decision.get("escalationKind")
                in {"code_scope", "formal_revision"}
                else None
            ),
            "failure_handling": planner_decision.get("failure_handling", ""),
            "prepared_by": {
                "agent": "repair-planner-agent",
                "mode": "deep_agent",
                "model": settings.model_name,
                "requiredSkillsLoaded": list(selected_skill_names or []),
            },
        }

    repair_task_plan = create_repair_task_plan(
        revision_requests=revision_requests,
        agent_note=agent_note,
        build_execution_scope=build_execution_scope,
        scoped_tasks=scoped_tasks,
        repair_attempt=repair_attempt,
    )
    repair_task_plan["prepared_by"]["agent"] = "repair-planner-agent"
    repair_task_plan["prepared_by"]["model"] = settings.model_name
    repair_task_plan["prepared_by"]["requiredSkillsLoaded"] = list(
        selected_skill_names or []
    )
    repair_task_plan["planner_decision"] = planner_decision
    if planner_decision.get("strategy"):
        repair_task_plan["strategy"] = planner_decision["strategy"]
    return repair_task_plan
