"""T3.3 Single Unit Prompt Contract 结构与边界回归。"""

from __future__ import annotations

import json
import unittest
from typing import Any

from app.agents.main.unit_task_prompt import build_unit_generation_prompt
from app.agents.main.unit_task_rules import (
    requirement_output_contracts,
    resolve_unit_task_rules,
)
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import UnitGenerationContext
from tests.test_unit_generation_contracts import _context_payload


def _context() -> UnitGenerationContext:
    """构造含两个增量职责和同 Unit retained 摘要的冻结 Context。"""

    payload = _context_payload()
    payload["generation_requirements"][0]["source_refs"].update({
        "capability_id": "orders-page",
        "kind": "frontend.page",
        "page_id": "orders",
    })
    payload["generation_requirements"].append(
        {
            "requirement_id": "orders-page-filter",
            "description": "实现订单筛选职责",
            "source_refs": {
                "artifact": "product-plan",
                "capability_id": "orders-page-filter",
                "kind": "frontend.page",
                "page_id": "orders",
                "pointers": ["/pages/orders"],
            },
        }
    )
    payload["dependency_context"]["retained_task_summaries"] = [
        {
            "id": "task-orders-api-retained",
            "unit_id": "page:orders",
            "title": "既有订单 API 调用",
            "source_refs": {"capabilities": ["orders.api.ready"]},
        }
    ]
    return UnitGenerationContext(**payload)


def _issue(code: str, message: str, *, level: str = "unit") -> ValidationIssue:
    """构造可在 Prompt 中稳定序列化的结构化反馈。"""

    return ValidationIssue(
        code=code,
        level=level,
        category="generation",
        unit_ids=("page:orders",),
        task_ids=("task-orders",),
        retry_unit_ids=("page:orders",),
        retryable=True,
        message=message,
        details={"field": "dependencies"},
    )


def _prompt() -> str:
    """使用固定 Context、规则和两级反馈构建测试 Prompt。"""

    return build_unit_generation_prompt(
        _context(),
        global_feedback=(
            _issue("GLOBAL_ENDPOINT_OWNER_CONFLICT", "修复 Endpoint owner 冲突", level="global"),
        ),
        latest_local_feedback=(
            _issue("CANDIDATE_DEPENDENCY_UNKNOWN", "移除未知 Task 依赖"),
        ),
        unit_kind_rules=(
            "只实现当前页面的 PageImplementationContract。",
            "页面 Task 必须复用现有入口文件。",
        ),
    )


def _requirement(kind: str, suffix: str, **source_refs: Any) -> dict:
    """构造能被 Prompt 规则清单精确消费的增量职责。"""

    requirement_prefix = "backend.endpoint.objects" if kind == "backend.objects" else kind
    requirement_id = f"{requirement_prefix}:{suffix}"
    artifact = (
        "endpoint-api-design"
        if kind in {
            "backend.objects",
            "backend.repository",
            "backend.upstream",
            "backend.application_service",
            "backend.endpoint_controller",
        }
        else "technical-plan"
    )
    return {
        "requirement_id": requirement_id,
        "description": f"实现 {requirement_id}",
        "source_refs": {
            "artifact": artifact,
            "capability_id": requirement_id,
            "kind": kind,
            **source_refs,
        },
    }


def _unit_context(
    unit_id: str,
    unit_kind: str,
    requirements: list[dict],
    *,
    retained_task_summaries: list[dict] | None = None,
) -> UnitGenerationContext:
    """基于公共冻结夹具构造不同类型的模型 Unit Context。"""

    payload = _context_payload()
    payload.update({
        "unit_id": unit_id,
        "unit_kind": unit_kind,
        "generation_requirements": requirements,
        "constraints": {
            "owner": "frontend" if unit_kind in {"page", "frontend"} else unit_kind,
            "managed_files": [],
            "strong_rules": ["exact_unit_owner"],
        },
        "dependency_context": {
            "dependency_unit_ids": [],
            "retained_task_summaries": retained_task_summaries or [],
            "retained_owner_constraints": [],
        },
    })
    return UnitGenerationContext(**payload)


def _backend_endpoint_context(*source_types: str) -> UnitGenerationContext:
    """按 Endpoint 来源集合构造当前五阶段职责的精确子集。"""

    kinds = ["backend.objects"]
    if "database" in source_types:
        kinds.append("backend.repository")
    if "external_api" in source_types:
        kinds.append("backend.upstream")
    kinds.extend(("backend.application_service", "backend.endpoint_controller"))
    return _unit_context(
        "backend:endpoint:orders-api:orders.list",
        "backend",
        [
            _requirement(
                kind,
                "orders-api:orders.list",
                api_contract_id="orders-api",
                endpoint_id="orders.list",
                target_id="orders.list",
                data_source_types=list(source_types),
            )
            for kind in kinds
        ],
    )


def _backend_endpoint_manifest(context: UnitGenerationContext) -> list[dict]:
    """从公开 Unit rules 中提取模型必须逐字执行的 Endpoint manifest。"""

    rule = next(
        item
        for item in resolve_unit_task_rules(context)
        if item.startswith("Emit exactly one Task per record")
    )
    return json.loads(rule.split(":\n", 1)[1])


class UnitTaskPromptTests(unittest.TestCase):
    def test_prompt_binds_exact_single_unit_identity(self) -> None:
        """Prompt 必须绑定唯一 Unit、kind、Run 和输入指纹。"""

        prompt = _prompt()
        self.assertIn("Plan exactly one Unit and no other Unit", prompt)
        self.assertIn("Current unit_id: `page:orders`", prompt)
        self.assertIn("Current unit_kind: `page`", prompt)
        self.assertIn("Current planning_run_id: `planning-run-1`", prompt)
        self.assertIn("Current input_fingerprint: `input-digest`", prompt)

    def test_prompt_contains_incremental_generation_requirements(self) -> None:
        """每条本轮缺失职责必须出现，并明确不是累计 Unit 历史。"""

        prompt = _prompt()
        self.assertIn('"requirement_id": "orders-page"', prompt)
        self.assertIn('"requirement_id": "orders-page-filter"', prompt)
        self.assertIn("incremental requirements, not the Unit's cumulative history", prompt)
        self.assertIn("Generate only the current PlanningRun's new Task contribution", prompt)

    def test_prompt_declares_target_files_required_by_local_validator(self) -> None:
        """模型输出契约必须显式包含 Local Validator 强制校验的 target_files。"""

        self.assertIn('"target_files": [', _prompt())

    def test_prompt_contains_retained_summary_and_dependency_allowlist(self) -> None:
        """仅 Context 暴露的同 Unit retained ID 可以与 Candidate IDs 一起被引用。"""

        prompt = _prompt()
        self.assertIn('"id": "task-orders-api-retained"', prompt)
        self.assertIn('"orders.api.ready"', prompt)
        self.assertIn("another Task ID returned in this same Candidate", prompt)
        self.assertIn("current Unit retained summaries", prompt)
        self.assertIn("Never reference a Task from another Candidate", prompt)
        self.assertIn("platform compiles cross-Unit dependencies later", prompt)

    def test_prompt_exposes_only_frozen_contract_reader_tool(self) -> None:
        """合同正文只能通过 catalog 授权的 Reader tool 读取，不能扩展到工作区工具。"""

        prompt = _prompt()
        self.assertIn("only available tool is `read_frozen_contract_fragment`", prompt)
        self.assertIn("exact catalog `ref_id` and authorized `selector`", prompt)
        self.assertIn("opaque `nextCursor`", prompt)
        self.assertIn("Never invent a cursor", prompt)
        self.assertIn("return one complete `tasks[]` envelope", prompt)

    def test_prompt_separates_global_and_latest_local_feedback(self) -> None:
        """Global 与最新 Local Issue 必须分区投影并保留结构化路由字段。"""

        prompt = _prompt()
        self.assertIn("### Global feedback", prompt)
        self.assertIn('"code": "GLOBAL_ENDPOINT_OWNER_CONFLICT"', prompt)
        self.assertIn("### Latest local feedback", prompt)
        self.assertIn('"code": "CANDIDATE_DEPENDENCY_UNKNOWN"', prompt)
        self.assertIn('"retry_unit_ids": [', prompt)
        self.assertIn("Feedback is diagnostic input only", prompt)

    def test_prompt_combines_automatic_and_supplied_unit_kind_rules(self) -> None:
        """自动 Unit 规则与调用方附加规则共同绑定当前 Unit。"""

        prompt = _prompt()
        self.assertIn("Apply these rules only to `page` Unit `page:orders`", prompt)
        self.assertIn("`page:orders::page`", prompt)
        self.assertIn("requirement-to-deliverable manifest is authoritative", prompt)
        self.assertIn("只实现当前页面的 PageImplementationContract。", prompt)
        self.assertIn("页面 Task 必须复用现有入口文件。", prompt)

    def test_all_model_unit_types_resolve_current_responsibility_rules(self) -> None:
        """六类模型 Unit 均自动获得当前数量、固定 ID、分层或复用规则。"""

        database_requirements = [
            _requirement(
                kind,
                "orders-api:orders.list",
                api_contract_id="orders-api",
                endpoint_id="orders.list",
                target_id="orders.list",
                data_source_types=["database"],
            )
            for kind in (
                "backend.objects",
                "backend.repository",
                "backend.application_service",
                "backend.endpoint_controller",
            )
        ]
        external_requirements = [
            _requirement(
                kind,
                "profile-api:profile.get",
                api_contract_id="profile-api",
                endpoint_id="profile.get",
                target_id="profile.get",
                data_source_types=["external_api"],
            )
            for kind in (
                "backend.objects",
                "backend.upstream",
                "backend.application_service",
                "backend.endpoint_controller",
            )
        ]
        cases = (
            (
                _unit_context("page:orders", "page", [
                    _requirement("frontend.page", "orders", page_id="orders")
                ]),
                ("page:orders::page", "PageImplementationContract"),
            ),
            (
                _unit_context("frontend:api-client", "frontend", [
                    _requirement(
                        "frontend.shared_capability",
                        "response-entity-adapter",
                        target_id="response-entity-adapter",
                    ),
                    _requirement(
                        "frontend.api_module",
                        "orders-api:orders.list",
                        api_contract_id="orders-api",
                        endpoint_id="orders.list",
                    ),
                ]),
                ("frontend:api-client::response-entity-adapter", "SUC0000"),
            ),
            (
                _unit_context("frontend:data:static", "frontend", [
                    _requirement(
                        "frontend.static_data_module",
                        "orders-api:orders.list",
                        api_contract_id="orders-api",
                        endpoint_id="orders.list",
                    )
                ]),
                ("frontend:data:static::data-module", "module-local types and constants"),
            ),
            (
                _unit_context("backend:bootstrap", "backend", [
                    _requirement(
                        "backend.bootstrap",
                        source_type,
                        data_source_type=source_type,
                    )
                    for source_type in ("database", "external_api")
                ]),
                ("backend:bootstrap::bootstrap", "MyBatis-Plus/MySQL", "Spring Cloud OpenFeign"),
            ),
            (
                _unit_context(
                    "backend:endpoint:orders-api:orders.list",
                    "backend",
                    database_requirements,
                ),
                (
                    "backend:endpoint:orders-api:orders.list::objects",
                    "repository/upstream branch from objects",
                ),
            ),
            (
                _unit_context(
                    "backend:endpoint:profile-api:profile.get",
                    "backend",
                    external_requirements,
                ),
                (
                    "backend:endpoint:profile-api:profile.get::upstream",
                    "sourceSnapshots",
                    "fieldMappings",
                ),
            ),
        )
        for context, expected_fragments in cases:
            with self.subTest(unit_id=context.unit_id):
                rules = resolve_unit_task_rules(context)
                self.assertTrue(rules)
                rendered = "\n".join(rules)
                task_owner = (
                    "backend" if context.unit_kind == "backend" else "frontend"
                )
                self.assertIn(f"task_type `{task_owner}.code`", rendered)
                self.assertIn(context.generation_requirements[0].requirement_id, rendered)
                for fragment in expected_fragments:
                    self.assertIn(fragment, rendered)

    def test_mixed_backend_rules_form_endpoint_branch_join_without_mapping_task(self) -> None:
        """Mixed Endpoint 只生成五个 responsibility Task，并在 service 汇合两条来源分支。"""

        context = _backend_endpoint_context("database", "external_api")

        rendered = "\n".join(resolve_unit_task_rules(context))
        manifest = {
            item["stage"]: item
            for item in _backend_endpoint_manifest(context)
        }

        for stage in ("objects", "repository", "upstream", "service", "controller"):
            self.assertIn(f"backend:endpoint:orders-api:orders.list::{stage}", rendered)
        self.assertNotIn("backend:endpoint:orders-api:orders.list::mapping", rendered)
        self.assertNotIn("::Order::", rendered)
        self.assertNotIn("backend:endpoint:orders-api:orders.list::endpoint::", rendered)
        self.assertIn('"backend:endpoint:orders-api:orders.list::repository"', rendered)
        self.assertIn('"backend:endpoint:orders-api:orders.list::upstream"', rendered)
        self.assertIn("service joins available physical branches", rendered)
        self.assertIn("service Task is the sole owner of Endpoint fieldMappings", rendered)
        self.assertEqual(
            manifest["repository"]["dependencies"],
            ["backend:endpoint:orders-api:orders.list::objects"],
        )
        self.assertEqual(
            manifest["upstream"]["dependencies"],
            ["backend:endpoint:orders-api:orders.list::objects"],
        )
        self.assertEqual(
            manifest["service"]["dependencies"],
            [
                "backend:endpoint:orders-api:orders.list::repository",
                "backend:endpoint:orders-api:orders.list::upstream",
            ],
        )

    def test_database_endpoint_manifest_has_no_entity_based_ids(self) -> None:
        """纯数据库 Endpoint 固定为 objects/repository/service/controller。"""

        manifest = _backend_endpoint_manifest(
            _backend_endpoint_context("database")
        )

        self.assertEqual(
            [item["stage"] for item in manifest],
            ["objects", "repository", "service", "controller"],
        )
        self.assertEqual(
            [item["task_id"] for item in manifest],
            [
                "backend:endpoint:orders-api:orders.list::objects",
                "backend:endpoint:orders-api:orders.list::repository",
                "backend:endpoint:orders-api:orders.list::service",
                "backend:endpoint:orders-api:orders.list::controller",
            ],
        )
        self.assertNotIn("entity_id", json.dumps(manifest))
        self.assertNotIn("::Order::", json.dumps(manifest))

    def test_external_api_endpoint_manifest_has_no_mapping_task(self) -> None:
        """纯外部 API Endpoint 固定为 objects/upstream/service/controller。"""

        manifest = _backend_endpoint_manifest(
            _backend_endpoint_context("external_api")
        )

        self.assertEqual(
            [item["stage"] for item in manifest],
            ["objects", "upstream", "service", "controller"],
        )
        self.assertNotIn("mapping", {item["stage"] for item in manifest})
        self.assertTrue(all(
            item["task_id"].startswith(
                "backend:endpoint:orders-api:orders.list::"
            )
            for item in manifest
        ))
        self.assertNotIn("::Order::", json.dumps(manifest))

    def test_backend_incremental_task_can_depend_on_retained_stage(self) -> None:
        """缺失 controller 可依赖同 Unit 已保留的稳定 service Task，而无需重建 service。"""

        unit_id = "backend:endpoint:orders-api:orders.list"
        context = _unit_context(
            unit_id,
            "backend",
            [_requirement(
                "backend.endpoint_controller",
                "orders-api:orders.list",
                api_contract_id="orders-api",
                endpoint_id="orders.list",
                target_id="orders.list",
                data_source_types=["database"],
            )],
            retained_task_summaries=[{
                "id": f"{unit_id}::service",
                "unit_id": unit_id,
                "title": "既有 Endpoint service",
            }],
        )

        rendered = "\n".join(resolve_unit_task_rules(context))

        self.assertIn(f'"task_id": "{unit_id}::controller"', rendered)
        self.assertIn(f'"{unit_id}::service"', rendered)
        self.assertNotIn(f'"task_id": "{unit_id}::service"', rendered)

    def test_backend_rules_accept_empty_physical_source_set(self) -> None:
        """无物理来源的 Endpoint 仍允许 objects-service-controller 增量拓扑。"""

        unit_id = "backend:endpoint:orders-api:orders.list"
        context = _unit_context(
            unit_id,
            "backend",
            [
                _requirement(
                    kind,
                    "orders-api:orders.list",
                    api_contract_id="orders-api",
                    endpoint_id="orders.list",
                    target_id="orders.list",
                    data_source_types=[],
                )
                for kind in (
                    "backend.objects",
                    "backend.application_service",
                    "backend.endpoint_controller",
                )
            ],
        )

        rendered = "\n".join(resolve_unit_task_rules(context))

        self.assertIn(f'"task_id": "{unit_id}::objects"', rendered)
        self.assertIn(f'"task_id": "{unit_id}::service"', rendered)
        self.assertIn(f'"task_id": "{unit_id}::controller"', rendered)
        self.assertNotIn(f'"task_id": "{unit_id}::repository"', rendered)
        self.assertNotIn(f'"task_id": "{unit_id}::upstream"', rendered)

    def test_backend_rules_reject_legacy_entity_stage_requirement(self) -> None:
        """旧 entity/mapping responsibility 不能重新进入 Endpoint Unit Prompt。"""

        context = _unit_context(
            "backend:endpoint:profile-api:profile.get",
            "backend",
            [_requirement(
                "backend.external_api_mapping",
                "profile-api:profile.get:Profile",
                api_contract_id="profile-api",
                endpoint_id="profile.get",
                entity_id="Profile",
                data_source_type="external_api",
            )],
        )

        with self.assertRaisesRegex(ValueError, "无法绑定到当前 Endpoint"):
            resolve_unit_task_rules(context)

    def test_requirement_contract_compiler_rejects_unknown_kind(self) -> None:
        """输出合同编译器不能为未来未知 kind 静默选择默认 target 字段。"""

        context = _unit_context(
            "page:orders",
            "page",
            [_requirement("backend.future_responsibility", "orders")],
        )

        with self.assertRaisesRegex(ValueError, "未知 deliverable kind"):
            requirement_output_contracts(context.generation_requirements)

    def test_unknown_model_unit_cannot_fall_back_to_empty_rules(self) -> None:
        """未知模型 Unit 在构建 Prompt 前失败，不能静默携带空规则调用模型。"""

        context = _unit_context("frontend:unknown", "frontend", [
            _requirement(
                "frontend.api_module",
                "orders-api:orders.list",
                api_contract_id="orders-api",
                endpoint_id="orders.list",
            )
        ])
        with self.assertRaisesRegex(ValueError, "没有可用的任务规划规则投影"):
            build_unit_generation_prompt(context)

    def test_prompt_forbids_replacement_and_platform_owned_work(self) -> None:
        """Prompt 必须禁止 replacement、其他 Candidate 和平台拥有职责。"""

        prompt = _prompt()
        for required in (
            "Do not decide, emit, or imply replacement",
            "Do not assemble a Scope DAG",
            "frontend:shell",
            "frontend:auth-guard",
            "route/menu registration",
            "authorization projection",
            "acceptance",
            "deterministic-executor responsibilities",
            "Never output `workspace_analysis`",
        ):
            with self.subTest(required=required):
                self.assertIn(required, prompt)

    def test_prompt_declares_exact_raw_candidate_output_schema(self) -> None:
        """最终响应必须只有 tasks 顶层键并声明完整 Task 基础结构。"""

        prompt = _prompt()
        self.assertIn("exactly one top-level key: `tasks`", prompt)
        self.assertIn('{"tasks":[]}', prompt)
        self.assertIn('{"tasks":[...]}', prompt)
        self.assertIn('"unit_id": "page:orders"', prompt)
        for field in (
            "id",
            "owner",
            "task_type",
            "dependencies",
            "change_scope",
            "allowed_paths",
            "deliverables",
            "impact_scope",
            "status",
        ):
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', prompt)

    def test_prompt_is_deterministic_and_does_not_mutate_context(self) -> None:
        """相同冻结输入生成相同 snapshot，且 Builder 不修改 Context。"""

        context = _context()
        before = context.model_dump_json()
        kwargs = {
            "global_feedback": (_issue("GLOBAL", "global", level="global"),),
            "latest_local_feedback": (_issue("LOCAL", "local"),),
            "unit_kind_rules": ("只处理当前页面 Unit。",),
        }

        first = build_unit_generation_prompt(context, **kwargs)
        second = build_unit_generation_prompt(context, **kwargs)

        self.assertEqual(first, second)
        self.assertEqual(context.model_dump_json(), before)
        headings = [
            "## 1. Single Unit Role & Boundary",
            "## 2. Strict Output Contract",
            "## 3. Current Generation Requirements",
            "## 4. Frozen Unit Context & Contract Catalog",
            "## 5. Dependency Allowlist",
            "## 6. Unit-Kind Rules",
            "## 7. Structured Feedback",
            "## 8. Forbidden Decisions and Responsibilities",
            "## 9. Final Response",
        ]
        positions = [first.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
