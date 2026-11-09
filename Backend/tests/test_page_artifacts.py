"""验证页面概览事实来源、任务范围和只读 AG-UI 生命周期。"""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.page_artifacts import page_artifacts_router
from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle
from app.domain.development_artifacts import DevelopmentArtifactProgress
from app.services.page_artifact_overview import page_artifact_overview
from app.workspace.task_documents import write_build_task_plan_json


class PageArtifactTests(unittest.TestCase):
    """每例使用当前合同的隔离工作区，不依赖真实模型或修改用户项目。"""

    def setUp(self) -> None:
        """准备两个页面、接口依赖和真实存在的任务关联文件。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.meta = self.root / ".devagentstudio"
        (self.meta / "plans").mkdir(parents=True)
        self.product = {"confirmation_status": "confirmed", "pages": [
            {"pageId": "orders", "name": "订单列表", "path": "/page/orders", "description": "按状态查看订单", "goal": "核对订单"},
            {"pageId": "other", "name": "另一页面", "path": "/page/other"},
        ]}
        self.technical = {"confirmation_status": "confirmed", "entities": [], "pages": [{"pageId": "orders", "references": {
            "endpoint_dependencies": [{"endpoint_id": "api.list"}]}}],
            "api_contracts": [{"id": "api", "endpoints": [{"id": "api.list", "name": "查询订单", "method": "GET", "path": "/api/orders"}]}]}
        self.write("plans/product-plan.json", self.product)
        self.write("plans/technical-plan.json", self.technical)
        self.write("application.json", {"branchName": "v2_0"})
        lifecycle = create_application_lifecycle(application_id="app", application_name="测试应用")
        lifecycle.development_artifacts.catalog_error = None
        lifecycle.development_artifacts.pages["orders"] = DevelopmentArtifactProgress(initialDevelopmentStatus="pending")
        write_application_lifecycle(self.root, lifecycle)
        self.plan = {
            "schema_version": "build-dag.v4", "confirmation_status": "confirmed", "status": "ready",
            "unit_graph": {"nodes": ["page:orders", "backend:endpoint:api:api.list", "page:other"],
                           "edges": [{"type": "depends_on", "from": "backend:endpoint:api:api.list", "to": "page:orders"}],
                           "validation": {"is_valid": True, "errors": []}},
            "task_graph": {"validation": {"is_valid": True}},
            "task_registry": {
                "orders.ui": {"id": "orders.ui", "title": "真实页面实现任务", "unit_id": "page:orders", "status": "completed", "dependencies": ["api.list"], "target_files": ["frontend/orders.tsx", "frontend/not-created.tsx", "../escape.txt"]},
                "api.list": {"id": "api.list", "title": "真实查询接口任务", "unit_id": "backend:endpoint:api:api.list", "status": "already_satisfied", "dependencies": []},
                "other": {"id": "other", "title": "无关任务", "unit_id": "page:other", "status": "failed", "dependencies": []},
            },
        }
        (self.root / "frontend").mkdir()
        (self.root / "frontend/orders.tsx").write_text("export default () => null")

    def write(self, path: str, value: object) -> None:
        """把指定当前来源写入临时工作区。"""
        (self.meta / path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def save_tasks(self) -> None:
        """使用生产内容引用存储写入正式 DAG。"""
        write_build_task_plan_json({"workspace": str(self.root)}, self.plan)

    def overview(self) -> dict:
        """调用生产只读概览投影。"""
        return page_artifact_overview(str(self.root), "orders")

    def test_scope_sources_and_files_are_factual_and_read_only(self) -> None:
        """只展示页面闭包，存在的计划目标不冒充已交付文件，任务完成不推导页面完成。"""
        self.save_tasks()
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = self.overview()
        self.assertEqual(result["page"]["description"], "按状态查看订单")
        self.assertNotIn("branchName", result)
        self.assertNotIn("goal", result["page"])
        self.assertEqual([task["id"] for task in result["tasks"]], ["orders.ui", "api.list"])
        self.assertEqual([task["scope"] for task in result["tasks"]], ["page", "dependency"])
        self.assertEqual(result["progress"]["initialDevelopmentStatus"], "pending")
        self.assertEqual(result["files"], [{"path": "frontend/orders.tsx", "taskIds": ["orders.ui"]}])
        self.assertEqual(result["endpoints"][0]["endpointId"], "api.list")
        self.assertEqual(result["issues"], {})
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_missing_plan_is_empty_and_refresh_reads_current_status(self) -> None:
        """没有正式任务时不补造任务，后续状态修改由再次读取真实获取。"""
        self.assertFalse(self.overview()["taskPlanAvailable"])
        self.assertEqual(self.overview()["tasks"], [])
        self.save_tasks()
        self.plan["task_registry"]["orders.ui"]["status"] = "failed"
        self.plan["task_registry"]["orders.ui"]["failure_reason"] = "真实构建失败"
        self.save_tasks()
        self.assertEqual(self.overview()["tasks"][0]["failureReason"], "真实构建失败")

    def test_corruption_and_unconfirmed_sources_are_not_empty_success(self) -> None:
        """损坏和未确认任务单独报错，不清空或伪造其他来源。"""
        self.write("plans/build-task-plan.json", {})
        result = self.overview()
        self.assertIn("tasks", result["issues"])
        self.assertEqual(result["page"]["name"], "订单列表")
        (self.meta / "plans/technical-plan.json").write_text("broken")
        self.assertIn("endpoints", self.overview()["issues"])
        self.write("plans/product-plan.json", {"pages": []})
        with self.assertRaisesRegex(ValueError, "没有唯一对应"):
            self.overview()

    def test_long_task_details_and_dangling_endpoints(self) -> None:
        """完整保留真实长任务说明，找不到接口时显式错误而非无接口。"""
        self.plan["task_registry"]["orders.ui"]["description"] = "真实任务说明" * 1000
        self.save_tasks()
        self.assertEqual(self.overview()["tasks"][0]["description"], "真实任务说明" * 1000)
        self.technical["api_contracts"] = []
        self.write("plans/technical-plan.json", self.technical)
        self.assertIn("不存在或身份不唯一", self.overview()["issues"]["endpoints"])

    def test_ag_ui_success_and_handled_failure_emit_complete_lifecycle(self) -> None:
        """真实 ASGI 流覆盖成功和请求校验失败，两者均发送消息、结果、快照和结束事件。"""
        app = FastAPI()
        app.include_router(page_artifacts_router)
        client = TestClient(app)
        for request, expected in [({"action": "get", "workspaceRoot": str(self.root), "pageId": "orders"}, "completed"),
                                  ({"action": "get", "workspaceRoot": str(self.root), "pageId": "orders", "status": "completed"}, "failed")]:
            response = client.post("/page-artifacts/run", json={"threadId": "view", "runId": "read", "forwardedProps": {"pageArtifact": request}})
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            types = [event["type"] for event in events]
            self.assertEqual(types[0], "RUN_STARTED")
            self.assertEqual(types[-1], "RUN_FINISHED")
            for kind in ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT"]:
                self.assertIn(kind, types)
            self.assertEqual(events[-1]["result"]["pageArtifact"]["status"], expected)


if __name__ == "__main__":
    unittest.main()
