"""验证缺失 ID 的确定性绑定与歧义拒绝边界。

后端模块 Unit 已改为按接口分组的 `backend:module:<contract>`，候选是
`key/title/files` 的草稿形态并由生产 Parser 编译；非后端 Unit 仍使用
`unit_id/deliverables` 形态做缺失 ID 绑定。
"""

import json
import unittest
from copy import deepcopy

from app.services.unit_candidate_parser import RawUnitCandidateParseError, parse_raw_unit_candidate
from app.services.unit_task_manifest import (
    backend_stage_manifest,
    unit_task_identity_manifest,
)
from app.services.business_acceptance import DELIVERABLE_TARGET_IDENTITY_FIELD_BY_KIND
from app.services.unit_generation_contracts import UnitGenerationContext
from tests.unit_prompt_contexts import _backend_endpoint_context, _requirement, _unit_context
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


def backend_drafts(context):
    """构造后端模块草稿：每个阶段任务用 files[].covers 覆盖其全部职责序号。"""

    drafts = []
    for record in backend_stage_manifest(context):
        covers = list(range(1, len(record["requirement_ids"]) + 1))
        path = (
            f"backend/src/main/java/com/cmbchina/backend/Orders{record['stage'].title()}Handler.java"
            if record["stage"] == "controller"
            else "backend/src/main/java/com/cmbchina/backend/OrdersService.java"
        )
        drafts.append({
            "key": record["key"],
            "title": f"实现 {record['key']}",
            "files": [{
                "path": path,
                "operation": "add",
                "description": "实现该阶段职责",
                "covers": covers,
            }],
        })
    return drafts


class UnitCandidateIdBindingTests(unittest.TestCase):
    """通过生产 Parser 入口验证职责匹配，不依赖标题、数组顺序或交付物命名。"""

    def parse(self, context, tasks):
        """携带冻结身份上下文执行真实解析与绑定。"""

        return parse_raw_unit_candidate(json.dumps({"tasks": tasks}), unit_id=context.unit_id,
                                        task_identity_context=context)

    def test_all_unit_groups_bind_exact_ids(self):
        """覆盖多来源后端、页面、多 Endpoint API、bootstrap 和静态数据分组。"""

        contexts = [
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

    def test_backend_module_binds_exact_stage_task_ids(self):
        """后端模块按阶段清单编译，任务 ID 不依赖模型标题。"""

        context = _backend_endpoint_context("database", "external_api")
        drafts = backend_drafts(context)
        bound = self.parse(context, list(reversed(drafts)))

        expected = {record["task_id"] for record in backend_stage_manifest(context)}
        # 编译顺序由阶段清单决定，与草稿顺序无关；只校验身份集合与数量。
        self.assertEqual({task["id"] for task in bound}, expected)
        self.assertEqual(len(bound), len(expected))
        # 草稿正文不被就地改写，编译结果独立返回。
        self.assertEqual(drafts[0]["key"], backend_stage_manifest(context)[0]["key"])

    def test_existing_ids_and_other_fields_are_preserved(self):
        """合法混合候选只补缺失 ID，其他字段连同非法内容原样交给后续校验。"""

        # 页面 Unit 只有一个任务时，另造第二个任务以验证"齐全 + 非法字段原样保留"。
        context = _context()
        tasks = tasks_for(context)
        tasks[0]["id"] = next(iter(unit_task_identity_manifest(context)))
        tasks.append({**deepcopy(tasks[0]), "id": "", "owner": "wrong-owner"})
        tasks[1].pop("id")
        with self.assertRaises(RawUnitCandidateParseError):
            # 剩余职责已被占满，多出来的任务无法绑定 → 显式拒绝而不是静默丢弃。
            self.parse(context, tasks)
        single = tasks_for(context)
        single[0]["owner"] = "wrong-owner"
        result = self.parse(context, single)
        self.assertEqual(result[0]["id"], next(iter(unit_task_identity_manifest(context))))
        self.assertEqual(result[0]["owner"], "wrong-owner")

    def test_backend_module_rejects_unknown_or_duplicate_keys(self):
        """后端模块只接受清单内的唯一 key；未知、重复或缺少文件都拒绝。"""

        context = _backend_endpoint_context()
        base = backend_drafts(context)
        cases = {
            "unknown_key": [*base, {**deepcopy(base[0]), "key": "unknown"}],
            "duplicate_key": [*base, deepcopy(base[0])],
            "missing_files": [{**deepcopy(base[0]), "files": []}, base[1]],
            "extra_field": [{**deepcopy(base[0]), "owner": "x"}, base[1]],
        }
        for name, tasks in cases.items():
            before = deepcopy(tasks)
            with self.subTest(case=name), self.assertRaises(RawUnitCandidateParseError) as caught:
                self.parse(context, tasks)
            self.assertEqual(tasks, before)
            self.assertTrue(all(issue.retryable for issue in caught.exception.issues))

    def test_backend_module_rejects_uncovered_requirements(self):
        """covers 必须覆盖该阶段全部职责序号，漏项或越界都拒绝。"""

        context = _backend_endpoint_context()
        base = backend_drafts(context)
        first = backend_stage_manifest(context)[0]
        for name, covers in (
            ("missing", list(range(1, len(first["requirement_ids"])))),
            ("out_of_range", [len(first["requirement_ids"]) + 1]),
        ):
            tasks = deepcopy(base)
            tasks[0]["files"][0]["covers"] = covers
            with self.subTest(case=name), self.assertRaises(RawUnitCandidateParseError):
                self.parse(context, tasks)

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
        """增量候选只绑定当前职责，既有阶段只作为同 Unit 依赖保留。"""

        payload = _backend_endpoint_context().model_dump(mode="json")
        payload["generation_requirements"] = payload["generation_requirements"][-1:]
        unit_id = payload["unit_id"]
        payload["dependency_context"]["retained_task_summaries"] = [
            {"id": f"{unit_id}::service", "unit_id": unit_id},
        ]
        context = UnitGenerationContext.model_validate(payload)
        records = backend_stage_manifest(context)
        drafts = [{
            "key": record["key"],
            "title": f"实现 {record['key']}",
            "files": [{
                "path": "backend/src/main/java/com/cmbchina/backend/OrdersController.java",
                "operation": "add",
                "description": "实现控制器职责",
                "covers": list(range(1, len(record["requirement_ids"]) + 1)),
            }],
        } for record in records]

        result = self.parse(context, drafts)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], records[0]["task_id"])

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
