"""通用 ChangeSet 与操作执行器的结构、原子性和全局引用约束回归。"""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from pydantic import TypeAdapter, ValidationError

from app.domain.application_planning_recovery import application_planning_sha256
from app.domain.technical_plan_changeset import LogicalOperation, RevisionOperationBatch, TechnicalPlanChangeSet
from app.services.technical_plan_changeset import apply_technical_plan_changeset, revision_document, verify_revision_effects
from app.services.technical_plan_content_changes import technical_plan_content_errors
from app.services.technical_plan_logical_patch import apply_logical_operation

_ENDPOINT_ID = "greeting_contract.get_morning_greeting"
_OTHER_ENDPOINT_ID = "greeting_contract.unrelated"


def _base_plan() -> dict:
    """构造一个含独立类型、稳定接口及实体字段的当前技术规划。"""

    return {
        "artifact_type": "technical-plan",
        "architecture": {"frontend": "前端 React 应用", "backend": "后端 Springboot 服务", "data": "MySQL8 数据库"},
        "entities": [{"id": "greeting", "name": "问候语", "fields": [{"name": "email", "type": "string"}]}],
        "api_contracts": [{
            "id": "greeting_contract", "name": "问候语", "base_path": "/api/greetings",
            "entity_ids": ["greeting"], "authentication": {"required": False},
            "schemas": {
                "MorningGreetingResponse": {"type": "object", "properties": {"morningGreetingText": {"type": "string", "description": "早上好"}}},
                "OtherResponse": {"type": "object", "properties": {}},
            },
            "endpoints": [
                {"id": _ENDPOINT_ID, "name": "获取问候语", "method": "GET", "path": "/api/greetings/morning", "summary": "基线说明", "response_schema_ref": "MorningGreetingResponse", "error_codes": ["INTERNAL_SERVER_ERROR"]},
                {"id": _OTHER_ENDPOINT_ID, "name": "无关接口", "method": "GET", "path": "/api/greetings/other", "summary": "无关接口原说明", "response_schema_ref": "OtherResponse", "error_codes": ["INTERNAL_SERVER_ERROR"]},
            ],
        }],
        "pages": [{"pageId": "home", "references": {"endpoint_dependencies": [{"endpoint_id": _ENDPOINT_ID}], "action_implementations": []}}],
    }


def _contract(plan: dict) -> dict:
    """读取测试接口分组。"""

    return plan["api_contracts"][0]


def _endpoint(plan: dict, endpoint_id: str = _ENDPOINT_ID) -> dict:
    """通过稳定身份读取测试接口。"""

    return next(item for item in _contract(plan)["endpoints"] if item["id"] == endpoint_id)


def _changeset(plan: dict, source: str, operations: list[dict]) -> dict:
    """为同一草稿构造严格协议输入，不使用整份候选替代操作。"""

    return {"schemaVersion": 1, "baseSha256": application_planning_sha256(revision_document(plan)),
            "intents": [{"id": "intent-1", "sourceText": source, "operations": operations}]}


class ChangeSetTests(unittest.TestCase):
    """验证协议固定、所有子需求落实及整批回滚性质。"""

    def setUp(self) -> None:
        """为每项测试准备独立的当前文档。"""

        self.plan = _base_plan()
        self.document = revision_document(self.plan)
        self.path = f"/api_contracts/greeting_contract/endpoints/{_ENDPOINT_ID}/summary"

    def test_schema_rejects_old_shape_missing_fields_and_extra_keys(self) -> None:
        """只接受固定协议，显式 null 合法但遗漏字段非法。"""

        valid = _changeset(self.plan, "改说明", [{"op": "replace", "path": self.path, "expectedBefore": "基线说明", "value": None}])
        self.assertIsNone(TechnicalPlanChangeSet.model_validate(valid).intents[0].operations[0].value)
        for edit in ("missing", "extra", "version", "candidate", "test-only"):
            invalid = deepcopy(valid)
            if edit == "missing": invalid["intents"][0]["operations"][0].pop("expectedBefore")
            if edit == "extra": invalid["intents"][0]["operations"][0]["from"] = "/x"
            if edit == "version": invalid["schemaVersion"] = True
            if edit == "candidate": invalid["entities"] = []
            if edit == "test-only": invalid["intents"][0]["operations"] = [{"op": "test", "path": self.path, "value": "基线说明"}]
            with self.subTest(edit=edit), self.assertRaises(ValidationError):
                TechnicalPlanChangeSet.model_validate(invalid)

    def test_multiple_changes_share_platform_bound_request(self) -> None:
        """多项修改共用一个平台绑定事务，所有未涉及子树完全不变。"""

        request = "修改请求体；修改基础路径；删除无关接口"
        operations = [
            {"op": "add", "path": "/api_contracts/greeting_contract/schemas/NewRequest", "value": {"type": "object", "properties": {}}},
            {"op": "add", "path": f"/api_contracts/greeting_contract/endpoints/{_ENDPOINT_ID}/request_schema_ref", "value": "NewRequest"},
            {"op": "replace", "path": "/api_contracts/greeting_contract/base_path", "expectedBefore": "/api/greetings", "value": "/api/new"},
            {"op": "remove", "path": f"/api_contracts/greeting_contract/endpoints/{_OTHER_ENDPOINT_ID}", "expectedBefore": _endpoint(self.plan, _OTHER_ENDPOINT_ID)},
        ]
        changeset = _changeset(self.plan, request, operations)
        original = deepcopy(self.document)
        after, _effects = apply_technical_plan_changeset(self.document, changeset, request=request)
        self.assertEqual(self.document, original)
        self.assertEqual(after["entities"], original["entities"])
        self.assertEqual(after["pages"], original["pages"])
        self.assertEqual(_contract(after)["base_path"], "/api/new")
        self.assertEqual(len(_contract(after)["endpoints"]), 1)
        self.assertEqual(_endpoint(after)["request_schema_ref"], "NewRequest")

    def test_failure_rolls_back_even_after_valid_first_operation(self) -> None:
        """任一步失败整份传入草稿不变，没有部分成功。"""

        original = deepcopy(self.document)
        operations = [{"op": "replace", "path": self.path, "expectedBefore": "基线说明", "value": "新说明"},
                      {"op": "remove", "path": "/entities/missing", "expectedBefore": None}]
        with self.assertRaisesRegex(ValueError, "不存在"):
            apply_technical_plan_changeset(self.document, _changeset(self.plan, "改说明并删除", operations), request="改说明并删除")
        self.assertEqual(self.document, original)

    def test_noop_missing_intent_stale_base_and_conflicts_fail(self) -> None:
        """一项有变化不能掩盖另一项无变化、漏项、过期草稿或互相覆盖。"""

        op = {"op": "replace", "path": self.path, "expectedBefore": "基线说明", "value": "新说明"}
        for case in ("noop", "omitted", "stale", "conflict"):
            request = "改说明；删除字段"
            value = _changeset(self.plan, request, [op])
            if case == "noop": value["intents"][0]["operations"][0]["value"] = "基线说明"
            if case == "omitted": value["intents"][0]["sourceText"] = "改说明"
            if case == "stale": value["baseSha256"] = "0" * 64
            if case == "conflict":
                value["intents"][0]["sourceText"] = "改说明"
                value["intents"].append({"id": "i-2", "sourceText": "删除字段", "operations": [{**op, "expectedBefore": "新说明", "value": "冲突说明"}]})
            with self.subTest(case=case), self.assertRaises(ValueError):
                apply_technical_plan_changeset(self.document, value, request=request)

    def test_delete_field_and_cleanup_reference_use_same_protocol(self) -> None:
        """实体字段删除与引用解除不需要实体/邮箱专用合并器。"""

        _contract(self.document)["schemas"]["MorningGreetingResponse"]["properties"]["email"] = {"type": "string", "entity_field_ref": "greeting.email"}
        operations = [{"op": "remove", "path": "/entities/greeting/fields/email", "expectedBefore": {"name": "email", "type": "string"}}]
        changeset = _changeset(self.document, "删除邮箱", operations)
        after, effects = apply_technical_plan_changeset(self.document, changeset, request="删除邮箱")
        self.assertTrue(any("实体字段" in error for error in technical_plan_content_errors(after)))
        operations.append({"op": "remove", "path": "/api_contracts/greeting_contract/schemas/MorningGreetingResponse/properties/email/entity_field_ref", "expectedBefore": "greeting.email"})
        after, effects = apply_technical_plan_changeset(self.document, _changeset(self.document, "删除邮箱", operations), request="删除邮箱")
        self.assertEqual(technical_plan_content_errors(after), [])
        resurrected = deepcopy(after)
        resurrected["entities"][0]["fields"] = self.document["entities"][0]["fields"]
        with self.assertRaisesRegex(ValueError, "覆盖"):
            verify_revision_effects(resurrected, effects)

    def test_logical_paths_escape_keys_reject_indexes_and_require_existing_null(self) -> None:
        """稳定路径严格区分缺失/null，拒绝数组下标，转义字面键可正常修改。"""

        self.document["architecture"]["a/b~c"] = None
        op = TypeAdapter(LogicalOperation).validate_python({"op": "replace", "path": "/architecture/a~1b~0c", "expectedBefore": None, "value": "值"})
        apply_logical_operation(self.document, op)
        self.assertEqual(self.document["architecture"]["a/b~c"], "值")
        for path in ("/entities/0/name", "/architecture/missing", "/architecture/a~2b", "/architecture//x"):
            op = TypeAdapter(LogicalOperation).validate_python({"op": "replace", "path": path, "expectedBefore": None, "value": "值"})
            with self.subTest(path=path), self.assertRaises(ValueError):
                apply_logical_operation(self.document, op)

    def test_identity_prefix_error_explains_bare_identity_path(self):
        """id=/name= 前缀必须给出明确纠正，不能误报成旧应用缺少目标节点。"""
        path = "/api_contracts/id=greeting_contract/schemas/MorningGreetingResponse/properties/morningGreetingText"
        operation = {
            "op": "remove", "path": path,
            "expectedBefore": {"type": "string", "description": "早上好"},
        }
        with self.assertRaisesRegex(ValueError, "只写身份值本身.*greeting_contract"):
            apply_technical_plan_changeset(
                self.document, _changeset(self.plan, "删除响应字段", [operation]), request="删除响应字段",
            )

    def test_bracketed_identity_and_index_paths_get_actionable_errors(self):
        """方括号身份和数字下标不得再退化为缺少节点的模糊错误。"""
        for segment in ("[id=greeting_contract]", "[0]"):
            with self.subTest(segment=segment):
                path = f"/api_contracts/{segment}/schemas/MorningGreetingResponse/properties/morningGreetingText"
                operation = {
                    "op": "remove", "path": path,
                    "expectedBefore": {"type": "string", "description": "早上好"},
                }
                with self.assertRaisesRegex(ValueError, "身份值本身|数字下标"):
                    apply_technical_plan_changeset(
                        self.document, _changeset(self.plan, "删除响应字段", [operation]), request="删除响应字段",
                    )

    def test_changeset_error_does_not_embed_full_original_request(self):
        """超长迭代上下文不得淹没真正的路径错误，界面诊断应保持简短可读。"""
        request = "原始需求：\n" + "旧应用上下文" * 2000
        operation = {
            "op": "remove", "path": "/entities/id=greeting/fields/email",
            "expectedBefore": {"name": "email", "type": "string"},
        }
        with self.assertRaises(ValueError) as raised:
            apply_technical_plan_changeset(
                self.document, _changeset(self.plan, request, [operation]), request=request,
            )
        message = str(raised.exception)
        self.assertLess(len(message), 500)
        self.assertIn("只写身份值本身", message)
        self.assertNotIn("旧应用上下文", message)

    def test_revision_prompt_shows_bare_identity_and_response_only_scope(self):
        """提示词必须给出正确路径示例，并防止只删响应字段时连带删除实体。"""
        from app.agents.main.technical_plan_revision import _changeset_prompt

        prompt = _changeset_prompt({}, self.document, "删除响应体 list 中的 phone 字段", [])
        self.assertIn("只写身份值本身，禁止写成 id=", prompt)
        self.assertIn("/api_contracts/personnel_detail_contract/schemas/PersonnelDetailListItem/properties/phone", prompt)
        self.assertIn("/parameters/query.current/required", prompt)
        self.assertIn("只删除接口响应体字段时，不要删除实体字段", prompt)

    def test_parameter_bare_name_error_identifies_composite_identity(self):
        """查询参数存在时，裸名称应指向正确的 in.name 路径而不是误报目标缺失。"""
        _endpoint(self.document)["parameters"] = [
            {"name": "current", "in": "query", "required": False, "schema": {"type": "integer", "default": 1}},
            {"name": "pageSize", "in": "query", "required": False, "schema": {"type": "integer", "default": 20}},
        ]
        endpoint_path = f"/api_contracts/greeting_contract/endpoints/{_ENDPOINT_ID}/parameters"
        for name in ("current", "pageSize"):
            with self.subTest(name=name):
                operation = {
                    "op": "replace", "path": f"{endpoint_path}/{name}/required",
                    "expectedBefore": False, "value": True,
                }
                with self.assertRaisesRegex(ValueError, f"query\\.{name}"):
                    apply_technical_plan_changeset(
                        self.document, _changeset(self.document, "查询参数改为必填", [operation]),
                        request="查询参数改为必填",
                    )

        operations = [
            {
                "op": "replace", "path": f"{endpoint_path}/query.{name}/required",
                "expectedBefore": False, "value": True,
            }
            for name in ("current", "pageSize")
        ]
        after, _effects = apply_technical_plan_changeset(
            self.document, _changeset(self.document, "查询参数改为必填", operations),
            request="查询参数改为必填",
        )
        parameters = _endpoint(after)["parameters"]
        self.assertEqual([item["required"] for item in parameters], [True, True])
        self.assertEqual([item["schema"]["default"] for item in parameters], [1, 20])

    def test_response_schema_field_delete_preserves_entity(self):
        """按正确稳定路径删除响应字段时，实体字段保持原样。"""
        properties = _contract(self.document)["schemas"]["MorningGreetingResponse"]["properties"]
        properties["phone"] = {"type": "string", "description": "联系电话"}
        path = "/api_contracts/greeting_contract/schemas/MorningGreetingResponse/properties/phone"
        operation = {"op": "remove", "path": path, "expectedBefore": properties["phone"]}
        after, _effects = apply_technical_plan_changeset(
            self.document, _changeset(self.document, "只删除响应 phone", [operation]), request="只删除响应 phone",
        )
        self.assertNotIn("phone", _contract(after)["schemas"]["MorningGreetingResponse"]["properties"])
        self.assertEqual(after["entities"], self.document["entities"])

    def test_move_and_test_use_atomic_existing_values(self) -> None:
        """字段改名使用 move，前置 test 失败与移动失败均不暴露半修改。"""

        source = "/api_contracts/greeting_contract/schemas/MorningGreetingResponse/properties/morningGreetingText"
        target = source.rsplit("/", 1)[0] + "/greetingText"
        value = _contract(self.document)["schemas"]["MorningGreetingResponse"]["properties"]["morningGreetingText"]
        operations = [{"op": "test", "path": source, "value": value}, {"op": "move", "from": source, "path": target, "expectedBefore": value}]
        after, _effects = apply_technical_plan_changeset(self.document, _changeset(self.plan, "字段改名", operations), request="字段改名")
        self.assertIn("greetingText", _contract(after)["schemas"]["MorningGreetingResponse"]["properties"])
        self.assertNotIn("morningGreetingText", _contract(after)["schemas"]["MorningGreetingResponse"]["properties"])

    def test_platform_binds_full_multi_requirement_request_after_model_operations(self) -> None:
        """模型无需复制自然语言，多项字段、列表和弹框修改仍绑定同一完整请求。"""

        request = "我想在人员实体添加姓名和岗位的字段，列表中显示姓名、岗位信息，另外新增弹框也需要填写姓名和岗位信息"
        batch = RevisionOperationBatch.model_validate({
            "schemaVersion": 1,
            "baseSha256": application_planning_sha256(self.document),
            "operations": [
                {"op": "replace", "path": "/architecture/frontend", "expectedBefore": "前端 React 应用", "value": "前端包含姓名和岗位"},
                {"op": "replace", "path": "/architecture/backend", "expectedBefore": "后端 Springboot 服务", "value": "后端保存姓名和岗位"},
                {"op": "replace", "path": "/architecture/data", "expectedBefore": "MySQL8 数据库", "value": "人员记录包含姓名和岗位"},
            ],
        })
        bound = batch.bind_request(request)
        self.assertEqual(len(bound.intents), 1)
        self.assertEqual(bound.intents[0].id, "current-request")
        self.assertEqual(bound.intents[0].source_text, request)
        after, _effects = apply_technical_plan_changeset(self.document, bound, request=request)
        self.assertEqual(after["architecture"]["frontend"], "前端包含姓名和岗位")
        self.assertEqual(after["architecture"]["backend"], "后端保存姓名和岗位")
        self.assertEqual(after["architecture"]["data"], "人员记录包含姓名和岗位")

    def test_native_structured_output_is_validated_not_full_candidate_fallback(self) -> None:
        """OpenAI直连关闭思考后使用函数调用，其余通道保持各自协议参数。"""

        from app.agents.main.technical_plan_revision import plan_technical_plan_changeset
        changeset = _changeset(self.plan, "改说明", [{"op": "replace", "path": self.path, "expectedBefore": "基线说明", "value": "新说明"}])
        batch = RevisionOperationBatch.model_validate({
            "schemaVersion": changeset["schemaVersion"], "baseSha256": changeset["baseSha256"],
            "operations": changeset["intents"][0]["operations"],
        })
        expected = TechnicalPlanChangeSet.model_validate({
            "schemaVersion": changeset["schemaVersion"], "baseSha256": changeset["baseSha256"],
            "intents": [{"id": "current-request", "sourceText": "改说明", "operations": changeset["intents"][0]["operations"]}],
        })
        for transport, provider in (
            ("direct", "openai"),
            ("direct", "anthropic"),
            ("transmit", "openai"),
        ):
            with self.subTest(transport=transport, provider=provider):
                model = MagicMock()
                model.with_structured_output.return_value.invoke.return_value = {
                    "raw": MagicMock(response_metadata={}), "parsed": batch, "parsing_error": None,
                }
                settings = SimpleNamespace(model_transport=transport, model_provider=provider)
                with (
                    patch(
                        "app.agents.main.technical_plan_revision.create_chat_model", return_value=model,
                    ) as create_model,
                    patch("app.agents.main.technical_plan_revision.Settings.from_env", return_value=settings),
                    patch("app.agents.main.technical_plan_revision.thinking_disabled_kwargs", return_value={}),
                ):
                    result = plan_technical_plan_changeset({}, self.document, "改说明")
                self.assertEqual(result, expected)
                model_kwargs = (
                    {"thinking": {"type": "disabled"}}
                    if transport == "direct" and provider == "openai" else {}
                )
                create_model.assert_called_once_with(settings, extra_model_kwargs=model_kwargs)
                options = {"include_raw": True}
                if transport == "direct":
                    options["method"] = "function_calling"
                model.with_structured_output.assert_called_once_with(RevisionOperationBatch, **options)


if __name__ == "__main__":
    unittest.main()
