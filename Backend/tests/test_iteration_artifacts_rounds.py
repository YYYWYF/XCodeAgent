"""迭代产物记录按**轮次**保留，同一分支上的多轮各记各的。

用户选「继续当前版本」时分支名不变。若记录以分支名为 key，本轮事实会写进同一条、
把上一轮抹掉；归属标注又靠"排除当前分支"来算，于是上一轮的产出**永远**标不出来。
这里验证改成轮次列表后，两种情况（继续当前版本 / 新建版本）都能正确回答
"这个页面上次是哪个版本做的"。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.iteration_artifacts import (
    begin_iteration_round,
    iteration_artifacts_path,
    iteration_origins_for_pages,
    read_iteration_artifacts,
    record_iteration_artifacts,
)


class IterationRoundsTests(unittest.TestCase):
    def test_same_branch_rounds_are_kept_apart(self) -> None:
        """同一分支连续两轮：上一轮的事实必须保留，不能被本轮覆盖。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)

            begin_iteration_round(workspace, "v1.1")
            record_iteration_artifacts(
                workspace,
                planned_page_ids=["welcome_home", "hello_agent_display"],
                designed_page_ids=["welcome_home", "hello_agent_display"],
            )

            begin_iteration_round(workspace, "v1.1")
            record_iteration_artifacts(
                workspace,
                planned_page_ids=["welcome_home", "hello_agent_display", "greeting_display"],
                designed_page_ids=[],
            )

            rounds = read_iteration_artifacts(workspace)
            self.assertEqual(len(rounds), 2, "两轮必须各记一条")
            self.assertEqual(
                rounds[0]["designedPageIds"],
                ["welcome_home", "hello_agent_display"],
                "上一轮的设计事实被本轮覆盖了",
            )
            self.assertEqual(rounds[1]["designedPageIds"], [])
            self.assertEqual(rounds[1]["branch"], "v1.1")

    def test_record_updates_last_round_only(self) -> None:
        """一轮内多次落盘（逐页生成）只更新末轮。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            begin_iteration_round(workspace, "v1.1")

            record_iteration_artifacts(workspace, planned_page_ids=["a", "b"])
            record_iteration_artifacts(workspace, designed_page_ids=["a"])
            record_iteration_artifacts(workspace, designed_page_ids=["a", "b"])

            rounds = read_iteration_artifacts(workspace)
            self.assertEqual(len(rounds), 1)
            self.assertEqual(rounds[0]["plannedPageIds"], ["a", "b"])
            self.assertEqual(rounds[0]["designedPageIds"], ["a", "b"])

    def test_record_without_begin_still_works(self) -> None:
        """末轮缺失时补一条，保证单独调用也可用。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)

            record_iteration_artifacts(workspace, planned_page_ids=["a"])

            rounds = read_iteration_artifacts(workspace)
            self.assertEqual(len(rounds), 1)
            self.assertEqual(rounds[0]["plannedPageIds"], ["a"])

    def test_origins_exclude_current_round_not_current_branch(self) -> None:
        """核心回归：「继续当前版本」时，上一轮的产出仍要标得出来。

        旧实现按"分支名 != 当前分支"排除，同一分支的上一轮会被一起排掉，
        于是界面永远不显示"已设计过"。
        """

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)

            # 第一轮（v1.1）：设计过 welcome_home
            begin_iteration_round(workspace, "v1.1")
            record_iteration_artifacts(
                workspace,
                planned_page_ids=["welcome_home"],
                designed_page_ids=["welcome_home"],
            )

            # 第二轮仍继续 v1.1：本轮还没设计任何页面
            begin_iteration_round(workspace, "v1.1")
            record_iteration_artifacts(
                workspace,
                planned_page_ids=["welcome_home", "greeting_display"],
                designed_page_ids=[],
            )

            origins = iteration_origins_for_pages(workspace, ["welcome_home", "greeting_display"])

            self.assertEqual(
                origins.get("welcome_home"),
                {"designedIn": "v1.1"},
                "同一分支上一轮设计过的页面必须标出归属",
            )
            self.assertNotIn("greeting_display", origins, "本轮新增的页面不该有归属")

    def test_origins_report_new_branch_case(self) -> None:
        """新建版本的情况同样正确（回归既有行为）。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)

            begin_iteration_round(workspace, "v1.0")
            record_iteration_artifacts(
                workspace, planned_page_ids=["home"], designed_page_ids=["home"]
            )

            begin_iteration_round(workspace, "v1.1")
            record_iteration_artifacts(
                workspace, planned_page_ids=["home", "hello_agent"], designed_page_ids=[]
            )

            origins = iteration_origins_for_pages(workspace, ["home", "hello_agent"])

            self.assertEqual(origins.get("home"), {"designedIn": "v1.0"})
            self.assertNotIn("hello_agent", origins)

    def test_planned_but_undesigned_is_reported(self) -> None:
        """上一轮计划了却没设计 → plannedButUndesignedIn。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)

            begin_iteration_round(workspace, "v1.0")
            record_iteration_artifacts(
                workspace, planned_page_ids=["home", "skipped"], designed_page_ids=["home"]
            )

            begin_iteration_round(workspace, "v1.1")
            record_iteration_artifacts(workspace, planned_page_ids=["home", "skipped"])

            origins = iteration_origins_for_pages(workspace, ["home", "skipped"])

            self.assertEqual(origins.get("home"), {"designedIn": "v1.0"})
            self.assertEqual(origins.get("skipped"), {"plannedButUndesignedIn": "v1.0"})

    def test_read_tolerates_missing_and_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            self.assertEqual(read_iteration_artifacts(workspace), [])

            path = iteration_artifacts_path(workspace)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(read_iteration_artifacts(workspace), [])

            path.write_text(json.dumps({"schemaVersion": 2, "rounds": "nope"}), encoding="utf-8")
            self.assertEqual(read_iteration_artifacts(workspace), [])
