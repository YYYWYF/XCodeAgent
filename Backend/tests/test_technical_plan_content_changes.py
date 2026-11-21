"""已确认需求的明确文本替换与模型润色的边界回归。"""

from __future__ import annotations

import json
import unittest

from app.services.technical_plan_merger import merge_technical_plan, relevant_object_ids


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
