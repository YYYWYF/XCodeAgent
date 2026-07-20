from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


REQUIRED_TEST_CHECKS = [
    ("frontend_install", "前端依赖安装检查"),
    ("frontend_build", "前端 TS 构建检查"),
    ("frontend_lint", "前端 lint 通过"),
    ("frontend_typecheck", "前端 typecheck 通过"),
    ("frontend_unit_tests", "前端单元测试通过"),
    ("backend_build", "后端 Java 构建检查"),
    ("backend_static_check", "后端静态检查通过"),
    ("backend_unit_tests", "后端单元测试通过"),
    ("api_contract", "API 契约有效"),
    ("joint_integration", "前后端集成测试通过"),
]


def _check_result(
    *,
    check_id: str,
    name: str,
    passed: bool,
    evidence: str,
    command: str | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "name": name,
        "passed": passed,
        "command": command,
        "evidence": evidence,
    }


def create_deterministic_test_results(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Create deterministic check results from build state.

    The runnable demo does not execute real npm/pytest commands yet.
    This function is the stable boundary where those commands will be wired in.
    For now, checks pass only when the build stage has no failed or pending tasks.
    """

    build_summary = state.get("build_summary", {})
    build_is_clean = (
        int(build_summary.get("failed", 0)) == 0
        and int(build_summary.get("pending", 0)) == 0
    )

    return [
        _check_result(
            check_id=check_id,
            name=name,
            passed=build_is_clean,
            command=None,
            evidence=(
                "Demo deterministic check passed because build summary has no failed/pending tasks."
                if build_is_clean
                else f"Build summary is not clean: {build_summary}"
            ),
        )
        for check_id, name in REQUIRED_TEST_CHECKS
    ]


def create_revision_requests(
    test_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failed_results = [result for result in test_results if not result["passed"]]
    return [
        {
            "id": f"revision:{result['id']}",
            "source": "integration_test",
            "target": "repair-planner-agent",
            "owner": _revision_owner(result["id"]),
            "owners": _revision_owners(result["id"]),
            "reason": result["name"],
            "evidence": result["evidence"],
            "failed_check": result,
            "failed_attempt": _failed_attempt(result),
            "status": "pending",
        }
        for result in failed_results
    ]


def _revision_owner(check_id: str) -> str:
    return _revision_owners(check_id)[0]


def _revision_owners(check_id: str) -> list[str]:
    if check_id.startswith("frontend_"):
        return ["frontend"]
    if check_id.startswith("backend_") or check_id == "api_contract":
        return ["data_source"]
    return ["frontend", "data_source"]


def evaluate_quality_gate(
    *,
    test_results: list[dict[str, Any]],
    agent_note: str,
) -> dict[str, Any]:
    passed = all(result["passed"] for result in test_results)
    revision_requests = create_revision_requests(test_results)
    return {
        "version": "0.1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "checks": test_results,
        "agent_note": agent_note,
        "summary": {
            "total": len(test_results),
            "passed": len([result for result in test_results if result["passed"]]),
            "failed": len([result for result in test_results if not result["passed"]]),
        },
        "needs_revision": bool(revision_requests),
        "revision_requests": revision_requests,
        "quality_gate": {
            "passed": passed,
            "required_checks": [check_id for check_id, _ in REQUIRED_TEST_CHECKS],
            "evaluated_by": "deterministic-quality-gate",
        },
    }


def create_repair_task_plan(
    *,
    revision_requests: list[dict[str, Any]],
    agent_note: str,
) -> dict[str, Any]:
    tasks = []
    for request in revision_requests:
        owners = request.get("owners") if isinstance(request.get("owners"), list) else []
        owners = owners or [request["owner"]]
        for owner in owners:
            tasks.append(
                {
                    "id": f"repair:{request['failed_check']['id']}:{owner}",
                    "task_id": f"repair:{request['failed_check']['id']}:{owner}",
                    "kind": "repair",
                    "owner": owner,
                    "description": f"修复测试失败：{request['reason']}",
                    "dependencies": [],
                    "dependsOn": [],
                    "status": "pending",
                    "source_ref": {
                        "type": "revision_request",
                        "id": request["id"],
                        "failed_check_id": request["failed_check"]["id"],
                    },
                    "allowed_paths": _repair_allowed_paths(owner),
                    "change_scope": [{"path": path} for path in _repair_allowed_paths(owner)],
                    "canRunInParallel": False,
                    "can_run_in_parallel": False,
                    "parallel_reason": "integration-test repair must run in a bounded follow-up cycle.",
                    "acceptance_criteria": [
                        f"{request['reason']} 重新执行后必须通过。",
                        "不得修改已确认需求、页面规格或 API 契约；如必须修改契约，需返回变更申请。",
                    ],
                    "failure_evidence": {
                        "evidence": request["evidence"],
                        "failed_attempt": request.get("failed_attempt", {}),
                    },
                }
            )
    return {
        "version": "0.1.0",
        "status": "ready" if tasks else "not_required",
        "decision": "repair" if tasks else "terminal_failure",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "integration_test",
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "frontend": len([task for task in tasks if task["owner"] == "frontend"]),
            "data_source": len([task for task in tasks if task["owner"] == "data_source"]),
        },
        "agent_note": agent_note,
        "prepared_by": {
            "agent": "repair-planner-agent",
            "mode": "live",
            "source": "repair_planner_test_repair_planning",
        },
    }


def _repair_allowed_paths(owner: str) -> list[str]:
    if owner == "frontend":
        return ["app/frontend/**", "app/shared/api/**", "tests/frontend/**"]
    if owner == "data_source":
        return ["app/backend/**", "app/shared/api/**", "tests/backend/**"]
    return ["app/**", "tests/**"]


def _failed_attempt(result: dict[str, Any]) -> dict[str, Any]:
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    return {
        "source": "integration_test",
        "check_id": result.get("id"),
        "check_name": result.get("name"),
        "status": "completed" if result.get("passed") else "failed",
        "failure_category": result.get("failure_category") or "test_failure",
        "command": result.get("command"),
        "execution": execution,
        "logs": {
            "stdout": execution.get("stdout_log"),
            "stderr": execution.get("stderr_log"),
        },
        "agent_note": result.get("evidence"),
    }
