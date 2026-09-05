"""T3.5 Unit Local Validator 全规则、归因与 fatal 边界回归。"""

from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.build_task_reuse_contracts import RetainedEndpointOwner, ReuseFacts
from app.services.planning_issues import ValidationIssue
from app.services.unit_candidate_validator import validate_unit_candidate
from app.services.unit_generation_contracts import UnitGenerationContext


def _requirement(unit_id: str) -> dict:
    """按 Page、Endpoint 或 Shared Unit 构造精确职责。"""

    if unit_id == "page:orders":
        return {
            "requirement_id": "frontend.page:orders",
            "description": "实现订单页面",
            "source_refs": {
                "artifact": "technical-plan", "kind": "frontend.page",
                "capability_id": "frontend.page:orders", "page_id": "orders",
            },
        }
    if unit_id == "backend:endpoint:orders-api:orders.list":
        capability = "backend.endpoint_controller:orders-api:orders.list:Order"
        return {
            "requirement_id": capability,
            "description": "实现订单查询 Controller",
            "source_refs": {
                "artifact": "technical-plan", "kind": "backend.endpoint_controller",
                "capability_id": capability, "api_contract_id": "orders-api",
                "endpoint_id": "orders.list", "entity_id": "Order",
            },
        }
    if unit_id == "frontend:api-client":
        return {
            "requirement_id": "frontend.response-entity-adapter",
            "description": "实现公共响应适配器",
            "source_refs": {
                "artifact": "technical-plan", "kind": "frontend.shared_capability",
                "capability_id": "frontend.response-entity-adapter",
                "target_id": "response-entity-adapter",
            },
        }
    capability = "frontend.api_module:orders-api:orders.list"
    return {
        "requirement_id": capability,
        "description": "实现订单 API 模块",
        "source_refs": {
            "artifact": "technical-plan", "kind": "frontend.api_module",
            "capability_id": capability, "api_contract_id": "orders-api",
            "endpoint_id": "orders.list",
        },
    }


def _context(unit_id: str = "page:orders") -> UnitGenerationContext:
    """构造可供 Local Validator 使用的完整冻结 Context。"""

    owner = "backend" if unit_id.startswith("backend:") else "frontend"
    kind = "backend" if owner == "backend" else ("page" if unit_id.startswith("page:") else "frontend")
    return UnitGenerationContext(
        planning_run_id="planning-run-1",
        unit_id=unit_id,
        unit_kind=kind,
        build_execution_scope={"type": "page", "targetId": "orders"},
        input_fingerprint="input-digest",
        base_confirmed_plan_digest="confirmed-digest",
        generation_requirements=[_requirement(unit_id)],
        formal_contracts={"inline_slices": []},
        workspace_context={"snapshot_id": "workspace-1"},
        dependency_context={
            "dependency_unit_ids": ["frontend:api-client"],
            "retained_task_summaries": [],
            "retained_owner_constraints": [],
        },
        constraints={
            "owner": owner,
            "managed_files": [
                "frontend/src/constants/menus.ts",
                "frontend/src/constants/resources.ts",
            ],
            "strong_rules": [
                "exact_unit_owner", "exact_file_scope", "no_platform_owned_fields",
                "no_platform_owned_tasks", "no_repair_or_verification_tasks", "status_pending",
            ],
        },
    )


def _task(unit_id: str = "page:orders", task_id: str = "task-orders") -> dict:
    """构造覆盖完整 Task schema 的合法 Candidate Task。"""

    requirement = _requirement(unit_id)
    kind = requirement["source_refs"]["kind"]
    owner = "backend" if unit_id.startswith("backend:") else "frontend"
    path = (
        "backend/src/main/java/com/example/orders/OrderController.java"
        if owner == "backend"
        else "frontend/src/apis/orders.ts"
        if kind in {"frontend.api_module", "frontend.shared_capability"}
        else "frontend/src/pages/Orders/index.tsx"
    )
    target_id = (
        requirement["source_refs"].get("endpoint_id")
        if kind == "backend.endpoint_controller"
        else requirement["source_refs"].get("entity_id")
        or requirement["source_refs"].get("endpoint_id")
        or requirement["source_refs"].get("target_id")
        or requirement["source_refs"].get("page_id")
    )
    return {
        "id": task_id,
        "unit_id": unit_id,
        "owner": owner,
        "task_type": "backend.code" if owner == "backend" else "frontend.code",
        "title": "实现当前 Unit",
        "description": "按冻结合同实现当前 Unit 的缺失职责。",
        "dependencies": [],
        "target_files": [path],
        "change_scope": [{"operation": "add", "path": path, "description": "新增实现"}],
        "allowed_paths": [path],
        "deliverables": [{
            "id": f"{task_id}-deliverable",
            "kind": kind,
            "target_id": target_id,
            "paths": [path],
            "provides": [requirement["requirement_id"]],
        }],
        "impact_scope": {
            "summary": "仅影响当前 Unit",
            "affected_modules": [],
            "public_contracts": [],
            "risks": [],
        },
        "can_run_in_parallel": True,
        "parallel_reason": "与同 Unit 其他任务无文件冲突。",
        "status": "pending",
    }


def _reuse_facts(*owners: RetainedEndpointOwner, issues: tuple[ValidationIssue, ...] = ()) -> ReuseFacts:
    """构造只读 ReuseFacts，保留 owner 或前置 fatal Issues。"""

    return ReuseFacts(
        retained_task_ids_by_unit={},
        reusable_capabilities_by_unit={},
        retained_endpoint_owners=owners,
        external_capabilities=(),
        issues=issues,
    )


class UnitCandidateValidatorTests(unittest.TestCase):
    """验证 Local Validator 是纯函数且严格区分内容问题与平台 fatal。"""

    def _assert_local_retry(self, issues: list[ValidationIssue], unit_id: str) -> None:
        """断言所有模型内容问题只归因并重试当前 Unit。"""

        self.assertTrue(issues)
        for issue in issues:
            self.assertEqual(issue.level, "unit")
            self.assertEqual(issue.category, "generation")
            self.assertEqual(issue.retry_unit_ids, (unit_id,))
            self.assertTrue(issue.retryable)

    def test_page_endpoint_and_shared_candidates_are_valid(self) -> None:
        """Page、Endpoint 与 Shared Unit 的完整 Candidate 均返回空 Issues。"""

        for unit_id in (
            "page:orders",
            "backend:endpoint:orders-api:orders.list",
            "frontend:api-client",
        ):
            with self.subTest(unit_id=unit_id):
                self.assertEqual(validate_unit_candidate(_context(unit_id), [_task(unit_id)]), [])

    def test_each_local_rule_reports_single_invalid_candidate(self) -> None:
        """每个主要不变量被单独破坏时均产生对应结构化 Issue。"""

        cases = {
            "schema": (lambda task: task.pop("title"), "CANDIDATE_TASK_FIELD_MISSING"),
            "unit_id": (lambda task: task.__setitem__("unit_id", "page:other"), "CANDIDATE_UNIT_ID_MISMATCH"),
            "owner": (lambda task: task.__setitem__("owner", "backend"), "CANDIDATE_OWNER_MISMATCH"),
            "requirements": (
                lambda task: task["deliverables"][0].__setitem__("provides", ["other.capability"]),
                "CANDIDATE_GENERATION_REQUIREMENT_MISSING",
            ),
            "target_files": (
                lambda task: task.__setitem__("target_files", ["frontend/src/pages/Orders/Other.tsx"]),
                "CANDIDATE_TARGET_FILES_MISMATCH",
            ),
            "target_owner": (
                lambda task: (
                    task.__setitem__("target_files", ["backend/src/Orders.ts"]),
                    task.__setitem__("allowed_paths", ["backend/src/Orders.ts"]),
                    task.__setitem__("change_scope", [{"operation": "add", "path": "backend/src/Orders.ts", "description": "越界"}]),
                    task["deliverables"][0].__setitem__("paths", ["backend/src/Orders.ts"]),
                ),
                "CANDIDATE_TARGET_FILE_OWNER_MISMATCH",
            ),
            "change_scope": (
                lambda task: task["change_scope"][0].__setitem__("operation", "upsert"),
                "CANDIDATE_CHANGE_OPERATION_INVALID",
            ),
            "deliverables": (
                lambda task: task["deliverables"][0].__setitem__("kind", "frontend.unknown"),
                "CANDIDATE_DELIVERABLE_KIND_INVALID",
            ),
            "requirement_target": (
                lambda task: task["deliverables"][0].__setitem__("target_id", "other-page"),
                "CANDIDATE_REQUIREMENT_TARGET_MISMATCH",
            ),
            "strong_rules": (
                lambda task: task.__setitem__("acceptance_checks", []),
                "CANDIDATE_PLATFORM_FIELD_FORBIDDEN",
            ),
        }
        for label, (mutate, code) in cases.items():
            with self.subTest(rule=label):
                context = _context()
                task = _task()
                mutate(task)
                issues = validate_unit_candidate(context, [task])
                self.assertIn(code, {issue.code for issue in issues})
                self._assert_local_retry(issues, context.unit_id)

    def test_deliverable_rejects_valid_requirement_with_unexpected_capability(self) -> None:
        """合法 requirement 不能掩盖同一 provides 中夹带的未请求 capability。"""

        context = _context()
        task = _task()
        task["deliverables"][0]["provides"].append("unexpected.capability")

        issues = validate_unit_candidate(context, [task])

        issue = next(
            item for item in issues
            if item.code == "CANDIDATE_UNREQUESTED_DELIVERABLE"
        )
        self.assertEqual(
            issue.details["unexpected_capabilities"],
            ("unexpected.capability",),
        )
        self._assert_local_retry(issues, context.unit_id)

    def test_deliverable_cannot_mix_kind_and_target_across_requirements(self) -> None:
        """一个 deliverable 必须逐项匹配 provides 中每个 requirement 的完整身份。"""

        context_payload = _context("frontend:api-client").model_dump(mode="json")
        api_requirement = _requirement("frontend:api-module")
        shared_requirement = _requirement("frontend:api-client")
        context_payload["generation_requirements"] = [
            api_requirement,
            shared_requirement,
        ]
        context = UnitGenerationContext(**context_payload)
        task = _task("frontend:api-client")
        task["deliverables"][0].update({
            "kind": api_requirement["source_refs"]["kind"],
            "target_id": shared_requirement["source_refs"]["target_id"],
            "provides": [
                api_requirement["requirement_id"],
                shared_requirement["requirement_id"],
            ],
        })

        issues = validate_unit_candidate(context, [task])

        codes = {issue.code for issue in issues}
        self.assertIn("CANDIDATE_REQUIREMENT_KIND_MISMATCH", codes)
        self.assertIn("CANDIDATE_REQUIREMENT_TARGET_MISMATCH", codes)
        self._assert_local_retry(issues, context.unit_id)

    def test_managed_files_are_rejected_without_sanitizing_paths(self) -> None:
        """模型触碰 managed file 时返回 Issue，原 Candidate 不被删路径或改 owner。"""

        context = _context()
        task = _task()
        managed = "frontend/src/constants/resources.ts"
        task["target_files"] = [managed]
        task["allowed_paths"] = [managed]
        task["change_scope"] = [{"operation": "modify", "path": managed, "description": "修改资源"}]
        task["deliverables"][0]["paths"] = [managed]
        before = deepcopy(task)

        issues = validate_unit_candidate(context, [task])

        self.assertIn("CANDIDATE_MANAGED_FILE_CONFLICT", {issue.code for issue in issues})
        self.assertEqual(task, before)
        self._assert_local_retry(issues, context.unit_id)

    def test_same_unit_dependencies_and_cycles_use_full_api(self) -> None:
        """完整 API 接入 retained allowlist、未知依赖与 same-unit cycle 检查。"""

        payload = _context().model_dump(mode="json")
        payload["dependency_context"]["retained_task_summaries"] = [
            {"id": "task-retained", "unit_id": "page:orders"}
        ]
        context = UnitGenerationContext(**payload)
        first = _task(task_id="task-a")
        second = _task(task_id="task-b")
        first["dependencies"] = ["task-b", "task-retained", "unknown"]
        second["dependencies"] = ["task-a"]

        issues = validate_unit_candidate(context, [first, second])

        codes = {issue.code for issue in issues}
        self.assertIn("CANDIDATE_DEPENDENCY_UNKNOWN", codes)
        self.assertIn("CANDIDATE_DEPENDENCY_CYCLE", codes)
        self._assert_local_retry(issues, context.unit_id)

    def test_retained_endpoint_owner_conflict_is_local_and_attributable(self) -> None:
        """Shared Candidate 重做 retained Endpoint 实现时只重试当前 Shared Unit。"""

        context_payload = _context("frontend:api-client").model_dump(mode="json")
        api_requirement = _requirement("frontend:api-module")
        context_payload["generation_requirements"] = [api_requirement]
        context = UnitGenerationContext(**context_payload)
        task = _task("frontend:api-client")
        task["deliverables"][0].update({
            "kind": "frontend.api_module",
            "target_id": "orders.list",
            "provides": [api_requirement["requirement_id"]],
        })
        retained = RetainedEndpointOwner(
            api_contract_id="orders-api", endpoint_id="orders.list",
            owner_task_id="task-api-retained", owner_unit_id="page:history",
        )

        issues = validate_unit_candidate(context, [task], _reuse_facts(retained))

        conflict = next(issue for issue in issues if issue.code == "CANDIDATE_RETAINED_ENDPOINT_OWNER_CONFLICT")
        self.assertEqual(conflict.task_ids, (task["id"], "task-api-retained"))
        self._assert_local_retry(issues, context.unit_id)

    def test_multiple_invalid_rules_accumulate_without_mutation(self) -> None:
        """同一 Candidate 的多个内容错误同时返回，不能首错退出或 silent sanitize。"""

        context = _context()
        task = _task()
        task["owner"] = "backend"
        task["unit_id"] = "page:other"
        task["target_files"] = ["../escape.ts"]
        task["status"] = "completed"
        task["dependencies"] = ["unknown"]
        before = deepcopy(task)

        issues = validate_unit_candidate(context, [task])

        codes = {issue.code for issue in issues}
        self.assertTrue({
            "CANDIDATE_OWNER_MISMATCH", "CANDIDATE_UNIT_ID_MISMATCH",
            "CANDIDATE_PATH_INVALID", "CANDIDATE_STATUS_INVALID",
            "CANDIDATE_DEPENDENCY_UNKNOWN",
        } <= codes)
        self.assertEqual(task, before)
        self._assert_local_retry(issues, context.unit_id)

    def test_nested_schema_errors_remain_structured(self) -> None:
        """Parser 允许的任意 JSON 嵌套类型不能让 Validator 崩溃或触发隐式转换。"""

        context = _context()
        task = _task()
        task.update({"target_files": None, "allowed_paths": {}, "risk": {"level": "low"}})
        task["change_scope"][0]["operation"] = {"kind": "add"}
        task["deliverables"][0]["paths"] = None

        issues = validate_unit_candidate(context, [task])

        self.assertIn("CANDIDATE_TASK_FIELD_TYPE_INVALID", {issue.code for issue in issues})
        self.assertIn("CANDIDATE_CHANGE_OPERATION_INVALID", {issue.code for issue in issues})
        self._assert_local_retry(issues, context.unit_id)

    def test_platform_or_input_failures_never_become_model_retry_issues(self) -> None:
        """非法冻结 Context 和 ReuseFacts 问题均 fatal，且不混入 Candidate 内容反馈。"""

        invalid_context = _context().model_dump(mode="json")
        invalid_context["constraints"]["strong_rules"] = ["execute-natural-language-rule"]
        invalid_task = _task()
        invalid_task["owner"] = "backend"
        context_issues = validate_unit_candidate(invalid_context, [invalid_task])
        self.assertEqual([issue.code for issue in context_issues], ["UNIT_VALIDATION_STRONG_RULE_CONTRACT_INVALID"])

        baseline_issue = ValidationIssue(
            code="CONFIRMED_ENDPOINT_OWNER_CONFLICT", level="pre_generation",
            category="platform", unit_ids=("frontend:api-client",), task_ids=("old-a", "old-b"),
            retry_unit_ids=(), retryable=False, message="confirmed baseline owner conflict",
        )
        reuse_issues = validate_unit_candidate(_context(), [invalid_task], _reuse_facts(issues=(baseline_issue,)))
        self.assertEqual(reuse_issues, [baseline_issue])
        for issue in (*context_issues, *reuse_issues):
            self.assertFalse(issue.retryable)
            self.assertEqual(issue.retry_unit_ids, ())
            self.assertIn(issue.category, {"input", "platform"})

    def test_retained_baseline_conflict_is_platform_fatal(self) -> None:
        """ReuseFacts 自身的多 owner 冲突不能伪装成当前 Candidate 的重试问题。"""

        owners = (
            RetainedEndpointOwner(
                api_contract_id="orders-api", endpoint_id="orders.list",
                owner_task_id="old-a", owner_unit_id="page:a",
            ),
            RetainedEndpointOwner(
                api_contract_id="orders-api", endpoint_id="orders.list",
                owner_task_id="old-b", owner_unit_id="page:b",
            ),
        )

        issues = validate_unit_candidate(_context(), [_task()], _reuse_facts(*owners))

        self.assertEqual([issue.code for issue in issues], ["UNIT_VALIDATION_RETAINED_OWNER_BASELINE_CONFLICT"])
        self.assertFalse(issues[0].retryable)
        self.assertEqual(issues[0].category, "platform")

    def test_non_parser_candidate_is_platform_fatal(self) -> None:
        """调用方绕过 T3.1 Parser 时失败不可重试，不把平台接线错误归因给模型。"""

        issues = validate_unit_candidate(_context(), ["not-an-object"])  # type: ignore[list-item]

        self.assertEqual([issue.code for issue in issues], ["UNIT_VALIDATION_PARSED_CANDIDATE_INVALID"])
        self.assertFalse(issues[0].retryable)
        self.assertEqual(issues[0].retry_unit_ids, ())


if __name__ == "__main__":
    unittest.main()
