"""技术规划增量合并的变更标记剥离与跨迭代继承口径回归。

覆盖两个曾经导致"计划阶段无法确认"的缺陷：
1. 展示版 `_change_status` 落盘后，下一轮确认校验把它当非法字段拒绝；
2. 需求未涉及的既有对象被模型改写后仍被判"修改"，且用户没要求改的
   页面依赖（`required_for_initial_load`）被静默改写。
"""

import json

import unittest

from app.services.project_plan import validate_technical_plan_api_contracts
from app.services.technical_plan_carryover import drop_generated_fields
from app.services.technical_plan_merger import (
    merge_technical_plan,
    relevant_object_ids,
    strip_change_status,
)


def _plan() -> dict:
    """构造一份含实体、接口分组与页面依赖的最小技术规划。"""

    return {
        "artifact_type": "technical-plan",
        "architecture": {"frontend": "SPA", "backend": "Springboot", "data": "MySQL8"},
        "entities": [
            {
                "id": "greeting",
                "name": "问候语",
                "fields": [{"name": "content", "type": "text", "label": "内容", "description": "问候内容"}],
            }
        ],
        "api_contracts": [
            {
                "id": "greeting_contract",
                "name": "获取问候语",
                "base_path": "/api",
                "entity_ids": ["greeting"],
                "endpoints": [
                    {
                        "id": "greeting_contract.getGreeting",
                        "name": "获取问候语",
                        "method": "GET",
                        "path": "/greetings",
                        "error_codes": ["INTERNAL_ERROR"],
                    }
                ],
                "schemas": {},
            }
        ],
        "pages": [
            {
                "pageId": "home",
                "references": {
                    "endpoint_dependencies": [
                        {
                            "endpoint_id": "greeting_contract.getGreeting",
                            "usage": "initial_load",
                            "trigger": "打开首页",
                            "required_for_initial_load": True,
                        }
                    ]
                },
            }
        ],
        "authorization_manifest": {},
    }


def _requirement_spec() -> dict:
    """构造与上面计划对应的本轮需求条目。"""

    return {
        "pages": [{"pageId": "home", "name": "首页"}],
        "entities": [{"id": "greeting", "name": "问候语", "description": "问候内容"}],
    }


class TechnicalPlanMergerMarkerTests(unittest.TestCase):
    """变更标记永远不能进入正式产物。"""

    def test_strip_removes_markers_on_entities_contracts_endpoints_and_pages(self) -> None:
        """三个列表字段与其嵌套 endpoints 上的标记都要被剥离。"""

        tagged = merge_technical_plan(None, _plan(), relevant=None)

        self.assertIn("_change_status", json.dumps(tagged))
        clean = strip_change_status(tagged)

        self.assertNotIn("_change_status", json.dumps(clean))
        self.assertEqual(clean, drop_generated_fields(_plan()))

    def test_clean_candidate_passes_contract_validation(self) -> None:
        """落盘 / 入库用的干净版不得触发"包含非法字段：_change_status"。"""

        tagged = merge_technical_plan(None, _plan(), relevant=None)
        clean = strip_change_status(tagged)

        self.assertEqual(validate_technical_plan_api_contracts(clean), [])
        # 展示版本身带上标记，正是校验会拒绝它、从而必须剥离的原因。
        self.assertTrue(validate_technical_plan_api_contracts(tagged))

    def test_display_diff_is_not_written_back_into_state(self) -> None:
        """`technical_plan` 必须是干净版：展示 diff 只存在 `technical_plan_diff` 里。"""

        tagged = merge_technical_plan(None, _plan(), relevant=None)

        self.assertNotIn("_change_status", json.dumps(strip_change_status(tagged)))


class TechnicalPlanMergerInheritanceTests(unittest.TestCase):
    """需求没涉及的既有对象必须原样继承基线。"""

    def test_untouched_requirement_keeps_every_object_exactly_as_baseline(self) -> None:
        """模型整份重写但需求没变时，干净版必须与基线完全一致。"""

        base = _plan()
        rewritten = json.loads(json.dumps(base))
        rewritten["entities"][0]["fields"][0]["description"] = "模型顺手润色"
        rewritten["api_contracts"][0]["endpoints"][0]["error_codes"] = []
        rewritten["pages"][0]["references"]["endpoint_dependencies"][0].update(
            trigger="模型改写", required_for_initial_load=False
        )
        spec = _requirement_spec()

        merged = merge_technical_plan(
            base,
            rewritten,
            relevant=relevant_object_ids(spec, None, base, _requirement_spec()),
        )
        clean = strip_change_status(merged)

        self.assertEqual(clean, base)
        # 标记只打在合并粒度（页面 / 实体 / 接口）上，页面内的依赖对象靠整体继承保证不变。
        self.assertEqual(merged["pages"][0]["_change_status"], "unchanged")
        self.assertEqual(
            merged["pages"][0]["references"]["endpoint_dependencies"][0]["required_for_initial_load"],
            True,
        )

    def test_untouched_contract_shell_ignores_llm_rewording(self) -> None:
        """模型润色既有分组名称或路径前缀时，外壳与变更状态都继承基线。"""

        base = _plan()
        base["api_contracts"][0]["authentication"] = {"required": False}
        rewritten = json.loads(json.dumps(base))
        rewritten["api_contracts"][0].update(
            name="获取问候语管理",
            base_path="/api/rewritten",
            authentication={"required": True},
        )

        merged = merge_technical_plan(
            base,
            rewritten,
            relevant=relevant_object_ids(
                _requirement_spec(), None, base, _requirement_spec()
            ),
        )
        contract = merged["api_contracts"][0]

        self.assertEqual(contract["_change_status"], "unchanged")
        self.assertEqual(contract["name"], "获取问候语")
        self.assertEqual(contract["base_path"], "/api")
        self.assertEqual(contract["authentication"], {"required": False})
        self.assertEqual(strip_change_status(merged), base)

    def test_added_endpoint_does_not_unlock_contract_shell_rewording(self) -> None:
        """新增接口只让分组标为修改，不能夹带接受模型对分组外壳的润色。"""

        base = _plan()
        rewritten = json.loads(json.dumps(base))
        contract = rewritten["api_contracts"][0]
        contract.update(name="获取问候语管理", base_path="/api/rewritten")
        contract["endpoints"].append({
            **json.loads(json.dumps(contract["endpoints"][0])),
            "id": "greeting_contract.listGreetings",
            "name": "问候语列表",
            "path": "/greetings/list",
        })

        merged = merge_technical_plan(
            base,
            rewritten,
            relevant=relevant_object_ids(
                _requirement_spec(), None, base, _requirement_spec()
            ),
        )
        merged_contract = merged["api_contracts"][0]

        self.assertEqual(merged_contract["_change_status"], "modified")
        self.assertEqual(merged_contract["name"], "获取问候语")
        self.assertEqual(merged_contract["base_path"], "/api")
        self.assertEqual(
            [endpoint["_change_status"] for endpoint in merged_contract["endpoints"]],
            ["unchanged", "added"],
        )

    def test_changed_requirement_allows_the_object_to_be_modified(self) -> None:
        """需求里确实改过的实体才允许采用模型的新内容。"""

        base = _plan()
        rewritten = json.loads(json.dumps(base))
        rewritten["entities"][0]["fields"][0]["description"] = "用户要求的新说明"
        spec = _requirement_spec()
        spec["entities"][0]["description"] = "用户要求的新说明"

        merged = merge_technical_plan(
            base,
            rewritten,
            relevant=relevant_object_ids(spec, None, base, _requirement_spec()),
        )

        self.assertEqual(merged["entities"][0]["_change_status"], "modified")
        self.assertEqual(merged["entities"][0]["fields"][0]["description"], "用户要求的新说明")

    def test_requests_new_page_is_treated_as_added(self) -> None:
        """基线需求里没有的对象直接放行；内容一致时仍如实标 unchanged / added。"""

        base = _plan()
        base["pages"].append({"pageId": "about", "references": {}})
        spec = _requirement_spec()
        spec["pages"].append({"pageId": "about", "name": "关于"})

        merged = merge_technical_plan(
            base, json.loads(json.dumps(base)),
            relevant=relevant_object_ids(spec, None, base, _requirement_spec()),
        )

        self.assertEqual(
            [page["_change_status"] for page in merged["pages"]], ["unchanged", "unchanged"]
        )


if __name__ == "__main__":
    unittest.main()


class TechnicalPlanCommitDisplayTests(unittest.TestCase):
    """commit 落盘后，确认界面必须还能看到本轮的删除级联标记。"""

    def test_deleted_page_marker_survives_into_confirmation_view(self) -> None:
        """展示版候选的标记要保留；落盘候选必须完全干净。"""

        base = _plan()
        base["pages"].append({"pageId": "cancel_completed", "references": {}})
        # 模型照常输出了待删除页，合并时才按需求标记级联删除。
        new = json.loads(json.dumps(base))

        display = merge_technical_plan(base, new, relevant=None, deleted_page_ids={"cancel_completed"})
        clean = strip_change_status(display)

        marks = {page["pageId"]: page.get("_change_status") for page in display["pages"]}
        self.assertEqual(marks["cancel_completed"], "deleted")
        self.assertEqual(marks["home"], "unchanged")
        # 删除标记属于当前产物契约；非删除标记必须剥离，删除内容仍沿用基线。
        deleted = next(page for page in clean["pages"] if page["pageId"] == "cancel_completed")
        self.assertEqual(deleted, {**base["pages"][1], "_change_status": "deleted"})
        self.assertNotIn("_change_status", json.dumps(clean["pages"][0]))


class TechnicalPlanDeletionMergeTests(unittest.TestCase):
    """删除页及接口必须保留基线事实，不能被模型改写成悬空引用。"""

    def _baseline(self) -> dict:
        """构造存活页和删除页分别使用接口、共用类型定义的基线。"""

        plan = _plan()
        contract = plan["api_contracts"][0]
        contract["schemas"] = {
            "Greeting": {"type": "object", "properties": {"content": {"type": "string"}}},
            "DeleteRequest": {"type": "object", "properties": {}},
            "DeleteResult": {"type": "object", "properties": {}},
        }
        contract["endpoints"][0]["response_schema_ref"] = "Greeting"
        contract["endpoints"].append({
            "id": "greeting_contract.submit",
            "name": "提交注销",
            "method": "POST",
            "path": "/greetings/deactivation",
            "request_schema_ref": "DeleteRequest",
            "response_schema_ref": "DeleteResult",
        })
        plan["pages"].append({
            "pageId": "return_card",
            "references": {
                "endpoint_dependencies": [{"endpoint_id": "greeting_contract.submit"}],
                "action_implementations": [{
                    "actionId": "submit",
                    "stepBindings": [{"stepId": "save", "endpointId": "greeting_contract.submit"}],
                }],
            },
        })
        return plan

    def test_deleted_endpoint_and_page_keep_baseline_references(self) -> None:
        """模型改写待删除接口的类型名时，删除记录仍使用基线及其完整类型表。"""

        base = self._baseline()
        rewritten = json.loads(json.dumps(base))
        contract = rewritten["api_contracts"][0]
        contract["endpoints"][1].update(
            request_schema_ref="RenamedRequest", response_schema_ref="RenamedResult",
        )
        contract["schemas"] = {
            "Greeting": base["api_contracts"][0]["schemas"]["Greeting"],
            "RenamedRequest": {"type": "object", "properties": {}},
            "RenamedResult": {"type": "object", "properties": {}},
        }
        rewritten["pages"][1]["references"] = {}

        merged = merge_technical_plan(base, rewritten, deleted_page_ids={"return_card"})
        endpoint = merged["api_contracts"][0]["endpoints"][1]
        self.assertEqual(endpoint, {**base["api_contracts"][0]["endpoints"][1], "_change_status": "deleted"})
        self.assertEqual(merged["pages"][1], {**base["pages"][1], "_change_status": "deleted"})
        from app.services.api_contract_validation import validate_api_contract_definitions
        self.assertEqual(validate_api_contract_definitions(strip_change_status(merged)), [])

    def test_omitted_shared_endpoint_is_not_deleted(self) -> None:
        """模型省略仍被存活页使用的接口时，必须继承而非静默标记删除。"""

        base = self._baseline()
        rewritten = json.loads(json.dumps(base))
        rewritten["api_contracts"][0]["endpoints"] = []
        rewritten["pages"] = [rewritten["pages"][0]]
        merged = merge_technical_plan(base, rewritten, deleted_page_ids={"return_card"})
        endpoints = {item["id"]: item for item in merged["api_contracts"][0]["endpoints"]}
        self.assertEqual(endpoints["greeting_contract.getGreeting"]["_change_status"], "unchanged")
        self.assertEqual(endpoints["greeting_contract.submit"]["_change_status"], "deleted")
        self.assertEqual(merged["pages"][1]["_change_status"], "deleted")

    def test_repeated_merge_does_not_resurrect_deleted_endpoint(self) -> None:
        """同轮重试沿用已删除基线时，不得把删除记录重新标记为未变更。"""

        base = self._baseline()
        first = merge_technical_plan(base, base, deleted_page_ids={"return_card"})
        repeated = merge_technical_plan(strip_change_status(first), base, deleted_page_ids={"return_card"})
        endpoint = repeated["api_contracts"][0]["endpoints"][1]
        self.assertEqual(endpoint["_change_status"], "deleted")
        self.assertEqual(endpoint["request_schema_ref"], "DeleteRequest")


class TechnicalPlanEndpointPathChangeTests(unittest.TestCase):
    """用户在页面需求里点名新接口路径时，接口地址必须真正更新。

    回归：此前接口白名单恒为空集合，"把问候首页的接口换成 /api/hi" 只会让页面
    显示为变更，接口条目却仍继承基线的旧路径，技术规划与需求脱节。
    """

    def _spec_with_path(self, path: str) -> dict:
        """构造需求：首页描述里点名了新的接口路径。"""

        spec = _requirement_spec()
        spec["pages"][0]["description"] = f"应用首页，调用后端问候接口 {path} 获取问候字符串。"
        return spec

    def _rewritten(self, path: str) -> dict:
        """构造模型输出：把接口路径改成指定值。"""

        plan = _plan()
        plan["api_contracts"][0]["endpoints"][0]["path"] = path
        return plan

    def _endpoint(self, merged: dict) -> dict:
        """取出合并结果里的问候接口条目。"""

        return merged["api_contracts"][0]["endpoints"][0]

    def test_requested_path_replaces_baseline_path(self) -> None:
        """需求点名的 /api/hi 必须落地并标记为修改。"""

        base = _plan()
        spec = self._spec_with_path("/api/hi")

        merged = merge_technical_plan(
            base,
            self._rewritten("/api/hi"),
            relevant=relevant_object_ids(spec, None, base, _requirement_spec()),
        )

        endpoint = self._endpoint(merged)
        self.assertEqual(endpoint["path"], "/api/hi")
        self.assertEqual(endpoint["_change_status"], "modified")

    def test_path_not_requested_by_user_is_rejected(self) -> None:
        """需求只点名 /api/hi 时，模型改到其它地址必须退回基线。"""

        base = _plan()
        spec = self._spec_with_path("/api/hi")

        merged = merge_technical_plan(
            base,
            self._rewritten("/api/hacked"),
            relevant=relevant_object_ids(spec, None, base, _requirement_spec()),
        )

        endpoint = self._endpoint(merged)
        self.assertEqual(endpoint["path"], "/greetings")
        self.assertEqual(endpoint["_change_status"], "unchanged")

    def test_requirement_without_interface_path_keeps_baseline(self) -> None:
        """需求没提接口路径时，接口改写一律不被采纳。"""

        base = _plan()
        spec = _requirement_spec()

        merged = merge_technical_plan(
            base,
            self._rewritten("/api/hacked"),
            relevant=relevant_object_ids(spec, None, base, _requirement_spec()),
        )

        endpoint = self._endpoint(merged)
        self.assertEqual(endpoint["path"], "/greetings")
        self.assertEqual(endpoint["_change_status"], "unchanged")
