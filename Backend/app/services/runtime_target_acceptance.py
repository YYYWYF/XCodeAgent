"""已确认 DAG 下的一次性真实目标验收与持久化完成证据。"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.runtime_agent_contracts import agent_fact_digest
from app.services.runtime_endpoint_dependencies import resolve_runtime_target
from app.services.runtime_endpoint_contracts import runtime_endpoint_capability_snapshot, runtime_endpoint_identity

QUESTION = "这是一次连接验收。请简短说明你能够提供的帮助。"
_LOCK = RLock()


def target_acceptance_plan(plan: dict, scope: dict) -> dict | None:
    """把真实调用次数、固定问题及期限写入 Pending，用户确认 DAG 才授权执行。"""
    kind = scope.get("type")
    if kind not in {"agent", "endpoint", "page", "application"}:
        return None
    resolved = resolve_runtime_target(plan, kind, scope.get("targetId", ""), scope.get("apiContractId"))
    if not resolved["agent_ids"]:
        return None
    from app.services.runtime_execution_policy import execution_capabilities
    from app.services.runtime_agent_contracts import agent_contract_index
    agents = agent_contract_index(plan)
    return {"schemaVersion": "runtime-target-acceptance.v1", "scope": dict(scope),
        "cases": [{"agentId": identity, "capability": runtime_endpoint_capability_snapshot(plan, *runtime_endpoint_identity(identity)),
                   "mode": "page" if kind == "page" else "endpoint",
                   "modelRequestLimit": execution_capabilities(agents[identity])["modelRequests"]} for identity in sorted(resolved["agent_ids"])],
        "question": QUESTION, "conversationRunsPerCase": 1, "automaticRetries": 0,
        "budgets": {"runtimeStartupSeconds": 60, "frontendStartupSeconds": 90, "runSeconds": 120,
                    "cleanupSeconds": 10, "attemptSeconds": 300}}


def acceptance_fingerprint(root: Path, formal: dict, specification: dict, *, cached_desktop: str | None = None) -> str:
    """绑定实际实现、正式能力、当前验证器、目标页面源码及模型配置，摘要不公开凭据。"""
    from app.config import Settings
    from app.services.runtime_execution_policy import compile_execution_policies
    from app.services.runtime_agent_contracts import agent_contract_index
    from app.services.page_identity import canonical_page_entry_path
    settings = Settings.from_env()
    policies = compile_execution_policies(root, formal)
    required = {case["agentId"] for case in specification["cases"]}
    if not required <= set(policies):
        raise ValueError("目标 Agent 尚未实际注册，不能验收或登记完成。")
    verifier_files = [Path(__file__), Path(__file__).with_name("runtime_acceptance_frontend.py"),
        Path(__file__).with_name("runtime_acceptance_bridge.py")]
    desktop = Path(__file__).resolve().parents[3] / "Frontend/src/main/runtimeAcceptanceWorker.ts"
    from app.services.runtime_acceptance_bridge import desktop_verifier_sha256
    actual_desktop = desktop_verifier_sha256(required=cached_desktop is None) or cached_desktop
    if desktop.is_file():
        if sha256(desktop.read_bytes()).hexdigest() != actual_desktop:
            raise ValueError("Electron 正在使用旧版目标验证器，请重启当前桌面应用。")
    facts = {"specification": specification, "policies": {key: policies[key] for key in sorted(required)},
        "agentInputs": {key: agent_contract_index(formal)[key] for key in sorted(required)},
        "model": {"provider": settings.model_provider, "name": settings.model_api_name, "base": settings.model_base_url,
                  "keyDigest": sha256((settings.model_api_key or "").encode()).hexdigest()},
        "verifier": {**{path.name: sha256(path.read_bytes()).hexdigest() for path in verifier_files}, "desktop": actual_desktop}}
    if specification["scope"]["type"] == "page":
        paths = [canonical_page_entry_path(specification["scope"]["targetId"]),
            "frontend/src/apis/agentConversationRuntime.ts", "frontend/src/components/AgentConversation/AgentChatCore.tsx"]
        facts["page"] = {path: sha256((root / path).read_bytes()).hexdigest() for path in paths}
    return agent_fact_digest(facts)


def _store(path: Path, record: dict):
    """每个不可逆阶段前落盘并 fsync，进程崩溃后也不会重新消费同一预算。"""
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def ensure_runtime_target_acceptance(workspace: str | Path, state: dict) -> dict | None:
    """同轮 Build、单测成功后执行一次；失败、取消或未知均保留消费事实并阻止完成登记。"""
    from app.services.python_runtime_checks import runtime_check_plan
    from app.workspace.task_documents import build_task_plan_sha256
    root = Path(workspace).resolve()
    formal = runtime_check_plan(root, {})
    if formal is None:
        return None
    plan, scope = state.get("build_task_plan") or {}, state.get("build_execution_scope") or {}
    specification = target_acceptance_plan(formal, scope)
    if specification is None:
        return None
    if (plan.get("confirmation_status") != "confirmed" or plan.get("targetAcceptance") != specification
            or plan.get("build_execution_scope") != scope or state.get("unit_test_gate_passed") is not True
            or state.get("build_run_plan_sha256") != build_task_plan_sha256(plan) or not state.get("build_run_id")):
        raise ValueError("目标验收缺少当前同轮已确认 DAG 和单测证据。")
    fingerprint = acceptance_fingerprint(root, formal, specification)
    # 同一确认 DAG 只有一个预算，恢复节点或另一个 Workflow run 不会产生第二次调用。
    identity = sha256(build_task_plan_sha256(plan).encode()).hexdigest()
    directory = root / WORKSPACE_ARTIFACT_DIR / "runtime" / "acceptance" / identity
    path = directory / "result.json"
    with _LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        if path.exists():
            previous = json.loads(path.read_text())
            if previous.get("status") == "passed" and previous.get("fingerprint") == fingerprint:
                return previous
            # 当前记录明确证明尚未签发调用，用户主动重试可重新准备；未知结果不能重放。
            retry_preparation = (previous.get("status") == "failed_before_dispatch"
                                 and previous.get("fingerprint") == fingerprint
                                 and all(case.get("budgetConsumed") is False for case in previous.get("cases", [])))
            if not retry_preparation:
                raise ValueError("当前已确认 DAG 的目标验收预算已消费或输入已变化；不会自动重放，请重新规划并明确确认新一轮验收。")
        record = {"status": "prepared", "fingerprint": fingerprint, "dagSha256": build_task_plan_sha256(plan),
                  "buildRunId": state["build_run_id"], "cases": [], "attemptId": uuid4().hex, "specification": specification}
        _store(path, record)
        record["status"] = "running"
        _store(path, record)
    started = monotonic()
    try:
        from app.services.agent_runtime_launcher import check_runtime_target
        for case in specification["cases"]:
            if monotonic() - started > 170:
                raise ValueError("目标验收剩余时间不足，不提交下一次供应商调用。")
            case_record = {"agentId": case["agentId"], "budgetConsumed": False, "status": "preparing"}
            record["cases"].append(case_record)
            _store(path, record)

            def consume_budget() -> None:
                """在实际签发桌面调用之前持久化消费事实，签发后任何失败均不可重放。"""
                case_record.update(budgetConsumed=True, status="running")
                _store(path, record)

            evidence = check_runtime_target(root, directory / case["agentId"], case=case, specification=specification,
                deadline=started + 290, before_dispatch=consume_budget)
            case_record.update(status="passed", evidence=evidence)
            _store(path, record)
        current_formal = runtime_check_plan(root, {})
        if current_formal is None or target_acceptance_plan(current_formal, scope) != specification or acceptance_fingerprint(root, current_formal, specification) != fingerprint:
            raise ValueError("目标验收期间实际输入已变化，不能提交完成。")
        if monotonic() - started > 300:
            raise ValueError("目标验收超过整体期限，不能提交完成。")
        record["status"] = "passed"
        from app.services.runtime_acceptance_bridge import desktop_verifier_sha256
        record["desktopVerifierSha256"] = desktop_verifier_sha256()
        record["agentFingerprints"] = {case["agentId"]: acceptance_fingerprint(root, current_formal,
            target_acceptance_plan(current_formal, {"type": "agent", "targetId": case["agentId"]})) for case in specification["cases"]}
        _store(path, record)
        return record
    except Exception:
        # 不落原始异常以免网络库把 URL、供应商正文或凭据写入证据。
        record["status"] = ("failed_or_unknown" if any(case.get("budgetConsumed") for case in record["cases"])
                            else "failed_before_dispatch")
        _store(path, record)
        raise


def require_runtime_target_evidence(root: Path, state: dict) -> dict | None:
    """完成登记只读消费本轮有效证据，绝不在生命周期写锁中触发模型调用。"""
    from app.services.python_runtime_checks import runtime_check_plan
    from app.workspace.task_documents import build_task_plan_sha256
    formal = runtime_check_plan(root, {})
    if formal is None:
        return None
    plan = state.get("build_task_plan") or {}
    specification = target_acceptance_plan(formal, state.get("build_execution_scope") or {})
    if specification is None:
        return None
    identity = sha256(build_task_plan_sha256(plan).encode()).hexdigest()
    path = root / WORKSPACE_ARTIFACT_DIR / "runtime/acceptance" / identity / "result.json"
    record = json.loads(path.read_text()) if path.is_file() else {}
    if (record.get("status") != "passed" or record.get("fingerprint") != acceptance_fingerprint(root, formal, specification)
            or record.get("buildRunId") != state.get("build_run_id")
            or state.get("build_run_plan_sha256") != build_task_plan_sha256(plan)):
        raise ValueError("当前目标缺少同轮、输入有效且清理成功的真实验收证据。")
    return record
