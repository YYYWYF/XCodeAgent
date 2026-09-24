"""跨迭代沿用「用户没要求改的页面」的完整定义。

发起新迭代会整份重新生成 ProductPlan，模型会顺手重写其余页面的信息项 —— 语义没变但
itemId 全换，上一轮按旧 ID 产出的设计稿就对不上、继承被拒。这里验证两条路径：

- **需求未变** → 整份沿用上一轮定义（`carried_definition_for_page` + `_normalized_pages`）
- **需求变了** → 退回模型输出，只做保守的单点 ID 还原（`reconcile_product_plan_ids`）
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.product_plan import _normalized_pages, create_product_plan
from app.services.product_plan_carryover import (
    carried_definition_for_page,
    product_plan_carryover_path,
    read_product_plan_carryover,
    reconcile_product_plan_ids,
    write_product_plan_carryover,
)

_PAGE_ID = "hello_agent_display"
_OLD_ITEM_IDS = [
    "hello_agent_display_agent_text",
    "hello_agent_display_side_navigation",
    "hello_agent_display_header",
]
# 上一轮 RequirementSpec 的该页条目（= 判断"用户改没改这一页"的依据）。
_REQUIREMENT_PAGE = {
    "pageId": _PAGE_ID,
    "name": "Hello Agent 展示页",
    "path": "/page/hello-agent",
    "module_id": "agent_display",
    "description": "纯展示页面。",
}


def _definition(item_ids: list[str] | None = None, action_ids: list[str] | None = None) -> dict:
    """上一轮 ProductPlan 页面里模型补充的那部分。"""

    return {
        "goal": "展示 hello agent 文案",
        "information_items": [
            {"itemId": item_id, "label": item_id, "description": item_id}
            for item_id in (_OLD_ITEM_IDS if item_ids is None else item_ids)
        ],
        "actions": [
            {"actionId": action_id, "name": action_id, "description": action_id, "behavior": {"type": "none"}}
            for action_id in (action_ids or [])
        ],
        "navigation_targets": [],
        "state_requirements": {"loading": "静态页面"},
    }


def _carried(item_ids: list[str] | None = None, action_ids: list[str] | None = None) -> dict:
    return {
        _PAGE_ID: {
            "requirement": dict(_REQUIREMENT_PAGE),
            "definition": _definition(item_ids, action_ids),
        }
    }


def _requirement_spec(page: dict | None = None) -> dict:
    return {
        "app_info": {"name": "测试应用", "summary": ""},
        "pages": [page if page is not None else dict(_REQUIREMENT_PAGE)],
        "business_flows": [],
        "acceptance_criteria": [],
    }


def _agent_plan(item_ids: list[str]) -> dict:
    return {
        "pages": [
            {
                "pageId": _PAGE_ID,
                "information_items": [
                    {"itemId": item_id, "label": item_id, "description": item_id}
                    for item_id in item_ids
                ],
                "actions": [],
            }
        ]
    }


def _plan(item_ids: list[str], action_ids: list[str] | None = None) -> dict:
    return {
        "pages": [
            {
                "pageId": _PAGE_ID,
                "information_items": [{"itemId": item_id} for item_id in item_ids],
                "actions": [{"actionId": action_id} for action_id in (action_ids or [])],
            }
        ]
    }


def _item_ids(plan: dict) -> list[str]:
    return [item["itemId"] for item in plan["pages"][0]["information_items"]]


class CarriedDefinitionTests(unittest.TestCase):
    """需求条目未变 → 沿用；变了 → 不沿用。"""

    def test_unchanged_requirement_returns_definition(self) -> None:
        definition = carried_definition_for_page(_carried(), _PAGE_ID, dict(_REQUIREMENT_PAGE))

        self.assertIsNotNone(definition)
        assert definition is not None
        self.assertEqual(
            [item["itemId"] for item in definition["information_items"]], _OLD_ITEM_IDS
        )

    def test_description_rewording_still_reuses(self) -> None:
        """description 是自由散文，模型每轮都会改写 —— 不能因此判成"用户要改这一页"。

        实测场景：同一页只把"应用首页与唯一页面"改成"应用首页"（因为后来确实不止一页）。
        若拿它做相等判定，本机制永不生效，ID 照旧漂移、继承被拒。
        """

        reworded = {
            **_REQUIREMENT_PAGE,
            "description": "应用首页，页面主体居中展示写死的字符串 hello world。",
        }

        self.assertIsNotNone(
            carried_definition_for_page(_carried(), _PAGE_ID, reworded),
            "仅措辞变化就放弃沿用，会让设计稿继承永远失败",
        )

    def test_changed_structural_field_returns_none(self) -> None:
        """结构性字段变了 = 用户确实要改这一页 → 不沿用，交给模型重新生成。"""

        for field, value in (
            ("name", "首页"),
            ("path", "/page/home-v2"),
            ("module_id", "home_display"),
            ("pageId", "home_page"),
        ):
            with self.subTest(field=field):
                changed = {**_REQUIREMENT_PAGE, field: value}
                self.assertIsNone(
                    carried_definition_for_page(_carried(), _PAGE_ID, changed),
                    f"{field} 变了却没放弃沿用",
                )

    def test_missing_record_or_definition_returns_none(self) -> None:
        self.assertIsNone(carried_definition_for_page({}, _PAGE_ID, dict(_REQUIREMENT_PAGE)))
        self.assertIsNone(
            carried_definition_for_page(
                {_PAGE_ID: {"requirement": dict(_REQUIREMENT_PAGE), "definition": {}}},
                _PAGE_ID,
                dict(_REQUIREMENT_PAGE),
            )
        )


class NormalizedPagesCarryoverTests(unittest.TestCase):
    """整份沿用落在 _normalized_pages 上。"""

    def test_unchanged_page_ignores_model_output(self) -> None:
        """模型这一轮把信息项全改了，但需求没变 → 仍用上一轮的。"""

        pages = _normalized_pages(
            _requirement_spec(),
            _agent_plan(["hello_agent_display_text", "hello_agent_display_navigation_entry"]),
            _carried(),
        )

        self.assertEqual([item["itemId"] for item in pages[0]["information_items"]], _OLD_ITEM_IDS)

    def test_changed_page_uses_model_output(self) -> None:
        """结构性字段变了（这里改 path）→ 用模型这一轮给的，不沿用旧定义。"""

        changed = {**_REQUIREMENT_PAGE, "path": "/page/hello-agent-v2"}

        pages = _normalized_pages(
            _requirement_spec(changed),
            _agent_plan(["hello_agent_display_text"]),
            _carried(),
        )

        self.assertEqual(
            [item["itemId"] for item in pages[0]["information_items"]],
            ["hello_agent_display_text"],
        )

    def test_reworded_description_still_uses_carryover(self) -> None:
        """只改措辞 → 仍沿用（回归：曾因 description 判等而永不生效）。"""

        reworded = {
            **_REQUIREMENT_PAGE,
            "description": "纯展示页面，展示 hello agent 文案。",
        }

        pages = _normalized_pages(
            _requirement_spec(reworded),
            _agent_plan(["hello_agent_display_text", "hello_agent_display_navigation_entry"]),
            _carried(),
        )

        self.assertEqual(
            [item["itemId"] for item in pages[0]["information_items"]],
            _OLD_ITEM_IDS,
            "仅措辞变化就该沿用旧定义，否则 ID 会漂移、设计稿继承失败",
        )

    def test_path_comes_from_current_requirement_not_carryover(self) -> None:
        """沿用只覆盖 supplement 字段；path 必须取当前 RequirementSpec。

        否则修好的路由前缀会被上一轮的旧值覆盖回去。
        """

        current = {**_REQUIREMENT_PAGE, "path": "/page/hello-agent"}
        # 需求条目变了（path 变了）→ 本就不沿用；这里直接验证 path 始终来自 source。
        pages = _normalized_pages(_requirement_spec(current), _agent_plan(list(_OLD_ITEM_IDS)), _carried())

        self.assertEqual(pages[0]["path"], "/page/hello-agent")

    def test_without_carryover_behaves_as_before(self) -> None:
        pages = _normalized_pages(
            _requirement_spec(), _agent_plan(["hello_agent_display_text"]), None
        )

        self.assertEqual(
            [item["itemId"] for item in pages[0]["information_items"]],
            ["hello_agent_display_text"],
        )

    def test_create_product_plan_accepts_carryover(self) -> None:
        """create_product_plan 的参数透传（默认 None，不影响既有调用点）。"""

        spec = _requirement_spec()
        plan = create_product_plan(
            spec,
            agent_plan=_agent_plan(["hello_agent_display_text"]),
            carried_pages=_carried(),
        )

        self.assertEqual(_item_ids(plan), _OLD_ITEM_IDS)


class ReconcileProductPlanIdsTests(unittest.TestCase):
    """需求确实变了时的保守 ID 还原。"""

    def test_single_rename_is_restored(self) -> None:
        plan = _plan(
            [
                "hello_agent_display_text",  # 由 ..._agent_text 漂移而来
                "hello_agent_display_side_navigation",
                "hello_agent_display_header",
            ]
        )

        changed = reconcile_product_plan_ids(plan, _carried())

        self.assertEqual(changed, 1)
        self.assertEqual(_item_ids(plan), _OLD_ITEM_IDS)

    def test_identical_ids_are_untouched(self) -> None:
        plan = _plan(list(_OLD_ITEM_IDS))

        self.assertEqual(reconcile_product_plan_ids(plan, _carried()), 0)
        self.assertEqual(_item_ids(plan), _OLD_ITEM_IDS)

    def test_multiple_changes_are_left_alone(self) -> None:
        """多项同时变动说明这一页真的改了 —— 放弃还原，避免错配。"""

        plan = _plan(["a", "b", "hello_agent_display_header"])

        self.assertEqual(reconcile_product_plan_ids(plan, _carried()), 0)
        self.assertEqual(_item_ids(plan), ["a", "b", "hello_agent_display_header"])

    def test_added_or_removed_item_does_not_trigger_restore(self) -> None:
        self.assertEqual(reconcile_product_plan_ids(_plan([*_OLD_ITEM_IDS, "new"]), _carried()), 0)
        self.assertEqual(reconcile_product_plan_ids(_plan(_OLD_ITEM_IDS[:2]), _carried()), 0)

    def test_unknown_page_is_untouched(self) -> None:
        plan = _plan(["brand_new_page_text"])

        self.assertEqual(reconcile_product_plan_ids(plan, _carried()), 0)

    def test_action_ids_reconciled_independently(self) -> None:
        plan = _plan(list(_OLD_ITEM_IDS), ["hello_agent_display_submit_btn"])

        changed = reconcile_product_plan_ids(
            plan, _carried(action_ids=["hello_agent_display_submit_button"])
        )

        self.assertEqual(changed, 1)
        self.assertEqual(_item_ids(plan), _OLD_ITEM_IDS, "信息项本来就没漂，不该被动")
        self.assertEqual(
            [action["actionId"] for action in plan["pages"][0]["actions"]],
            ["hello_agent_display_submit_button"],
        )

    def test_empty_carryover_or_malformed_plan_is_noop(self) -> None:
        plan = _plan(["hello_agent_display_text"])
        before = json.dumps(plan, sort_keys=True)

        self.assertEqual(reconcile_product_plan_ids(plan, {}), 0)
        self.assertEqual(reconcile_product_plan_ids({}, _carried()), 0)
        self.assertEqual(json.dumps(plan, sort_keys=True), before)


class ProductPlanCarryoverStoreTests(unittest.TestCase):
    """交接记录的读写。"""

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            write_product_plan_carryover(
                workspace,
                source_branch="v1.1",
                pages={_PAGE_ID: {"requirement": dict(_REQUIREMENT_PAGE), "definition": _definition()}},
            )

            carried = read_product_plan_carryover(workspace)

            self.assertEqual(carried[_PAGE_ID]["requirement"], _REQUIREMENT_PAGE)
            self.assertEqual(
                [item["itemId"] for item in carried[_PAGE_ID]["definition"]["information_items"]],
                _OLD_ITEM_IDS,
            )

    def test_empty_pages_clears_stale_record(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            write_product_plan_carryover(
                workspace,
                source_branch="v1.0",
                pages={_PAGE_ID: {"requirement": dict(_REQUIREMENT_PAGE), "definition": _definition()}},
            )

            write_product_plan_carryover(workspace, source_branch="v1.1", pages={})

            self.assertEqual(read_product_plan_carryover(workspace), {})
            self.assertFalse(product_plan_carryover_path(workspace).exists())

    def test_read_tolerates_missing_corrupt_and_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            self.assertEqual(read_product_plan_carryover(workspace), {})

            path = product_plan_carryover_path(workspace)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(read_product_plan_carryover(workspace), {})

            # 缺 requirement 或 definition 的条目没有交接价值，直接丢弃
            path.write_text(
                json.dumps(
                    {
                        "pages": {
                            _PAGE_ID: {"definition": _definition()},
                            "other": {"requirement": dict(_REQUIREMENT_PAGE)},
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(read_product_plan_carryover(workspace), {})
