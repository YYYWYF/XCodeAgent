from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.application_page_planning import (
    ApplicationApiDefinition,
    ApplicationPageContext,
    ApplicationPageDefinition,
    ApplicationPageInteraction,
    ApplicationPagePlan,
    ConfirmPagePlanRequest,
    PagePlanningQuestionsRequest,
    confirm_application_page_plan,
    generate_page_planning_questions,
)


class ApplicationPagePlanningTests(unittest.TestCase):
    def test_question_generation_forwards_chunks_and_parses_complete_json(self) -> None:
        """澄清问题应逐块上报，但只用完整模型文本构造最终结果。"""

        chunks = [
            '{"questions":[',
            '{"id":"role","question":"谁使用？"},',
            '{"id":"flow","question":"核心流程是什么？"},',
            '{"id":"scope","question":"数据范围是什么？"}',
            "]}",
        ]

        class FakeModel:
            async def astream(self, _messages):
                """按测试定义的边界模拟模型流式输出。"""

                for chunk in chunks:
                    yield SimpleNamespace(content=chunk)

        received: list[str] = []

        async def report_text(delta: str) -> None:
            """记录协议层收到的每个模型文本增量。"""

            received.append(delta)

        async def run_generation():
            """运行一次使用假模型的澄清问题生成。"""

            request = PagePlanningQuestionsRequest(
                application=ApplicationPageContext(
                    name="客户中心", scenario="维护客户资料", terminal="PC"
                )
            )
            return await generate_page_planning_questions(request, report_text)

        with patch(
            "app.services.application_page_planning.create_chat_model",
            return_value=FakeModel(),
        ):
            response = asyncio.run(run_generation())

        self.assertEqual(received, chunks)
        self.assertEqual(len(response.questions), 3)
        self.assertEqual(response.questions[0].id, "role")

    def test_confirm_persists_page_details_and_api_design(self) -> None:
        """确认后应原子保存 menus 页面设计与新增 apis，同时保留既有配置。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "application.json"
            target.write_text(
                json.dumps({"appName": "客户中心", "pagePlan": {"stale": True}}),
                encoding="utf-8",
            )
            plan = ApplicationPagePlan(
                application=ApplicationPageContext(
                    name="客户中心", scenario="维护客户资料", terminal="PC"
                ),
                pages=[
                    ApplicationPageDefinition(
                        id="customer-list",
                        name="客户列表",
                        path="/customers",
                        purpose="查询和筛选客户",
                        key_features=["查询客户"],
                        related_page_ids=["customer-detail"],
                        api_ids=["list-customers"],
                        interactions=[
                            ApplicationPageInteraction(
                                name="查看客户",
                                trigger="点击客户名称",
                                user_action="选择一条客户记录",
                                system_response="打开客户详情",
                                target_page_id="customer-detail",
                                api_ids=["list-customers"],
                            )
                        ],
                    ),
                    ApplicationPageDefinition(
                        id="customer-detail",
                        name="客户详情",
                        path="/customers/:id",
                        purpose="查看客户完整资料",
                    ),
                ],
                apis=[
                    ApplicationApiDefinition(
                        id="list-customers",
                        name="查询客户列表",
                        method="GET",
                        path="/api/customers",
                        purpose="按条件查询客户",
                        request_design="支持关键词和分页参数",
                        response_design="返回客户摘要列表和总数",
                        used_by_page_ids=["customer-list"],
                    )
                ],
            )

            response = confirm_application_page_plan(
                ConfirmPagePlanRequest(workspace_root=str(workspace), plan=plan)
            )
            saved = json.loads(target.read_text(encoding="utf-8"))

            self.assertEqual(saved["appName"], "客户中心")
            self.assertNotIn("pagePlan", saved)
            self.assertEqual(saved["apis"][0]["id"], "list-customers")
            customer_page = saved["menus"]["items"][0]["children"][0]
            self.assertEqual(customer_page["apiIds"], ["list-customers"])
            self.assertEqual(response.apis[0].used_by_page_ids, ["customer-list"])


if __name__ == "__main__":
    unittest.main()
