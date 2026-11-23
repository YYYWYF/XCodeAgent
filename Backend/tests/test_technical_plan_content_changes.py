"""已确认需求的明确文本替换与模型润色的边界回归。"""

from __future__ import annotations

import json
import unittest

from app.services.technical_plan_content_changes import (
    direct_endpoint_revision_targets, direct_revision_schema_targets,
)
from app.services.technical_plan_merger import (
    merge_direct_endpoint_revision, merge_technical_plan, relevant_object_ids,
    strip_change_status, technical_plan_revision_diff,
)


_ENDPOINT_ID = "greeting_contract.get_morning_greeting"
_OTHER_ENDPOINT_ID = "greeting_contract.unrelated"


def _base_plan() -> dict:
    """构造一个页面只依赖问候接口、同分组另有无关接口的基线。"""

    return {
        "entities": [],
        "api_contracts": [{
            "id": "greeting_contract",
            "name": "问候语",
            "base_path": "/api/greetings",
            "entity_ids": ["greeting"],
            "authentication": {"required": False},
            "schemas": {
                "MorningGreetingResponse": {
                    "type": "object",
                    "description": "欢迎页问候响应",
                    "properties": {
                        "morningGreetingText": {
                            "type": "string",
                            "description": "固定内容为“早上好！”",
                        }
                    },
                },
                "OtherResponse": {
                    "type": "object",
                    "description": "无关响应",
                    "properties": {},
                },
            },
            "endpoints": [
                {
                    "id": _ENDPOINT_ID,
                    "name": "获取问候语",
                    "method": "GET",
                    "path": "/api/greetings/morning",
                    "summary": "获取欢迎页展示的问候字符串“早上好！”",
                    "response_schema_ref": "MorningGreetingResponse",
                    "error_codes": ["INTERNAL_SERVER_ERROR"],
                    "authentication": {"required": False},
                },
                {
                    "id": _OTHER_ENDPOINT_ID,
                    "name": "无关接口",
                    "method": "GET",
                    "path": "/api/greetings/other",
                    "summary": "无关接口原说明",
                    "response_schema_ref": "OtherResponse",
                    "error_codes": ["INTERNAL_SERVER_ERROR"],
                    "authentication": {"required": False},
                },
            ],
        }],
        "pages": [{
            "pageId": "home",
            "references": {
                "endpoint_dependencies": [{"endpoint_id": _ENDPOINT_ID}],
                "action_implementations": [],
            },
        }],
    }


def _requirement(description: str) -> dict:
    """构造只含欢迎页描述的 RequirementSpec。"""

    return {"pages": [{"pageId": "home", "name": "欢迎页", "description": description}]}


def _old_requirement() -> dict:
    """返回包含旧问候值的基线需求。"""

    return _requirement("进入页面时请求接口获取字符串“早上好！”并展示。")


def _new_requirement() -> dict:
    """返回把旧问候值明确替换为 hello world 的本轮需求。"""

    return _requirement("进入页面时请求接口获取字符串“hello world”并展示。")


def _merge(base: dict, candidate: dict, current: dict | None = None) -> dict:
    """按真实调用方式计算白名单并合并技术规划。"""

    return merge_technical_plan(
        base,
        candidate,
        relevant=relevant_object_ids(
            current or _new_requirement(), None, base, _old_requirement()
        ),
    )


def _contract(plan: dict) -> dict:
    """读取合并结果中的问候 Contract。"""

    return plan["api_contracts"][0]


def _endpoint(plan: dict, endpoint_id: str = _ENDPOINT_ID) -> dict:
    """按 ID 读取合并结果中的接口。"""

    return next(item for item in _contract(plan)["endpoints"] if item["id"] == endpoint_id)


class DirectEndpointRevisionTests(unittest.TestCase):
    """直接技术修订只允许精确点名的接口和独占类型发生变化。"""

    def _targets(self, request: str, base: dict) -> dict:
        """解析测试目标并断言没有匹配失败。"""

        targets, errors = direct_endpoint_revision_targets(request, base)
        self.assertEqual(errors, [])
        return targets

    def test_similar_path_is_not_an_alias_and_missing_target_stops(self) -> None:
        """不同英文路径不自动匹配，即使另一项可以匹配也要报告全部错误。"""

        targets, errors = direct_endpoint_revision_targets(
            "1、/api/greetings/morning 接口改响应 2、删除 /api/greeting/other 接口", _base_plan(),
        )
        self.assertEqual(set(targets), {_ENDPOINT_ID})
        self.assertIn("未匹配", errors[0])
        self.assertIn("/api/greeting/other", errors[0])

    def test_same_path_requires_method_and_does_not_match_children(self) -> None:
        """同路径多个方法不能猜测，明确 POST 后也不会授权 GET 或子路径。"""

        base = _base_plan()
        post = {**_endpoint(base), "id": "greeting_contract.post", "method": "POST"}
        _contract(base)["endpoints"].append(post)
        _, errors = direct_endpoint_revision_targets("/api/greetings/morning 接口改响应", base)
        self.assertIn("歧义", errors[0])
        self.assertIn("GET、POST", errors[0])
        targets = self._targets("POST `/api/greetings/morning` 接口改响应", base)
        self.assertEqual(set(targets), {post["id"]})

    def test_missing_method_and_conflicting_deletion_are_reported(self) -> None:
        """方法不匹配和同一接口同时修改删除均不能交给模型自行选择。"""

        _, errors = direct_endpoint_revision_targets("POST /api/greetings/morning 接口改响应", _base_plan())
        self.assertIn("未匹配", errors[0])
        _, errors = direct_endpoint_revision_targets(
            "/api/greetings/morning 接口改响应；删除 /api/greetings/morning 接口", _base_plan(),
        )
        self.assertIn("冲突", errors[0])

    def test_delete_authorization_is_not_inferred_from_negative_or_response_fields(self) -> None:
        """不要删除接口与删除响应字段都只允许修改，不授予删除接口权限。"""

        base = _base_plan()
        for request in (
            "不要删除 GET /api/greetings/morning 接口，只改说明",
            "GET /api/greetings/morning 接口删除响应里的分页字段",
            "GET /api/greetings/morning 接口删除 响应里的分页字段",
        ):
            with self.subTest(request=request):
                self.assertFalse(self._targets(request, base)[_ENDPOINT_ID]["deleted"])
        self.assertTrue(self._targets("/api/greetings/morning 接口删除", base)[_ENDPOINT_ID]["deleted"])

    def test_model_generated_root_path_supports_nested_response_field_removal(self) -> None:
        """模型生成的无 /api 前缀路径也可精确修订嵌套响应字段并标为修改。"""

        base = _base_plan()
        _endpoint(base).update({
            "name": "分页查询人员明细",
            "path": "/personnel",
            "response_schema_ref": "PageResult",
        })
        _contract(base)["schemas"].update({
            "PageResult": {
                "type": "object",
                "properties": {
                    "list": {
                        "type": "array",
                        "items": {"$ref": "PersonnelListItem"},
                    },
                },
            },
            "PersonnelListItem": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string", "description": "人员的联系电话号码。"},
                },
            },
        })
        targets = self._targets(
            "分页查询人员明细/personnel接口的响应体的list中去除phone字段", base,
        )
        self.assertEqual(targets[_ENDPOINT_ID]["path"], "/personnel")
        self.assertFalse(targets[_ENDPOINT_ID]["deleted"])

        candidate = json.loads(json.dumps(base))
        properties = _contract(candidate)["schemas"]["PersonnelListItem"]["properties"]
        properties.pop("phone")
        merged = merge_direct_endpoint_revision(base, candidate, targets)

        self.assertNotIn(
            "phone", _contract(merged)["schemas"]["PersonnelListItem"]["properties"],
        )
        self.assertEqual(_endpoint(merged)["_change_status"], "modified")
        self.assertEqual(_endpoint(merged, _OTHER_ENDPOINT_ID)["_change_status"], "unchanged")

    def test_only_unrequested_edits_cannot_become_successful_unchanged_candidate(self) -> None:
        """只改错对象后全部回退必须报告未落实，显式无变更复核仍保持原语义。"""

        base = _base_plan()
        targets = self._targets("/api/greetings/morning 接口改说明", base)
        candidate = json.loads(json.dumps(base))
        _endpoint(candidate, _OTHER_ENDPOINT_ID)["summary"] = "模型改错接口"
        with self.assertRaisesRegex(ValueError, "未产生有效变更"):
            merge_direct_endpoint_revision(base, candidate, targets)
        merged = merge_direct_endpoint_revision(base, base, targets)
        self.assertEqual(strip_change_status(merged), base)

    def test_named_schema_field_change_preserves_other_types_and_marks_its_endpoint(self) -> None:
        """原有点名类型字段修改仍能落地，不要求用户改写成接口路径指令。"""

        base = _base_plan()
        request = "把 MorningGreetingResponse 中的 morningGreetingText 改成 greetingText"
        schemas, errors = direct_revision_schema_targets(request, base)
        self.assertEqual(errors, [])
        self.assertEqual(schemas, {"greeting_contract": {"MorningGreetingResponse"}})
        candidate = json.loads(json.dumps(base))
        props = _contract(candidate)["schemas"]["MorningGreetingResponse"]["properties"]
        props["greetingText"] = props.pop("morningGreetingText")
        _contract(candidate)["schemas"]["OtherResponse"]["description"] = "模型润色"
        merged = merge_direct_endpoint_revision(base, candidate, {}, schema_targets=schemas)
        self.assertEqual(_endpoint(merged)["_change_status"], "modified")
        self.assertEqual(_endpoint(merged, _OTHER_ENDPOINT_ID)["_change_status"], "unchanged")
        self.assertIn("greetingText", _contract(merged)["schemas"]["MorningGreetingResponse"]["properties"])
        self.assertEqual(_contract(merged)["schemas"]["OtherResponse"], _contract(base)["schemas"]["OtherResponse"])

    def test_named_schema_permissions_are_not_derived_from_similar_names(self) -> None:
        """类型原文名称不精确或同名跨组时，不能扩大授权范围。"""

        base = _base_plan()
        targets, errors = direct_revision_schema_targets("修改 MorningGreetingResponseExtra", base)
        self.assertEqual((targets, errors), ({}, []))
        base["api_contracts"].append({**_contract(base), "id": "duplicate_contract"})
        targets, errors = direct_revision_schema_targets("修改 MorningGreetingResponse", base)
        self.assertEqual(targets, {})
        self.assertIn("歧义", errors[0])

    def test_platform_scope_prompt_keeps_user_request_and_shared_schema_instruction(self) -> None:
        """平台范围作为独立模型约束投射，不替换用户修改意见或全量校验反馈。"""

        from app.agents.main.technical_plan_feedback import technical_plan_feedback_prompt
        prompt = technical_plan_feedback_prompt({
            "planning_adjustment_request": "本次只改提交状态",
            "technical_plan_validation_feedback": "当前类型引用未闭合",
            "technical_plan_revision_scope": {"endpoints": [{"method": "POST", "path": "/api/greetings"}]},
        })
        self.assertIn("本次只改提交状态", prompt)
        self.assertIn("当前类型引用未闭合", prompt)
        self.assertIn("拆分独立类型", prompt)
        self.assertIn("/api/greetings", prompt)

    def test_schema_response_change_is_adopted_without_other_rewrites(self) -> None:
        """修改独占响应类型时接口标修改，无关类型、页面、外壳及新增接口不采纳。"""

        base = _base_plan()
        candidate = json.loads(json.dumps(base))
        _contract(candidate)["schemas"]["MorningGreetingResponse"]["properties"] = {"status": {"type": "boolean"}}
        _contract(candidate)["schemas"]["OtherResponse"]["description"] = "未授权润色"
        _contract(candidate)["name"] = "模型更名"
        _endpoint(candidate, _OTHER_ENDPOINT_ID)["summary"] = "未授权润色"
        _contract(candidate)["endpoints"].append({**_endpoint(candidate), "id": "unrequested", "path": "/api/new"})
        candidate["pages"][0]["references"]["endpoint_dependencies"] = []
        merged = merge_direct_endpoint_revision(base, candidate, self._targets("/api/greetings/morning 接口响应只留状态", base))
        self.assertEqual(_endpoint(merged)["_change_status"], "modified")
        self.assertEqual(_endpoint(merged, _OTHER_ENDPOINT_ID)["_change_status"], "unchanged")
        clean = strip_change_status(merged)
        self.assertEqual(clean["pages"], base["pages"])
        self.assertEqual(_contract(clean)["name"], _contract(base)["name"])
        self.assertEqual(len(_contract(clean)["endpoints"]), 2)
        self.assertEqual(_contract(clean)["schemas"]["OtherResponse"], _contract(base)["schemas"]["OtherResponse"])
        self.assertEqual(_contract(clean)["schemas"]["MorningGreetingResponse"]["properties"], {"status": {"type": "boolean"}})

    def test_explicit_deletion_survives_model_omission_and_preserves_other_endpoint(self) -> None:
        """仅删除点名接口，模型省略其它接口或整个分组也不能扩大删除范围。"""

        base = _base_plan()
        targets = self._targets("删除 GET /api/greetings/morning 接口", base)
        merged = merge_direct_endpoint_revision(base, {**base, "api_contracts": []}, targets)
        self.assertEqual(_endpoint(merged)["_change_status"], "deleted")
        self.assertEqual(_endpoint(merged, _OTHER_ENDPOINT_ID)["_change_status"], "unchanged")
        self.assertEqual(_contract(merged)["schemas"], _contract(base)["schemas"])
        self.assertEqual(_endpoint(strip_change_status(merged))["_change_status"], "deleted")

    def test_non_target_interface_identity_is_protected(self) -> None:
        """目标方法或路径被改写、或修改目标被模型省略时必须报告未完成。"""

        base = _base_plan()
        targets = self._targets("/api/greetings/morning 接口改响应", base)
        for change in ({"path": "/api/greetings/other"}, {"method": "POST"}):
            with self.subTest(change=change):
                candidate = json.loads(json.dumps(base))
                _endpoint(candidate).update(change)
                with self.assertRaisesRegex(ValueError, "身份被改写"):
                    merge_direct_endpoint_revision(base, candidate, targets)
        with self.assertRaisesRegex(ValueError, "未完成修改"):
            merge_direct_endpoint_revision(base, {**base, "api_contracts": []}, targets)

    def test_shared_nested_schema_cannot_change_unrequested_endpoint(self) -> None:
        """间接共享类型也受保护；目标改用独立类型后可受控采纳。"""

        base = _base_plan()
        schemas = _contract(base)["schemas"]
        schemas["SharedStatus"] = {"type": "string"}
        schemas["MorningGreetingResponse"]["properties"] = {"status": {"$ref": "SharedStatus"}}
        schemas["OtherResponse"]["properties"] = {"status": {"$ref": "SharedStatus"}}
        candidate = json.loads(json.dumps(base))
        _contract(candidate)["schemas"]["SharedStatus"] = {"type": "boolean"}
        targets = self._targets("/api/greetings/morning 接口改响应", base)
        with self.assertRaisesRegex(ValueError, "共享类型"):
            merge_direct_endpoint_revision(base, candidate, targets)
        _contract(candidate)["schemas"]["OwnStatus"] = {"type": "boolean"}
        _contract(candidate)["schemas"]["MorningGreetingResponse"]["properties"]["status"] = {"$ref": "OwnStatus"}
        merged = merge_direct_endpoint_revision(base, candidate, targets)
        self.assertEqual(_contract(merged)["schemas"]["SharedStatus"], {"type": "string"})
        self.assertEqual(_contract(merged)["schemas"]["OwnStatus"], {"type": "boolean"})
        self.assertEqual(_endpoint(merged, _OTHER_ENDPOINT_ID)["_change_status"], "unchanged")

    def test_review_baseline_does_not_revert_prior_revision(self) -> None:
        """连续修订沿当前事务基线保存前次内容，展示仍相对迭代初始版本。"""

        original = _base_plan()
        current = json.loads(json.dumps(original))
        _endpoint(current, _OTHER_ENDPOINT_ID)["summary"] = "前次已确认修改"
        candidate = json.loads(json.dumps(current))
        _endpoint(candidate)["summary"] = "本次修改"
        controlled = merge_direct_endpoint_revision(current, candidate, self._targets("/api/greetings/morning 接口改说明", current))
        display = technical_plan_revision_diff(original, controlled)
        self.assertEqual(_endpoint(display, _OTHER_ENDPOINT_ID)["summary"], "前次已确认修改")
        self.assertEqual(_endpoint(display, _OTHER_ENDPOINT_ID)["_change_status"], "modified")


class ConfirmedContentChangeTests(unittest.TestCase):
    """只有可核对的需求差异才能改变既有接口内容。"""

    def test_explicit_value_change_updates_endpoint_and_schema_only(self) -> None:
        """明确 A → B 更新关联接口及 Schema，并拦截候选夹带的其它修改。"""

        base = _base_plan()
        candidate = json.loads(json.dumps(base))
        greeting = _endpoint(candidate)
        greeting.update(summary="模型自由改写", error_codes=[], path="/api/model")
        _contract(candidate)["schemas"]["MorningGreetingResponse"]["properties"][
            "morningGreetingText"
        ]["description"] = "模型自由改写"
        _endpoint(candidate, _OTHER_ENDPOINT_ID)["summary"] = "模型顺手润色"
        _contract(candidate)["schemas"]["OtherResponse"]["description"] = "模型顺手润色"

        merged = _merge(base, candidate)
        changed = _endpoint(merged)
        unchanged = _endpoint(merged, _OTHER_ENDPOINT_ID)

        self.assertEqual(changed["_change_status"], "modified")
        self.assertEqual(changed["summary"], "获取欢迎页展示的问候字符串“hello world”")
        self.assertEqual(changed["path"], "/api/greetings/morning")
        self.assertEqual(changed["error_codes"], ["INTERNAL_SERVER_ERROR"])
        self.assertEqual(
            _contract(merged)["schemas"]["MorningGreetingResponse"]["properties"][
                "morningGreetingText"
            ]["description"],
            "固定内容为“hello world”",
        )
        self.assertEqual(unchanged["_change_status"], "unchanged")
        self.assertEqual(unchanged["summary"], "无关接口原说明")
        self.assertEqual(
            _contract(merged)["schemas"]["OtherResponse"]["description"], "无关响应"
        )

    def test_schema_only_content_change_marks_endpoint_modified(self) -> None:
        """接口摘要没有旧值时，响应 Schema 的确定变化仍让接口进入开发范围。"""

        base = _base_plan()
        _endpoint(base)["summary"] = "获取问候语"
        candidate = json.loads(json.dumps(base))
        _endpoint(candidate)["error_codes"] = []

        merged = _merge(base, candidate)
        changed = _endpoint(merged)

        self.assertEqual(changed["summary"], "获取问候语")
        self.assertEqual(changed["_change_status"], "modified")
        self.assertEqual(changed["error_codes"], ["INTERNAL_SERVER_ERROR"])
        self.assertIn(
            "hello world",
            _contract(merged)["schemas"]["MorningGreetingResponse"]["properties"][
                "morningGreetingText"
            ]["description"],
        )

    def test_visual_only_text_change_does_not_modify_api(self) -> None:
        """页面视觉文案的 A → B 不能借相同字符串触发接口内容修改。"""

        base = _base_plan()
        old = _requirement("页面标题展示“早上好！”。")
        current = _requirement("页面标题展示“hello world”。")
        candidate = json.loads(json.dumps(base))
        _endpoint(candidate)["summary"] = "模型把接口也改成 hello world"
        _contract(candidate)["schemas"]["MorningGreetingResponse"]["properties"][
            "morningGreetingText"
        ]["description"] = "模型把接口也改成 hello world"

        merged = merge_technical_plan(
            base,
            candidate,
            relevant=relevant_object_ids(current, None, base, old),
        )

        self.assertEqual(_endpoint(merged)["_change_status"], "unchanged")
        self.assertEqual(_endpoint(merged)["summary"], _endpoint(base)["summary"])
        self.assertEqual(
            _contract(merged)["schemas"]["MorningGreetingResponse"],
            _contract(base)["schemas"]["MorningGreetingResponse"],
        )

    def test_ambiguous_text_change_does_not_accept_model_rewrite(self) -> None:
        """需求有多组值无法一一对应时，模型候选仍按润色处理并保留基线。"""

        base = _base_plan()
        old = _requirement("展示“早上好！”和“欢迎”。")
        current = _requirement("展示“hello world”和“您好”。")
        candidate = json.loads(json.dumps(base))
        _endpoint(candidate)["summary"] = "模型猜测应改成 hello world"
        _contract(candidate)["schemas"]["MorningGreetingResponse"]["properties"][
            "morningGreetingText"
        ]["description"] = "模型猜测应改成 hello world"

        merged = merge_technical_plan(
            base,
            candidate,
            relevant=relevant_object_ids(current, None, base, old),
        )
        greeting = _endpoint(merged)

        self.assertEqual(greeting["_change_status"], "unchanged")
        self.assertEqual(greeting["summary"], _endpoint(base)["summary"])
        self.assertEqual(
            _contract(merged)["schemas"]["MorningGreetingResponse"],
            _contract(base)["schemas"]["MorningGreetingResponse"],
        )


class EntityReferenceClosureTests(unittest.TestCase):
    """新增接口需要既有实体的新字段时，只闭合真实引用，不放开整份实体。"""

    def test_new_endpoint_adds_only_referenced_entity_field(self) -> None:
        """新 Schema 引用的新字段补入实体并标修改，未引用字段与旧字段润色被拦截。"""

        base = _base_plan()
        base["entities"] = [{
            "id": "greeting",
            "name": "问候语",
            "description": "基线实体说明",
            "fields": [{
                "name": "morning_greeting_text",
                "label": "早间问候",
                "description": "基线字段说明",
                "type": "text",
                "required": True,
            }],
        }]
        candidate = json.loads(json.dumps(base))
        candidate["entities"][0]["description"] = "模型润色实体说明"
        candidate["entities"][0]["fields"][0]["description"] = "模型润色旧字段"
        candidate["entities"][0]["fields"].extend([
            {
                "name": "noon_greeting_text",
                "label": "午间问候",
                "description": "固定内容为“中午好！”",
                "type": "text",
                "required": True,
            },
            {
                "name": "model_only_field",
                "label": "模型自行增加",
                "description": "没有 Schema 引用，不应采纳",
                "type": "text",
                "required": False,
            },
        ])
        _contract(candidate)["schemas"]["NoonGreetingResponse"] = {
            "type": "object",
            "properties": {
                "noonGreetingText": {
                    "type": "string",
                    "description": "固定内容为“中午好！”",
                    "entity_field_ref": "greeting.noon_greeting_text",
                }
            },
            "required": ["noonGreetingText"],
        }
        _contract(candidate)["endpoints"].append({
            **_endpoint(candidate),
            "id": "greeting_contract.get_noon_greeting",
            "name": "获取午间问候语",
            "path": "/api/greetings/noon",
            "summary": "获取午间问候字符串“中午好！”",
            "response_schema_ref": "NoonGreetingResponse",
        })
        candidate["pages"].append({
            "pageId": "noon_greeting",
            "references": {
                "endpoint_dependencies": [{
                    "endpoint_id": "greeting_contract.get_noon_greeting"
                }],
                "action_implementations": [],
            },
        })
        current = _old_requirement()
        current["pages"].append({
            "pageId": "noon_greeting",
            "name": "午间问候页",
            "description": "请求后端接口获取字符串“中午好！”并展示。",
        })

        merged = merge_technical_plan(
            base,
            candidate,
            relevant=relevant_object_ids(current, None, base, _old_requirement()),
        )
        entity = merged["entities"][0]
        fields = {field["name"]: field for field in entity["fields"]}

        self.assertEqual(entity["_change_status"], "modified")
        self.assertEqual(entity["description"], "基线实体说明")
        self.assertEqual(fields["morning_greeting_text"]["description"], "基线字段说明")
        self.assertEqual(fields["noon_greeting_text"]["description"], "固定内容为“中午好！”")
        self.assertNotIn("model_only_field", fields)
        self.assertEqual(
            _endpoint(merged, "greeting_contract.get_noon_greeting")["_change_status"],
            "added",
        )


if __name__ == "__main__":
    unittest.main()
