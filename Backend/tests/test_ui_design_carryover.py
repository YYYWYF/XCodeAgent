"""发起新迭代时继承上一轮已确认的 UI 设计稿。

背景：`specs/` 与 `ui-design/` 都在发起新迭代时被清空，于是上一轮设计过的页面
回到"未生成"，用户每轮都得把同样的页面重新做一遍。这里验证清空流程会先把
已确认的页面登记下来、按登记保留它们的代码目录，播种时再按当前 ProductPlan
重新校验后继承。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.graph.nodes.ui_confirmation import _carried_ui_page_manifest
from app.services.iteration_service import _clear_iteration_artifacts
from app.services.ui_design_carryover import (
    carried_page_keys,
    clear_ui_design_carryover,
    read_ui_design_carryover,
    ui_design_carryover_path,
    write_ui_design_carryover,
)

_PAGE_ID = "home"
_PAGE_KEY = "Home"
_ITEM_ID = "home_welcome_text"

# 一份能通过一致性校验的最小设计稿：标记与 ProductPlan 的 information_items 对齐。
_VALID_CODE = """export default function PageHome() {
  return (
    <div
      data-information-item-id="home_welcome_text"
      data-control-id="home_welcome_text-display"
    >
      hello world
    </div>
  );
}
"""


def _product_page() -> dict[str, object]:
    return {
        "pageId": _PAGE_ID,
        "name": "欢迎页",
        "path": "/page/home",
        "information_items": [{"itemId": _ITEM_ID, "label": "欢迎文案"}],
        "actions": [],
    }


def _manifest(*, status: str) -> dict[str, object]:
    return {
        "schema_version": "ui-manifest.v3",
        "confirmation_status": "pending_user_confirmation",
        "pages": [
            {
                "pageId": _PAGE_ID,
                "page_key": _PAGE_KEY,
                "status": status,
                "template_id": "commonTable",
                "template_source_path": "templates/commonTable",
            }
        ],
    }


class CarryoverStoreTests(unittest.TestCase):
    """交接记录的读写：读不出来一律按"没有"处理，不阻断设计阶段。"""

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            write_ui_design_carryover(
                workspace,
                source_branch="v1.0",
                pages={
                    _PAGE_ID: {
                        "pageKey": _PAGE_KEY,
                        "templateId": "commonTable",
                        "templateSourcePath": "templates/commonTable",
                    }
                },
            )

            carried = read_ui_design_carryover(workspace)

            self.assertEqual(list(carried), [_PAGE_ID])
            self.assertEqual(carried[_PAGE_ID]["pageKey"], _PAGE_KEY)
            self.assertEqual(carried[_PAGE_ID]["templateId"], "commonTable")
            self.assertEqual(carried_page_keys(workspace), {_PAGE_KEY})

    def test_empty_pages_clears_stale_record(self) -> None:
        """本轮没有可继承的设计稿时必须清掉旧记录，否则下一轮会继承到过期的来源。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            write_ui_design_carryover(
                workspace,
                source_branch="v1.0",
                pages={_PAGE_ID: {"pageKey": _PAGE_KEY, "templateId": "", "templateSourcePath": ""}},
            )

            write_ui_design_carryover(workspace, source_branch="v1.1", pages={})

            self.assertEqual(read_ui_design_carryover(workspace), {})
            self.assertFalse(ui_design_carryover_path(workspace).exists())

    def test_read_tolerates_missing_corrupt_and_keyless_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            # 文件缺失
            self.assertEqual(read_ui_design_carryover(workspace), {})

            path = ui_design_carryover_path(workspace)
            path.parent.mkdir(parents=True, exist_ok=True)

            # JSON 损坏
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(read_ui_design_carryover(workspace), {})

            # 缺 pageKey 的条目没有交接价值，直接丢弃
            path.write_text(
                json.dumps({"pages": {_PAGE_ID: {"templateId": "x"}, "other": "not-a-dict"}}),
                encoding="utf-8",
            )
            self.assertEqual(read_ui_design_carryover(workspace), {})

    def test_clear_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            clear_ui_design_carryover(workspace)
            clear_ui_design_carryover(workspace)
            self.assertEqual(read_ui_design_carryover(workspace), {})


class IterationClearKeepsConfirmedDesignsTests(unittest.TestCase):
    """清空流程：登记并保留已确认页面的代码目录，其余照常删除。"""

    def _workspace(self, root: Path, *, status: str) -> Path:
        devagentstudio = root / ".devagentstudio"
        (devagentstudio / "specs").mkdir(parents=True)
        (devagentstudio / "specs" / "ui-designs.json").write_text(
            json.dumps(_manifest(status=status)), encoding="utf-8"
        )
        page_dir = devagentstudio / "ui-design" / "pages" / _PAGE_KEY
        page_dir.mkdir(parents=True)
        (page_dir / "index.tsx").write_text(_VALID_CODE, encoding="utf-8")
        # 一个未被继承的页面目录，用来验证"其余照常删除"。
        (devagentstudio / "ui-design" / "pages" / "Stale").mkdir()
        (devagentstudio / "ui-design" / "pages" / "Stale" / "index.tsx").write_text(
            "export default function Stale() { return null; }\n", encoding="utf-8"
        )
        return devagentstudio

    def test_keeps_confirmed_page_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            devagentstudio = self._workspace(workspace, status="confirmed")

            _clear_iteration_artifacts(devagentstudio, workspace_root=workspace)

            kept = devagentstudio / "ui-design" / "pages" / _PAGE_KEY / "index.tsx"
            self.assertTrue(kept.is_file(), "已确认的设计稿被清掉了，新迭代无从继承")
            self.assertFalse(
                (devagentstudio / "ui-design" / "pages" / "Stale").exists(),
                "未被继承的页面目录应当照常清掉",
            )
            self.assertEqual(carried_page_keys(workspace), {_PAGE_KEY})
            # specs/ 仍然照常清空 —— 继承靠的是交接记录，不是留着旧 manifest。
            self.assertFalse((devagentstudio / "specs").exists())

    def test_pending_page_is_not_kept(self) -> None:
        """只有 confirmed 才继承：pending 描述的是"本轮没做完的事"，必须重新推导。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            devagentstudio = self._workspace(workspace, status="pending")

            _clear_iteration_artifacts(devagentstudio, workspace_root=workspace)

            self.assertFalse((devagentstudio / "ui-design" / "pages" / _PAGE_KEY).exists())
            self.assertEqual(read_ui_design_carryover(workspace), {})

    def test_missing_manifest_clears_stale_carryover(self) -> None:
        """读不到 manifest（首轮、或从未进入设计阶段）时不能留着上一轮的继承来源。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            write_ui_design_carryover(
                workspace,
                source_branch="v1.0",
                pages={_PAGE_ID: {"pageKey": _PAGE_KEY, "templateId": "", "templateSourcePath": ""}},
            )
            devagentstudio = workspace / ".devagentstudio"
            (devagentstudio / "ui-design").mkdir(parents=True)

            _clear_iteration_artifacts(devagentstudio, workspace_root=workspace)

            self.assertEqual(read_ui_design_carryover(workspace), {})


class CarriedPageManifestTests(unittest.TestCase):
    """播种时的继承判定：校验通过才继承，否则退回未生成。"""

    def test_inherits_when_code_still_matches_product_plan(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project_dir = Path(raw) / "ui-design"
            (project_dir / "pages" / _PAGE_KEY).mkdir(parents=True)
            (project_dir / "pages" / _PAGE_KEY / "index.tsx").write_text(_VALID_CODE, encoding="utf-8")

            entry = _carried_ui_page_manifest(
                _product_page(),
                str(project_dir),
                {_PAGE_ID: {"pageKey": _PAGE_KEY, "templateId": "tpl", "templateSourcePath": "src"}},
            )

            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry["status"], "confirmed")
            self.assertEqual(entry["page_key"], _PAGE_KEY)
            self.assertEqual(entry["template_id"], "tpl")
            self.assertTrue(entry.get("code"), "继承的条目必须带代码，否则前端看不了设计稿")

    def test_rejects_when_page_definition_changed(self) -> None:
        """新迭代增删了信息项 → 旧设计稿标记对不上 → 退回未生成，让用户重做。"""

        with tempfile.TemporaryDirectory() as raw:
            project_dir = Path(raw) / "ui-design"
            (project_dir / "pages" / _PAGE_KEY).mkdir(parents=True)
            (project_dir / "pages" / _PAGE_KEY / "index.tsx").write_text(_VALID_CODE, encoding="utf-8")

            changed_page = {**_product_page(), "information_items": [{"itemId": "brand_new_item"}]}

            entry = _carried_ui_page_manifest(
                changed_page,
                str(project_dir),
                {_PAGE_ID: {"pageKey": _PAGE_KEY, "templateId": "", "templateSourcePath": ""}},
            )

            self.assertIsNone(entry)

    def test_rejects_without_record_or_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project_dir = Path(raw) / "ui-design"
            project_dir.mkdir(parents=True)

            # 没有交接记录
            self.assertIsNone(_carried_ui_page_manifest(_product_page(), str(project_dir), {}))
            # 有记录但代码目录不存在（被清掉或写入失败）
            self.assertIsNone(
                _carried_ui_page_manifest(
                    _product_page(),
                    str(project_dir),
                    {_PAGE_ID: {"pageKey": _PAGE_KEY, "templateId": "", "templateSourcePath": ""}},
                )
            )
