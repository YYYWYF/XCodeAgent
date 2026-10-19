"""验证缺失 ID 的确定性绑定与歧义拒绝边界。"""

import json
import unittest
from copy import deepcopy

from app.services.unit_candidate_parser import RawUnitCandidateParseError, parse_raw_unit_candidate
from app.services.unit_task_manifest import unit_task_identity_manifest
from app.services.unit_generation_contracts import UnitGenerationContext
from app.services.business_acceptance import DELIVERABLE_TARGET_IDENTITY_FIELD_BY_KIND
from tests.test_unit_task_prompt import _backend_endpoint_context, _unit_context, _requirement
from tests.test_unit_candidate_validator import _context, _frontend_api_context


def tasks_for(context):
    """构造只携带职责证明的任务，交付物 ID 故意不编码任务 ID。"""

    requirements = {item.requirement_id: item for item in context.generation_requirements}
    result = []
    for task_id, ids in unit_task_identity_manifest(context).items():
        result.append({"unit_id": context.unit_id, "deliverables": [{
            "id": f"opaque-deliverable-{rid}", "provides": [rid],
            "kind": requirements[rid].source_refs["kind"],
            "target_id": requirements[rid].source_refs[
                DELIVERABLE_TARGET_IDENTITY_FIELD_BY_KIND[requirements[rid].source_refs["kind"]]
            ],
        } for rid in ids]})
    return result


class UnitCandidateIdBindingTests(unittest.TestCase):
    """通过生产 Parser 入口验证职责匹配，不依赖标题、数组顺序或交付物命名。"""

    def parse(self, context, tasks):
        """携带冻结身份上下文执行真实解析与绑定。"""

        return parse_raw_unit_candidate(json.dumps({"tasks": tasks}), unit_id=context.unit_id,
                                        task_identity_context=context)

    def test_all_unit_groups_bind_exact_ids(self):
        """覆盖多来源后端、页面、多 Endpoint API、bootstrap 和静态数据分组。"""

        contexts = [
            _backend_endpoint_context("database", "external_api"),
            _context(), _frontend_api_context("orders.list", "orders.detail"),
            _unit_context("backend:bootstrap", "backend", [
                _requirement("backend.bootstrap", "database", data_source_type="database"),
                _requirement("backend.bootstrap", "external_api", data_source_type="external_api"),
            ]),
            _unit_context("frontend:data:static", "frontend", [
                _requirement("frontend.static_data_module", "orders", endpoint_id="orders.list"),
            ]),
        ]
        for context in contexts:
            with self.subTest(unit=context.unit_id):
                tasks = tasks_for(context)
                original = deepcopy(tasks)
                bound = self.parse(context, list(reversed(tasks)))
                self.assertEqual([task["id"] for task in bound], list(reversed(unit_task_identity_manifest(context))))
                self.assertEqual(tasks, original)

    def test_existing_ids_and_other_fields_are_preserved(self):
        """合法混合候选只补缺失 ID，其他字段连同非法内容原样交给后续校验。"""

        context = _backend_endpoint_context()
        tasks = tasks_for(context)
        tasks[0]["id"] = next(iter(unit_task_identity_manifest(context)))
        tasks[1]["owner"] = "wrong-owner"
        result = self.parse(context, tasks)
        self.assertEqual(result[0], tasks[0])
        self.assertEqual(result[1]["owner"], "wrong-owner")

    def test_ambiguous_candidates_fail_without_partial_binding(self):
        """拒绝重复、缺失、多余、合并职责、错误归属、目标或已有 ID。"""

        context = _backend_endpoint_context()
        base = tasks_for(context)
        cases = {}
        cases["duplicate"] = base + [deepcopy(base[0])]
        cases["missing"] = base[:-1]
        mixed = deepcopy(base)
        mixed[0]["deliverables"] += mixed.pop()["deliverables"]
        cases["mixed"] = mixed
        for field, value in (("kind", "backend.repository"), ("target_id", "other"), ("provides", ["unknown"])):
            tasks = deepcopy(base)
            tasks[0]["deliverables"][0][field] = value
            cases[field] = tasks
        for field, value in (("unit_id", "other-unit"), ("id", "wrong-id"), ("id", ""), ("task_id", "alias")):
            tasks = deepcopy(base)
            tasks[0][field] = value
            cases[f"{field}:{value}"] = tasks
        repeated = deepcopy(base)
        repeated[0]["deliverables"].append(deepcopy(repeated[0]["deliverables"][0]))
        cases["duplicate_requirement"] = repeated
        for name, tasks in cases.items():
            before = deepcopy(tasks)
            with self.subTest(case=name), self.assertRaises(RawUnitCandidateParseError) as caught:
                self.parse(context, tasks)
            self.assertEqual(tasks, before)
            self.assertTrue(all(issue.retryable for issue in caught.exception.issues))

    def test_api_contract_cannot_split_or_merge(self):
        """同一 Contract 的多个 Endpoint 必须完整属于同一任务。"""

        context = _frontend_api_context("orders.list", "orders.detail")
        tasks = tasks_for(context)
        split = [{"unit_id": context.unit_id, "deliverables": [item]} for item in tasks[0]["deliverables"]]
        with self.assertRaises(RawUnitCandidateParseError):
            self.parse(context, split)
        context = _unit_context("frontend:api-client", "frontend", [
            _requirement("frontend.api_module", f"{contract}:{endpoint}",
                         api_contract_id=contract, endpoint_id=endpoint)
            for contract, endpoint in (("orders-api", "orders.list"), ("users-api", "users.list"))
        ])
        tasks = tasks_for(context)
        tasks[0]["deliverables"] += tasks.pop()["deliverables"]
        with self.assertRaises(RawUnitCandidateParseError):
            self.parse(context, tasks)

    def test_incremental_endpoint_does_not_recreate_retained_tasks(self):
        """增量候选只绑定当前 controller，既有阶段只作为同 Unit 依赖保留。"""

        payload = _backend_endpoint_context().model_dump(mode="json")
        payload["generation_requirements"] = payload["generation_requirements"][-1:]
        unit_id = payload["unit_id"]
        payload["dependency_context"]["retained_task_summaries"] = [
            {"id": f"{unit_id}::service", "unit_id": unit_id},
        ]
        context = UnitGenerationContext.model_validate(payload)
        tasks = tasks_for(context)
        tasks[0]["dependencies"] = [f"{unit_id}::service"]
        result = self.parse(context, tasks)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], f"{unit_id}::controller")
        self.assertEqual(result[0]["dependencies"], [f"{unit_id}::service"])

    def test_absent_id_is_distinct_from_invalid_existing_values(self):
        """空值、类型错误、空白和已有 ID 不归一化；无 Context 时仍严格拒绝缺 ID。"""

        context = _context()
        for value in ("", None, 12, " "):
            tasks = tasks_for(context)
            tasks[0]["id"] = value
            with self.subTest(value=value), self.assertRaises(RawUnitCandidateParseError):
                self.parse(context, tasks)
        tasks = tasks_for(context)
        with self.assertRaises(RawUnitCandidateParseError):
            parse_raw_unit_candidate(json.dumps({"tasks": tasks}), unit_id=context.unit_id)
