from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
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
    if check_id.startswith("backend_"):
        return ["data_source"]
    return ["frontend", "data_source"]


def evaluate_quality_gate(
    *,
    test_results: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = all(result["passed"] for result in test_results)
    revision_requests = create_revision_requests(test_results)
    default_required_ids = {check_id for check_id, _ in REQUIRED_TEST_CHECKS}
    required_checks = [
        str(result.get("id") or "")
        for result in test_results
        if result.get("id")
        and bool(
            result.get(
                "required",
                str(result.get("id") or "") in default_required_ids,
            )
        )
    ]
    return {
        "version": "0.1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "checks": test_results,
        "summary": {
            "total": len(test_results),
            "passed": len([result for result in test_results if result["passed"]]),
            "failed": len([result for result in test_results if not result["passed"]]),
        },
        "needs_revision": bool(revision_requests),
        "revision_requests": revision_requests,
        "quality_gate": {
            "passed": passed,
            "required_checks": required_checks,
            "evaluated_by": "deterministic-quality-gate",
        },
    }


def create_repair_task_plan(
    *,
    revision_requests: list[dict[str, Any]],
    agent_note: str,
    build_execution_scope: dict[str, Any] | None = None,
    scoped_tasks: list[dict[str, Any]] | None = None,
    repair_attempt: int = 1,
) -> dict[str, Any]:
    """把测试失败编译为当前执行切片内、具有精确授权路径的修复任务。"""

    repair_scope = _repair_scope(build_execution_scope)
    requested_paths_by_owner = {
        owner: _repair_allowed_paths(owner, scoped_tasks or [])
        for owner in ("frontend", "backend", "database")
    }
    plan_id = _repair_plan_id(
        revision_requests,
        repair_scope,
        requested_paths_by_owner,
    )
    tasks = []
    for request in revision_requests:
        owners = request.get("owners") if isinstance(request.get("owners"), list) else []
        owners = owners or [request["owner"]]
        for owner in owners:
            requested_paths = requested_paths_by_owner.get(owner, [])
            # scoped_tasks 可能为空（全部 already_satisfied），或 allowed_paths
            # 与修复操作无关（如 pnpm add 不涉及文件编辑）。此时回退为
            # 哨兵路径，让修复 agent 能执行命令级修复。
            if not requested_paths:
                requested_paths = [
                    "<no file paths — repair is a command-level operation>"
                ]
            repair_unit_id = _repair_task_unit_id(
                owner,
                repair_scope,
                scoped_tasks or [],
            )
            task_id = (
                f"repair:{plan_id}:{max(repair_attempt, 1)}:"
                f"{request['failed_check']['id']}:{owner}"
            )
            tasks.append(
                {
                    "id": task_id,
                    "kind": "repair",
                    "repair_attempt": max(repair_attempt, 1),
                    "owner": owner,
                    "unit_id": repair_unit_id,
                    "description": f"修复测试失败：{request['reason']}",
                    "dependencies": [],
                    "status": "pending",
                    "source_ref": {
                        "type": "revision_request",
                        "id": request["id"],
                        "failed_check_id": request["failed_check"]["id"],
                    },
                    "allowed_paths": requested_paths,
                    "target_files": requested_paths,
                    "change_scope": [
                        {
                            "operation": "modify",
                            "path": path,
                            "description": f"修复 {request['failed_check']['id']} 的测试失败。",
                        }
                        for path in requested_paths
                    ],
                    "repair_scope": {
                        **repair_scope,
                        "unit_id": repair_unit_id,
                        "planId": plan_id,
                        "requestedPaths": requested_paths,
                        "reason": request["reason"],
                    },
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
        "planId": plan_id,
        "requestedPaths": sorted(
            {
                path
                for paths in requested_paths_by_owner.values()
                for path in paths
            }
        ),
        "repair_scope": repair_scope,
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "frontend": len([task for task in tasks if task["owner"] == "frontend"]),
            "backend": len([task for task in tasks if task["owner"] == "backend"]),
            "database": len([task for task in tasks if task["owner"] == "database"]),
        },
        "agent_note": agent_note,
        "prepared_by": {
            "agent": "repair-planner-agent",
            "mode": "live",
            "source": "repair_planner_test_repair_planning",
        },
    }


def _repair_allowed_paths(owner: str, scoped_tasks: list[dict[str, Any]]) -> list[str]:
    """从当前切片同 owner 任务继承真实授权路径，禁止生成宿主模板路径。"""

    paths: list[str] = []
    for task in scoped_tasks:
        if task.get("owner") != owner:
            continue
        paths.extend(
            str(path)
            for path in task.get("allowed_paths", [])
            if str(path).strip()
        )
        paths.extend(
            str(change.get("path"))
            for change in task.get("change_scope", [])
            if isinstance(change, dict) and change.get("path")
        )
        paths.extend(
            str(path) for path in task.get("target_files", []) if str(path).strip()
        )
    return list(dict.fromkeys(paths))


def _repair_scope(build_execution_scope: dict[str, Any] | None) -> dict[str, str]:
    """把页面、数据源、endpoint 或应用执行范围映射为稳定 Unit ID。"""

    scope = build_execution_scope if isinstance(build_execution_scope, dict) else {}
    scope_type = str(scope.get("type") or "application")
    target_id = str(scope.get("targetId") or scope.get("target_id") or "").strip()
    if scope_type == "page" and target_id:
        unit_id = f"page:{target_id}"
    elif scope_type == "data_source" and target_id:
        unit_id = f"database:{target_id}"
    elif scope_type == "endpoint" and target_id:
        api_contract_id = str(
            scope.get("apiContractId") or scope.get("api_contract_id") or ""
        ).strip()
        unit_id = f"backend:endpoint:{api_contract_id}:{target_id}" if api_contract_id else ""
        if not unit_id:
            scope_type = "application"
            unit_id = "application:root"
    else:
        scope_type = "application"
        unit_id = "application:root"
    return {"type": scope_type, "targetId": target_id, "unit_id": unit_id}


def _repair_task_unit_id(
    owner: str,
    repair_scope: dict[str, str],
    scoped_tasks: list[dict[str, Any]],
) -> str:
    """按修复 owner 选择页面 Unit 或切片中的对应数据源 Unit。"""

    owner_unit_ids = [
        str(task.get("unit_id") or "")
        for task in scoped_tasks
        if task.get("owner") == owner and task.get("unit_id")
    ]
    if owner == "database":
        database_unit = next(
            (unit_id for unit_id in owner_unit_ids if unit_id.startswith("database:")),
            "",
        )
        if database_unit:
            return database_unit
    if owner == "backend":
        endpoint_unit = next(
            (unit_id for unit_id in owner_unit_ids if unit_id.startswith("backend:endpoint:")),
            "",
        )
        if endpoint_unit:
            return endpoint_unit
    if owner == "frontend" and repair_scope.get("type") == "page":
        return repair_scope["unit_id"]
    return owner_unit_ids[0] if owner_unit_ids else repair_scope["unit_id"]


def _repair_plan_id(
    revision_requests: list[dict[str, Any]],
    repair_scope: dict[str, str],
    requested_paths_by_owner: dict[str, list[str]],
) -> str:
    """根据失败证据、范围和授权路径生成稳定修复计划 ID。"""

    payload = {
        "revision_requests": revision_requests,
        "repair_scope": repair_scope,
        "requested_paths_by_owner": requested_paths_by_owner,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()[:16]


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
