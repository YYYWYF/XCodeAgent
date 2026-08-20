from __future__ import annotations

import json
from typing import Any

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
        "requirements, PageDetail, or API contracts. Do not edit files or mutate "
        "workflow state. Base the plan on stdout_tail/stderr_tail or readable virtual "
        "workspace logs. If evidence is unavailable, choose terminal_failure rather "
        "than guessing a root cause.\n\n"
        "For frontend Jest or backend JUnit/Surefire failures, explicitly classify the "
        "failure as test-code (mock, fixture, async wait, expectation, discovery), "
        "production-code behavior, or toolchain before choosing repair. The scoped task "
        "may include only the exact generated test files and their mapped production "
        "sources. Never delete a valid test, weaken an assertion, or add skip/disabled "
        "markers.\n\n"
        "For frontend_install, frontend_build, backend_build, backend_static_check, and "
        "other toolchain failures, choose repair when the fix stays inside the already "
        "authorized project directories (frontend and backend), including package.json, "
        "lockfiles, tsconfig, vite config, pom.xml, and application configuration. Do not "
        "request scope expansion for files inside these directories.\n\n"
        "Return only one JSON object using this contract:\n"
        "{\n"
        '  "decision": "repair" | "requires_user_confirmation" | "terminal_failure",\n'
        '  "strategy": "short repair strategy",\n'
        '  "reason": "required for confirmation or terminal failure",\n'
        '  "failure_handling": "how the workflow should handle this failure"\n'
        "}\n\n"
        "Choose requires_user_confirmation only when repair requires expanding product scope, "
        "changing confirmed requirements, or making a product decision. Choose "
        "terminal_failure when evidence is insufficient or the failure is not "
        "automatically actionable.\n\n"
        f"TestReport:\n{json.dumps(test_report, ensure_ascii=False, indent=2)}\n\n"
        f"RevisionRequests:\n{json.dumps(revision_requests, ensure_ascii=False, indent=2)}\n\n"
        f"CurrentBuildTaskPlan:\n{json.dumps(build_task_plan or {}, ensure_ascii=False, indent=2)}"
    )


def _build_failure_repair_prompt(*, repair_input: dict[str, Any]) -> str:
    """构造包含结构化业务资源扩展协议的构建失败修复提示。"""

    return (
        "You are the RepairPlanner Agent for a build scheduler.\n"
        "This is a planning-only DeepAgent node. Do not edit files, do not run code, "
        "do not mutate scheduler state, and do not rewrite the DAG directly. "
        "Use the provided input to decide whether a bounded repair task can be "
        "created for the failed build task.\n\n"
        "Return only one JSON object using this contract:\n"
        "{\n"
        '  "decision": "repair" | "requires_user_confirmation" | "terminal_failure",\n'
        '  "strategy": "short repair strategy",\n'
        '  "boundaries": {\n'
        '    "change_scope_policy": "must stay within input.change_scope",\n'
        '    "allowed_paths_policy": "must stay within input.change_scope.allowed_paths",\n'
        '    "contract_policy": "do not change confirmed requirements, ProjectPlan, or API contracts",\n'
        '    "requested_resources": [{"type": "page|endpoint|api_contract|data_source", "targetId": "stable-id"}]\n'
        "  },\n"
        '  "repair_tasks": [\n'
        "    {\n"
        '      "title": "repair task title",\n'
        '      "description": "what the code runner should fix",\n'
        '      "change_scope": [{"operation": "add|modify|delete", "path": "exact parent-authorized path", "description": "bounded change"}],\n'
        '      "acceptance_criteria": []\n'
        "    }\n"
        "  ],\n"
        '  "reason": "required when confirmation or terminal failure is selected",\n'
        '  "failure_handling": "how the scheduler should treat this failure"\n'
        "}\n\n"
        "Choose requires_user_confirmation when repair requires expanding "
        "change_scope, changing confirmed product/API contracts, or making a "
        "user-visible product decision. Choose terminal_failure when the failure "
        "When requesting expansion, list every newly affected stable business resource "
        "in boundaries.requested_resources; never use file paths as resource IDs. "
        "is not actionable with the provided evidence or the repair budget is "
        "exhausted. Choose repair only when the repair can be delegated as one "
        "or more bounded implementation tasks.\n\n"
        "For every repair task, change_scope must list only the exact files this repair "
        "attempt needs to add, modify, or delete inside the parent authorization. Keep "
        "acceptance_criteria empty: the scheduler recompiles attempt-specific file checks "
        "and inherits the parent task's deterministic outcome checks.\n\n"
        f"RepairPlannerInput:\n{json.dumps(repair_input, ensure_ascii=False, indent=2)}"
    )


def _invoke_repair_planner_agent(
    *,
    prompt: str,
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
) -> str:
    """使用本次工作流的技能白名单调用修复规划 Deep Agent。"""

    from app.agents import create_agent_bundle

    result = create_agent_bundle(workspace, selected_skill_names).repair_planner.invoke(
        {"messages": [{"role": "user", "content": prompt}]}
    )
    return last_agent_text(result)


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
) -> dict[str, Any]:
    """在相同技能集合下为集成测试失败生成修复任务。"""

    settings = Settings.from_env()
    if not revision_requests:
        agent_note = "No repair required because all quality gate checks passed."
        planner_decision: dict[str, Any] = {"decision": "terminal_failure"}
    else:
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
