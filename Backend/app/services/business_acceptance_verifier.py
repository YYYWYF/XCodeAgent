"""DAG 业务验收检查的确定性调度器。"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from app.services.business_acceptance import (
    BUSINESS_ACCEPTANCE_KINDS,
    _endpoint_expectations,
    _stable_hash,
)
from app.services.business_acceptance_verifiers.backend_domain import verify_domain_mapping_source
from app.services.business_acceptance_verifiers.backend_external_api import (
    verify_external_api_client_source,
    verify_external_api_mapping_source,
)
from app.services.business_acceptance_verifiers.backend_endpoint import verify_endpoint_source
from app.services.business_acceptance_verifiers.backend_application_service import (
    verify_application_service_source,
)
from app.services.business_acceptance_verifiers.backend_repository import verify_repository_source
from app.services.business_acceptance_verifiers.frontend_api import verify_api_contract_source
from app.services.business_acceptance_verifiers.frontend_page import verify_page_endpoint_usage_source
from app.services.business_acceptance_verifiers.frontend_static_data import (
    verify_static_data_contract_source,
)
from app.services.business_acceptance_verifiers.common import read_target_files, verification_result


Verifier = Callable[..., dict[str, Any]]

BUSINESS_VERIFIER_REGISTRY: dict[str, Verifier] = {
    "frontend.api_contract": verify_api_contract_source,
    "frontend.page_endpoint_usage": verify_page_endpoint_usage_source,
    "frontend.static_data_contract": verify_static_data_contract_source,
    "backend.domain_mapping": verify_domain_mapping_source,
    "backend.repository_contract": verify_repository_source,
    "backend.application_service_contract": verify_application_service_source,
    "backend.endpoint_contract": verify_endpoint_source,
    "backend.external_api_client_contract": verify_external_api_client_source,
    "backend.external_api_mapping_contract": verify_external_api_mapping_source,
}


class BusinessAcceptanceVerifier:
    """按固定白名单顺序执行业务检查，并汇总阻断性结果。"""

    def __init__(self, registry: dict[str, Verifier] | None = None) -> None:
        """初始化不可变语义边界内的确定性 verifier 注册表。"""

        self.registry = dict(registry or BUSINESS_VERIFIER_REGISTRY)

    def verify(
        self,
        task: dict[str, Any],
        workspace_root: str | Path | None,
        *,
        formal_artifacts: dict[str, Any] | None = None,
        dependency_evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """验证单个任务的全部业务检查，Agent 自报结果不参与裁决。"""

        raw_checks = task.get("business_acceptance_checks")
        checks = [
            item
            for item in (raw_checks if isinstance(raw_checks, list) else [])
            if isinstance(item, dict)
        ]
        evidence: list[dict[str, Any]] = []
        for check in checks:
            started_at = monotonic()
            evidence.append(
                {
                    **self._verify_check(
                        check,
                        task=task,
                        workspace_root=workspace_root,
                        formal_artifacts=formal_artifacts,
                        dependency_evidence=dependency_evidence or [],
                        prior_results=evidence,
                    ),
                    "duration_ms": round(max(monotonic() - started_at, 0.0) * 1000, 3),
                }
            )
        summary = summarize_business_acceptance(evidence)
        summary["by_kind"] = business_acceptance_kind_metrics(evidence)
        return {
            "business_acceptance_evidence": evidence,
            "business_acceptance_summary": summary,
            "status": (
                "failed"
                if summary["failed"]
                else "blocked"
                if summary["blocked"]
                else "passed"
            ),
        }

    def _verify_check(
        self,
        check: dict[str, Any],
        *,
        task: dict[str, Any],
        workspace_root: str | Path | None,
        formal_artifacts: dict[str, Any] | None,
        dependency_evidence: list[dict[str, Any]],
        prior_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """执行单项检查并将异常收敛为 blocked，不允许异常吞掉后通过。"""

        check_id = str(check.get("id") or "")
        kind = str(check.get("kind") or "")
        verifier = self.registry.get(kind)
        verification_value = check.get("verification")
        verification = verification_value if isinstance(verification_value, dict) else {}
        raw_target_paths = check.get("target_paths")
        raw_sources = check.get("sources")
        base = {
            "check_id": check_id,
            "kind": kind,
            "verifier": str(verification.get("verifier") or ""),
            "target_paths": [
                str(path)
                for path in (raw_target_paths if isinstance(raw_target_paths, list) else [])
                if str(path).strip()
            ],
            "source_hashes": [
                str(source.get("sha256") or "")
                for source in (raw_sources if isinstance(raw_sources, list) else [])
                if isinstance(source, dict)
            ],
        }
        if kind not in BUSINESS_ACCEPTANCE_KINDS or verifier is None:
            return {
                **base,
                **verification_result("blocked", f"业务检查类型未注册：{kind or '<empty>'}。"),
            }
        expected_verifier = (
            str(verification.get("verifier") or "")
            if isinstance(verification, dict)
            else ""
        )
        registered_verifier = _registered_verifier_name(kind)
        if expected_verifier != registered_verifier:
            return {
                **base,
                **verification_result(
                    "blocked",
                    f"业务检查 {check_id or '<unknown>'} 的 verifier 与 kind 不匹配。",
                ),
            }
        contract_errors = _runtime_check_contract_errors(check)
        if contract_errors:
            return {
                **base,
                **verification_result("blocked", "；".join(contract_errors)),
            }
        source_errors = _source_hash_errors(check, formal_artifacts)
        if source_errors:
            reason_code = (
                "formal_source_changed"
                if any("哈希已变化" in error for error in source_errors)
                else "formal_source_unavailable"
            )
            return {
                **base,
                **verification_result(
                    "blocked",
                    "；".join(source_errors),
                    facts={"reason_code": reason_code},
                ),
            }
        files, read_errors = read_target_files(check, workspace_root)
        if read_errors:
            return {
                **base,
                **verification_result("blocked", "；".join(read_errors)),
            }
        try:
            if kind == "frontend.page_endpoint_usage":
                result = verifier(
                    files,
                    check.get("expected") if isinstance(check.get("expected"), dict) else {},
                    dependency_evidence=[*dependency_evidence, *prior_results],
                )
            else:
                result = verifier(
                    files,
                    check.get("expected") if isinstance(check.get("expected"), dict) else {},
                )
        except Exception as exc:  # noqa: BLE001 - 验收异常必须结构化为 blocked
            result = verification_result(
                "blocked",
                f"业务检查执行异常：{type(exc).__name__}。",
            )
        return {
            **base,
            **result,
        }


def verify_business_acceptance(
    task: dict[str, Any],
    workspace_root: str | Path | None,
    formal_artifacts: dict[str, Any] | None = None,
    dependency_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """提供函数式入口，供 Build Scheduler 和单元测试直接调用。"""

    return BusinessAcceptanceVerifier().verify(
        task,
        workspace_root,
        formal_artifacts=formal_artifacts,
        dependency_evidence=dependency_evidence,
    )


def summarize_business_acceptance(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """统计业务检查通过、失败和证据不足数量。"""

    duration_total = round(
        sum(float(item.get("duration_ms") or 0.0) for item in evidence),
        3,
    )
    return {
        "total": len(evidence),
        "passed": sum(1 for item in evidence if item.get("status") == "passed"),
        "failed": sum(1 for item in evidence if item.get("status") == "failed"),
        "blocked": sum(1 for item in evidence if item.get("status") == "blocked"),
        "duration_ms_total": duration_total,
        "duration_ms_avg": round(duration_total / len(evidence), 3) if evidence else 0,
    }


def _source_hash_errors(
    check: dict[str, Any],
    formal_artifacts: dict[str, Any] | None,
) -> list[str]:
    """核对业务检查来源仍对应当前正式产物，阻止过期证据复用。"""

    if not isinstance(formal_artifacts, dict) or not formal_artifacts:
        return ["缺少当前正式产物，无法确认业务检查来源。"]
    errors: list[str] = []
    sources = check.get("sources")
    if not isinstance(sources, list) or not sources:
        return ["业务检查缺少正式来源，无法确认业务检查来源。"]
    for source in sources:
        if not isinstance(source, dict):
            errors.append("业务检查包含非法正式来源。")
            continue
        expected_hash = str(source.get("sha256") or "").strip().lower()
        if not expected_hash:
            errors.append(f"正式来源 {source.get('target_id') or '<unknown>'} 缺少哈希。")
            continue
        actual_hash = _current_source_hash(source, formal_artifacts)
        if not actual_hash:
            errors.append(
                f"无法定位正式来源 {source.get('artifact') or '<unknown>'}"
                f"/{source.get('target_id') or '<unknown>'}。"
            )
        elif actual_hash.lower() != expected_hash:
            errors.append(
                f"正式来源 {source.get('target_id') or '<unknown>'} 哈希已变化，"
                "必须重新生成 Build DAG。"
            )
    return errors


def _runtime_check_contract_errors(check: dict[str, Any]) -> list[str]:
    """在执行边界再次阻断缺少结构化输入的业务检查。"""

    errors: list[str] = []
    if not str(check.get("deliverable_id") or "").strip():
        errors.append("业务检查缺少 deliverable_id。")
    if not isinstance(check.get("sources"), list) or not check.get("sources"):
        errors.append("业务检查缺少正式 sources。")
    if not isinstance(check.get("expected"), dict):
        errors.append("业务检查缺少结构化 expected。")
    if not isinstance(check.get("target_paths"), list) or not check.get("target_paths"):
        errors.append("业务检查缺少 target_paths。")
    if check.get("required") is not True:
        errors.append("业务检查 required 必须为 true。")
    if check.get("verification_stage") != "build":
        errors.append("业务检查 verification_stage 必须为 build。")
    return errors


def _current_source_hash(source: dict[str, Any], formal: dict[str, Any]) -> str:
    """从 hydrated ProjectPlan 提取来源切片并计算与编译器一致的哈希。"""

    artifact = str(source.get("artifact") or "")
    target_id = str(source.get("target_id") or "")
    if artifact == "entity_design":
        detail = next(
            (
                item
                for item in _formal_items(formal, "entity_detail_plans")
                if str(item.get("entity_id") or "") == target_id
            ),
            {},
        )
        return _stable_hash(detail) if detail else ""
    if artifact == "page_implementation_contract":
        contract = next(
            (
                item
                for item in _formal_items(formal, "page_implementation_contracts")
                if str(item.get("pageId") or "") == target_id
            ),
            {},
        )
        return _stable_hash(contract) if contract else ""
    if artifact == "endpoint_detail":
        detail = next(
            (
                item
                for item in _formal_items(formal, "endpoint_detail_plans")
                if str(item.get("endpoint_id") or "") == target_id
            ),
            {},
        )
        reference_hash = _endpoint_reference_hash(formal, target_id)
        return reference_hash or (_stable_hash(detail) if detail else "")
    if artifact == "api_contract":
        contract_id = _pointer_contract_id(str(source.get("pointer") or ""))
        contract = next(
            (
                item
                for item in _formal_items(formal, "api_contracts")
                if str(item.get("id") or "") == contract_id
            ),
            {},
        )
        if not contract:
            return ""
        endpoint_ids = [target_id]
        endpoints = _endpoint_expectations(
            {
                "contracts": [contract],
                "endpoint_ids": endpoint_ids,
            }
        )
        endpoint = next(
            (item for item in endpoints if str(item.get("endpoint_id") or "") == target_id),
            {},
        )
        payload = {
            "contract_id": contract_id,
            "endpoint": endpoint,
            "schemas": deepcopy(contract.get("schemas"))
            if isinstance(contract.get("schemas"), dict)
            else {},
        }
        return _stable_hash(payload) if endpoint else ""
    return ""


def _endpoint_reference_hash(formal: dict[str, Any], endpoint_id: str) -> str:
    """读取外置 EndpointDetail 当前引用哈希。"""

    for contract in _formal_items(formal, "api_contracts"):
        for endpoint in _dict_items(contract.get("endpoints")):
            if str(endpoint.get("id") or "") != endpoint_id:
                continue
            reference = endpoint.get("detail_design")
            if isinstance(reference, dict) and reference.get("sha256"):
                return str(reference["sha256"])
    return ""


def _pointer_contract_id(pointer: str) -> str:
    """从 API Contract JSON Pointer 读取契约标识。"""

    match = re.search(r"/api_contracts/([^/]+)/endpoints/", pointer)
    return match.group(1) if match else ""


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """读取列表中的字典项，避免不可信正式输入向下传播。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _formal_items(formal: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """读取当前 ProjectPlan 的正式列表，必要时使用同一当前上下文的完整投影。"""

    items = _dict_items(formal.get(key))
    if items:
        return items
    executable = formal.get("executable_details")
    return _dict_items(executable.get(key)) if isinstance(executable, dict) else []


def _registered_verifier_name(kind: str) -> str:
    """读取 kind 对应的固定 verifier 名称，拒绝模型自定义执行器。"""

    from app.services.business_acceptance import BUSINESS_VERIFIER_NAMES

    return str(BUSINESS_VERIFIER_NAMES.get(kind) or "")


def business_acceptance_kind_metrics(
    evidence: list[dict[str, Any]],
) -> dict[str, dict[str, int | float]]:
    """按检查类型汇总通过、失败和 blocked 指标，便于观测。"""

    result: dict[str, dict[str, int | float]] = {}
    for item in evidence:
        kind = str(item.get("kind") or "unknown")
        counts = result.setdefault(
            kind,
            {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "blocked": 0,
                "duration_ms_total": 0.0,
                "duration_ms_avg": 0.0,
            },
        )
        counts["total"] += 1
        counts["duration_ms_total"] = round(
            float(counts["duration_ms_total"]) + float(item.get("duration_ms") or 0.0),
            3,
        )
        status = str(item.get("status") or "blocked")
        if status not in counts:
            status = "blocked"
        counts[status] += 1
        counts["duration_ms_avg"] = round(
            float(counts["duration_ms_total"]) / int(counts["total"]),
            3,
        )
    return result
