"""失败收尾与审查重试不得把成功摘要或旧错误当作当前失败。"""

import unittest
import tempfile
from unittest.mock import patch, MagicMock

from app.graph.nodes.code_review import code_review
from app.graph.nodes.lifecycle import handle_failure, launch_project
from app.graph.subgraphs.acceptance import run_acceptance_subgraph
from app.protocols.workflow.projection import _workflow_summary, _workflow_next_nodes
from app.protocols.workflow.lifecycle import project_workflow_lifecycle_boundary


class WorkflowFailureMessageTests(unittest.TestCase):
    """验证共享结果字段在成功、失败与重试之间的归属。"""

    def test_build_finalization_error_overrides_scan_summary_at_failure_boundary(self):
        """Build 收尾失败的既有事实贯穿错误收尾与最终摘要，不改 Build 行为。"""
        state = {"phase": "build", "status": "failed", "message": "代码扫描完成，已建立工作区代码索引。", "build_summary": {"status": "failed", "finalization_error": "模板缺少 Route Projector Descriptor。"}}
        self.assertEqual(_workflow_summary(state, [])["message"], "模板缺少 Route Projector Descriptor。")
        result = handle_failure(state)
        self.assertEqual(result["error"], "模板缺少 Route Projector Descriptor。")
        self.assertEqual(_workflow_summary({**state, **result}, [])["message"], result["error"])
        lifecycle = MagicMock()
        lifecycle.active_executions = {"run": MagicMock()}
        with patch("app.protocols.workflow.lifecycle.load_application_lifecycle", return_value=lifecycle), patch("app.protocols.workflow.lifecycle.update_workbench_execution", return_value=lifecycle) as persist, patch("app.protocols.workflow.lifecycle.application_lifecycle_payload", return_value={}):
            project_workflow_lifecycle_boundary("/workspace", run_id="run", node_name="build", update=state)
        self.assertEqual(persist.call_args.kwargs["error"].message, result["error"])

    def test_other_nodes_do_not_inherit_old_build_finalization_error(self):
        """单测等后续节点的真实错误不能被历史 Build 摘要覆盖。"""
        result = handle_failure({"phase": "unit_test", "status": "failed", "error": "测试生成失败。", "build_summary": {"status": "failed", "finalization_error": "旧模板错误。"}})
        self.assertEqual(result["error"], "测试生成失败。")

    def test_actual_launch_failure_replaces_successful_review_summary(self):
        """真实启动节点经过验收子图后必须覆盖上一阶段的零问题摘要。"""
        reason = "当前应用数据库配置不可用于启动后端：当前应用未配置 datasource.db 数据库信息。"
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.lifecycle.launch_project_preview",
            return_value={"status": "failed", "message": reason},
        ):
            result = run_acceptance_subgraph({
                "workspace": workspace, "status": "completed",
                "message": "Diff 审查完成，读取 1 个变动文件，发现 0 个问题。",
            })
        self.assertEqual(result["error"], reason)
        self.assertIn(reason, result["message"])
        self.assertEqual(_workflow_summary(result, [])["message"], reason)
        self.assertEqual(_workflow_next_nodes("acceptance", result), [])

    def test_acceptance_projects_only_confirmed_completion(self):
        """失败和待验收不宣告 finalize 开始，避免覆盖 durable 失败节点。"""
        for status in ("failed", "requires_user_input"):
            self.assertEqual(_workflow_next_nodes("acceptance", {"status": status}), [])
        self.assertEqual(_workflow_next_nodes("acceptance", {
            "status": "completed", "accepted": True,
        }), ["finalize_project"])

    def test_successful_launch_clears_previous_failure(self):
        """成功重试后写回新的结果摘要并清空旧错误。"""
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.nodes.lifecycle.launch_project_preview",
            return_value={"status": "completed", "preview_url": "http://127.0.0.1:3000"},
        ):
            result = launch_project({"workspace": workspace, "error": "旧启动错误"})
        self.assertEqual(result["error"], "")
        self.assertIn("项目已启动", result["message"])

    def test_completed_review_summary_is_not_failure_reason(self):
        """完成审查后进入失败收尾，不得把零问题摘要展示为错误。"""
        scan_message = "Diff 审查完成，读取 1 个变动文件，发现 0 个问题。"
        result = handle_failure({"status": "completed", "message": scan_message})
        self.assertEqual(result["status"], "failed")
        self.assertIn("未提供具体错误原因", result["error"])
        self.assertNotIn(scan_message, _workflow_summary(result, [])["message"])

    def test_current_error_takes_priority_over_scan_summary(self):
        """失败收尾和最终 AG-UI 摘要一致展示实际错误。"""
        result = handle_failure({
            "status": "failed", "message": "Diff 审查完成，发现 0 个问题。",
            "error": "项目预览启动失败。",
        })
        self.assertEqual(result["message"], "项目预览启动失败。")
        self.assertEqual(_workflow_summary(result, [])["message"], result["error"])

    def test_business_failure_without_error_preserves_reason(self):
        """明确失败的业务节点只有 message 时仍保留其具体原因。"""
        result = handle_failure({"status": "failed", "message": "修复额度已耗尽。"})
        self.assertEqual(result["error"], "修复额度已耗尽。")

    def test_review_success_and_confirmation_clear_inherited_error(self):
        """子图合并态继承旧错误时，主图成功或确认增量必须显式清空。"""
        for status in ("completed", "requires_user_input", "failed"):
            with self.subTest(status=status), patch(
                "app.graph.nodes.code_review.run_code_review_subgraph",
                return_value={"status": status, "error": "模型请求超时。"},
            ):
                update = code_review({"workspace": "/tmp/review-message-test"})
            self.assertEqual(update["error"], "模型请求超时。" if status == "failed" else "")


if __name__ == "__main__":
    unittest.main()
