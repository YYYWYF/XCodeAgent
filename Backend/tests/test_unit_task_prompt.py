"""T3.3 Single Unit Prompt Contract 结构与边界回归。"""

from __future__ import annotations

import unittest

from app.agents.main.unit_task_prompt import build_unit_generation_prompt
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import UnitGenerationContext
from tests.test_unit_generation_contracts import _context_payload


def _context() -> UnitGenerationContext:
    """构造含两个增量职责和同 Unit retained 摘要的冻结 Context。"""

    payload = _context_payload()
    payload["generation_requirements"].append(
        {
            "requirement_id": "orders-page-filter",
            "description": "实现订单筛选职责",
            "source_refs": {"artifact": "product-plan", "pointers": ["/pages/orders"]},
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

    def test_prompt_separates_global_and_latest_local_feedback(self) -> None:
        """Global 与最新 Local Issue 必须分区投影并保留结构化路由字段。"""

        prompt = _prompt()
        self.assertIn("### Global feedback", prompt)
        self.assertIn('"code": "GLOBAL_ENDPOINT_OWNER_CONFLICT"', prompt)
        self.assertIn("### Latest local feedback", prompt)
        self.assertIn('"code": "CANDIDATE_DEPENDENCY_UNKNOWN"', prompt)
        self.assertIn('"retry_unit_ids": [', prompt)
        self.assertIn("Feedback is diagnostic input only", prompt)

    def test_prompt_applies_only_supplied_unit_kind_rules(self) -> None:
        """Unit-kind rules 必须显式注入并绑定当前 Unit，不能变成全局规则。"""

        prompt = _prompt()
        self.assertIn("Apply these rules only to `page` Unit `page:orders`", prompt)
        self.assertIn("只实现当前页面的 PageImplementationContract。", prompt)
        self.assertIn("页面 Task 必须复用现有入口文件。", prompt)

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
            "## 4. Frozen Inline Unit Context",
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
