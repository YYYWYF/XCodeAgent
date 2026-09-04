"""从 confirmed DAG、正式合同和平台证据计算只读 ReuseFacts，不决定生成范围。"""

from collections.abc import Iterable, Mapping
from hashlib import sha256
import json
from typing import Any

from app.services.build_task_reuse_contracts import ExternalCapability, RetainedEndpointOwner, ReuseFacts
from app.services.planning_frozen import plain_json
from app.services.planning_issues import IssueCategory, ValidationIssue


_IMPLEMENTATION_CHECKS = {"frontend.api_contract", "frontend.static_data_contract"}


def _identity(value: Any) -> bool:
    """只接受已有精确字符串身份，不补 ID、转大小写或修剪后猜测匹配。"""

    return isinstance(value, str) and bool(value) and value == value.strip()


def _issue(
    code: str, message: str, *, units: Iterable[str] = (), tasks: Iterable[str] = (),
    category: IssueCategory = "platform", **details: Any,
) -> ValidationIssue:
    """在规则命中处构造不可重试问题，基线冲突不进入模型内容重试。"""

    return ValidationIssue(
        code=code, level="pre_generation", category=category,
        unit_ids=sorted(set(units)), task_ids=sorted(set(tasks)),
        retryable=False, retry_unit_ids=(), message=message, details=details,
    )


def _task_issue(task: Mapping, code: str, message: str, **details: Any) -> ValidationIssue:
    """将基线规则明确归因到当前 Task 与 Unit，不依赖诊断文本解析。"""

    return _issue(code, message, units=[task["unit_id"]], tasks=[task["id"]], **details)


def _baseline_tasks(
    plan: Mapping[str, Any] | None, units: set[str], issues: list[ValidationIssue],
) -> list[Mapping]:
    """读取完整 confirmed registry；执行状态不参与，非法身份报错而非补齐或静默丢弃。"""

    if plan is None:
        return []
    graph = plan.get("task_graph")
    validation = graph.get("validation") if isinstance(graph, Mapping) else None
    registry = plan.get("task_registry")
    if (
        plan.get("confirmation_status") != "confirmed"
        or plan.get("schema_version") != "build-dag.v3"
        or plan.get("status") == "failed"
        or not isinstance(validation, Mapping) or validation.get("is_valid") is not True
        or not isinstance(registry, Mapping)
    ):
        issues.append(_issue("CONFIRMED_BASELINE_INVALID", "输入必须是正式 confirmed 且有效的 v3 DAG。"))
        return []
    tasks = []
    for task_id, task in sorted(registry.items()):
        if (
            not _identity(task_id) or not isinstance(task, Mapping)
            or task.get("id") != task_id or not _identity(task.get("unit_id"))
        ):
            issues.append(_issue(
                "CONFIRMED_TASK_IDENTITY_INVALID", "正式任务缺失身份或与 registry key 不一致。",
                tasks=[task_id] if _identity(task_id) else (),
            ))
            continue
        unit_id = task["unit_id"]
        if unit_id not in units:
            issues.append(_issue(
                "CONFIRMED_UNIT_MISSING", "正式任务所属 Unit 不在当前骨架中，不能静默删除历史任务。",
                units=[unit_id], tasks=[task_id],
            ))
        tasks.append(task)
    return tasks


def _formal_endpoints(formal_plan: Mapping[str, Any], issues: list[ValidationIssue]) -> set[tuple[str, str]]:
    """使用完整正式 API 合同目录建立精确复合身份，不从 HTTP 路径或描述推断。"""

    identities: set[tuple[str, str]] = set()
    contracts = formal_plan.get("api_contracts", [])
    if not isinstance(contracts, (list, tuple)):
        issues.append(_issue("FORMAL_ENDPOINT_IDENTITY_INVALID", "正式 api_contracts 必须为数组。", category="input"))
        return identities
    for contract in contracts:
        if (
            not isinstance(contract, Mapping) or not _identity(contract.get("id"))
            or not isinstance(contract.get("endpoints"), (list, tuple))
        ):
            issues.append(_issue(
                "FORMAL_ENDPOINT_IDENTITY_INVALID", "正式 API 合同缺少明确身份或 Endpoint 数组。",
                category="input",
            ))
            continue
        for endpoint in contract["endpoints"]:
            if not isinstance(endpoint, Mapping) or not _identity(endpoint.get("id")):
                issues.append(_issue(
                    "FORMAL_ENDPOINT_IDENTITY_INVALID", "正式 Endpoint 缺少明确身份。",
                    category="input", api_contract_id=contract["id"],
                ))
                continue
            key = (contract["id"], endpoint["id"])
            if key in identities:
                issues.append(_issue(
                    "FORMAL_ENDPOINT_IDENTITY_CONFLICT", "正式目录存在重复 Endpoint 身份。",
                    category="input", api_contract_id=key[0], endpoint_id=key[1],
                ))
            identities.add(key)
    return identities


def _capabilities(task: Mapping, unit_ids: set[str], issues: list[ValidationIssue]) -> set[str]:
    """只收集显式 capability；编译器默认填入的裸 Unit ID 不能代表整个 Unit 职责。"""

    declarations = [task.get("provides_capabilities", [])]
    deliverables = task.get("deliverables", [])
    if not isinstance(deliverables, (list, tuple)):
        deliverables = [None]
    for deliverable in deliverables:
        declarations.append(deliverable.get("provides", []) if isinstance(deliverable, Mapping) else None)
    result: set[str] = set()
    for values in declarations:
        if not isinstance(values, (list, tuple)) or any(not _identity(value) for value in values):
            issues.append(_task_issue(
                task, "CONFIRMED_CAPABILITY_INVALID", "正式任务的 capability 声明必须是精确非空身份数组。",
            ))
            continue
        result.update(value for value in values if value not in unit_ids)
    return result


def _endpoint_owners(
    tasks: list[Mapping], formal_keys: set[tuple[str, str]], issues: list[ValidationIssue],
) -> list[RetainedEndpointOwner]:
    """从平台编译的业务检查提取正式实现身份；Repair 与页面调用检查不是新 owner。"""

    owners: list[RetainedEndpointOwner] = []
    by_endpoint: dict[tuple[str, str], list[RetainedEndpointOwner]] = {}
    for task in tasks:
        if task.get("owner") != "frontend" or task.get("kind") == "repair":
            continue
        checks = task.get("business_acceptance_checks", [])
        if not isinstance(checks, (list, tuple)):
            issues.append(_task_issue(task, "CONFIRMED_ENDPOINT_IDENTITY_INVALID", "正式业务检查必须为数组。"))
            continue
        task_keys: set[tuple[str, str]] = set()
        for check in checks:
            if not isinstance(check, Mapping):
                issues.append(_task_issue(task, "CONFIRMED_ENDPOINT_IDENTITY_INVALID", "正式业务检查必须为对象。"))
                continue
            if check.get("kind") not in _IMPLEMENTATION_CHECKS:
                continue
            expected = check.get("expected")
            endpoints = expected.get("endpoints") if isinstance(expected, Mapping) else None
            if not isinstance(endpoints, (list, tuple)) or not endpoints:
                endpoints = [None]
            for endpoint in endpoints:
                if not isinstance(endpoint, Mapping) or not all(
                    _identity(endpoint.get(field)) for field in ("api_contract_id", "endpoint_id")
                ):
                    issues.append(_task_issue(
                        task, "CONFIRMED_ENDPOINT_IDENTITY_INVALID", "实现 owner 必须明确声明正式 API 合同和 Endpoint 身份。",
                    ))
                    continue
                key = (endpoint["api_contract_id"], endpoint["endpoint_id"])
                if key not in formal_keys:
                    issues.append(_task_issue(
                        task, "CONFIRMED_ENDPOINT_NOT_IN_FORMAL_CONTRACTS", "实现 owner 的复合身份不在完整正式合同目录中。",
                        api_contract_id=key[0], endpoint_id=key[1],
                    ))
                    continue
                task_keys.add(key)
        for contract_id, endpoint_id in sorted(task_keys):
            owner = RetainedEndpointOwner(
                api_contract_id=contract_id, endpoint_id=endpoint_id,
                owner_task_id=task["id"], owner_unit_id=task["unit_id"],
            )
            owners.append(owner)
            by_endpoint.setdefault((contract_id, endpoint_id), []).append(owner)
    for (contract_id, endpoint_id), records in sorted(by_endpoint.items()):
        if len(records) > 1:
            issues.append(_issue(
                "CONFIRMED_ENDPOINT_OWNER_CONFLICT", "confirmed baseline 中同一 Endpoint 存在多个实现 owner，需平台处理。",
                units=[record.owner_unit_id for record in records], tasks=[record.owner_task_id for record in records],
                api_contract_id=contract_id, endpoint_id=endpoint_id,
            ))
    return sorted(owners, key=lambda owner: (owner.api_contract_id, owner.endpoint_id, owner.owner_task_id))


def _external_capabilities(
    units: set[str], build_context: Mapping, workspace_snapshot: Mapping,
    template_readiness: Mapping | None, issues: list[ValidationIssue],
) -> list[ExternalCapability]:
    """仅将真实模板只读门禁结果转为 shell 外部能力；文件扫描线索不证明已满足。"""

    if "frontend:shell" not in units or template_readiness is None:
        return []
    errors = template_readiness.get("errors")
    if template_readiness.get("ready") is not True or not isinstance(errors, (list, tuple)) or errors:
        if "frontend:shell" in build_context.get("required_unit_ids", []):
            issues.append(_issue(
                "WORKSPACE_TEMPLATE_NOT_READY", "当前范围需要的模板前置能力未通过只读门禁。",
                units=["frontend:shell"], category="input",
            ))
        return []
    revision = workspace_snapshot.get("workspace_revision")
    variant = template_readiness.get("templateVariant")
    manifest = template_readiness.get("manifest")
    if (
        not _identity(revision) or variant not in {"main", "auth"}
        or not isinstance(manifest, Mapping) or not manifest
    ):
        issues.append(_issue(
            "WORKSPACE_EVIDENCE_IDENTITY_MISSING", "模板能力证据缺少 snapshot revision、模板变体或 manifest。",
            units=["frontend:shell"], category="input",
        ))
        return []
    if build_context.get("template_variant", variant) != variant:
        issues.append(_issue(
            "WORKSPACE_TEMPLATE_VARIANT_MISMATCH", "模板只读检查与当前 BuildContext 变体不一致。",
            units=["frontend:shell"], category="input",
        ))
        return []
    manifest_digest = sha256(json.dumps(
        plain_json(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return [ExternalCapability(
        unit_id="frontend:shell", capability_id="frontend.shell.ready",
        source="template_generation_readiness", workspace_revision=revision,
        source_refs={
            "template_variant": variant, "manifest_path": ".xcodeagent/template-generation-manifest.json",
            "manifest_sha256": manifest_digest,
        },
    )]


def resolve_reuse_facts(
    *, confirmed_plan: Mapping[str, Any] | None, unit_skeleton: Mapping[str, Any],
    build_context: Mapping[str, Any], workspace_snapshot: Mapping[str, Any],
    formal_plan: Mapping[str, Any], template_readiness: Mapping[str, Any] | None = None,
) -> ReuseFacts:
    """计算全部 confirmed 职责事实，返回冻结且顺序稳定的结果，不读写文件或计算缺项。

    confirmed_plan 由 load_confirmed_build_task_plan 提供；formal_plan 是完整正式
    TechnicalPlan 运行时投影。template_readiness 必须来自同次 workspace 检查的
    inspect_template_generation_readiness 结果；缺少该证据就不声明外部能力。
    required_unit_ids 仅用于模板前置问题归因，不裁剪历史 Task；有 issues 必须阻断。
    """

    issues: list[ValidationIssue] = []
    skeleton_units = unit_skeleton.get("build_units")
    if not isinstance(skeleton_units, Mapping) or any(not _identity(key) for key in skeleton_units):
        raise ValueError("unit_skeleton 必须显式提供以精确 Unit ID 为键的 build_units。")
    unit_ids = set(skeleton_units)
    tasks = _baseline_tasks(confirmed_plan, unit_ids, issues)
    # 历史任务不会因当前 Scope 或 Unit 骨架的裁剪而消失；异常所属关系由 issues 阻断。
    all_unit_ids = unit_ids | {task["unit_id"] for task in tasks}
    retained: dict[str, list[str]] = {unit_id: [] for unit_id in sorted(all_unit_ids)}
    capabilities: dict[str, dict[str, list[str]]] = {unit_id: {} for unit_id in sorted(all_unit_ids)}
    for task in tasks:
        unit_id, task_id = task["unit_id"], task["id"]
        retained[unit_id].append(task_id)
        for capability in sorted(_capabilities(task, all_unit_ids, issues)):
            capabilities[unit_id].setdefault(capability, []).append(task_id)
    owners = _endpoint_owners(tasks, _formal_endpoints(formal_plan, issues), issues)
    external = _external_capabilities(unit_ids, build_context, workspace_snapshot, template_readiness, issues)
    return ReuseFacts(
        retained_task_ids_by_unit=retained,
        reusable_capabilities_by_unit={unit: dict(sorted(values.items())) for unit, values in capabilities.items()},
        retained_endpoint_owners=owners, external_capabilities=external,
        issues=sorted(issues, key=lambda issue: (
            issue.code, issue.unit_ids, issue.task_ids,
            json.dumps(plain_json(issue.details), sort_keys=True),
        )),
    )
