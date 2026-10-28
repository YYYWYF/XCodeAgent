"""验证平台保存的模型文档上报，以及人工保存、确认和失败的边界。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation, ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload, application_planning_sha256,
)
from app.graph.nodes.application_technical_planning import technical_planning_commit
from app.graph.nodes.product_planning import product_planning
from app.graph.nodes.requirements import requirements
from app.services.generated_document_inclusion import write_generated_documents
from app.services.product_plan import create_product_plan
from app.services.requirement_spec import create_requirement_spec
from app.services.revision_drafts import create_revision_draft, save_revision_draft_markdown
from app.tools.ask_user import clear_clarification
from app.workspace.product_plan_documents import write_product_plan_documents
from app.workspace.revision_draft_documents import revision_draft_directory
from app.workspace.spec_documents import (
    requirement_spec_draft_markdown_path, write_requirement_spec_draft_document,
)


class GeneratedDocumentInclusionTests(unittest.TestCase):
    """使用真实文档落盘函数及节点，只替换模型和统计服务。"""

    def setUp(self):
        """创建隔离工作区、明确模型身份和测试发送器。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.state = {"workspace": str(self.root), "active_thread_id": "document-thread"}
        config = self.root / ".devagentstudio/application.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"schemaVersion": 6, "configRevision": 1,
            "datasource": {"type": "static"}, "auth": {"enable": False},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []}}))
        self.spec = create_requirement_spec("一个静态公告页面，不需要登录或权限。")
        settings = patch("app.services.generated_document_inclusion.Settings.from_env",
                         return_value=SimpleNamespace(model_api_name="document-model"))
        settings.start()
        self.addCleanup(settings.stop)
        git = patch("app.services.inclusion_rate._git_fields", return_value={})
        git.start()
        self.addCleanup(git.stop)
        post = patch("app.services.inclusion_rate.httpx.post", return_value=httpx.Response(
            200, json={"returnCode": "SUC0000"}, request=httpx.Request("POST", "http://report.test")))
        self.post = post.start()
        self.addCleanup(post.stop)
        self.executor = ThreadPoolExecutor(max_workers=1)
        executor = patch("app.services.inclusion_rate._executor", self.executor)
        executor.start()
        self.addCleanup(executor.stop)
        self.addCleanup(self.executor.shutdown)

    def wait_reports(self):
        """等候此前的后台任务，用于稳定断言请求。"""
        self.executor.submit(threading.Event().set).result(timeout=5)

    def records(self):
        """读取所有已完成请求的编辑块。"""
        self.wait_reports()
        return [record for call in self.post.call_args_list
                for record in call.kwargs["json"]["inclusionRate"]]

    def test_requirement_generation_reports_only_written_markdown(self):
        """需求模型生成后真实落盘再上报，保存的内部 JSON 不重复计数。"""
        with patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                   return_value={"requirement_spec": self.spec, "clarification": clear_clarification(self.spec)}):
            result = requirements({**self.state, "request": "生成静态公告页需求"})
        records = self.records()
        self.assertTrue(records)
        self.assertTrue(all(record["fileName"].endswith("requirement-spec.md") for record in records))
        self.assertTrue(Path(result["requirement_spec_path"]).is_file())
        self.assertTrue(Path(result["requirement_spec_json_path"]).is_file())
        self.assertTrue(all(record["snippet"] == "" for record in records))
        self.assertTrue(all(record["sessionId"] == "document-thread" and record["model"] == "document-model"
                            for record in records))

    def test_product_generation_and_confirmation_do_not_double_report(self):
        """产品计划生成计一次，联合确认提升正式文件不再计数。"""
        with patch("app.graph.nodes.product_planning.plan_product_with_chat_model",
                   return_value=create_product_plan(self.spec)):
            pending = product_planning({**self.state, "requirement_spec": self.spec, "product_plan": {}})
        records = self.records()
        self.assertEqual(self.post.call_count, 1)
        self.assertTrue(all(record["fileName"].endswith("drafts/plans/product-plan.md") for record in records))
        confirmed = product_planning({**self.state, **pending, "requirement_spec": self.spec,
            "workflow_scope": "application_planning", "application_planning_interaction": {"action": "confirm"}})
        self.wait_reports()
        self.assertEqual(confirmed["status"], "completed")
        self.assertEqual(self.post.call_count, 1)

    def test_technical_commit_reports_once_and_replay_is_unchanged(self):
        """事务化技术规划提交真实文件后上报，幂等重放不增加请求。"""
        candidate = {"artifact_type": "technical-plan", "app": {"name": "文档统计"},
                     "pages": [], "entities": [], "api_contracts": [],
                     "confirmation_status": "pending_user_confirmation"}
        state = {**self.state, "technical_plan_candidate": candidate,
            "technical_plan_candidate_sha256": application_planning_sha256(candidate),
            "application_planning_recovery_boundary": application_planning_boundary_payload(
                operation_id="document-operation", operation=ApplicationPlanningOperation.INITIAL,
                boundary=ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED,
                request="生成技术规划", candidate=candidate)}
        lifecycle = SimpleNamespace(initialization=SimpleNamespace(
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            status=ApplicationLifecycleStatus.RUNNING))
        with patch("app.graph.nodes.application_technical_planning._ensure_technical_lifecycle",
                   return_value=(str(self.root), lifecycle)), \
             patch("app.graph.nodes.application_technical_planning.persist_application_lifecycle_transition"), \
             patch("app.graph.nodes.application_technical_planning.application_lifecycle_payload", return_value={}):
            result = technical_planning_commit(state)
            technical_planning_commit(state)
        records = self.records()
        self.assertEqual(self.post.call_count, 1)
        self.assertTrue(all(record["fileName"] == ".devagentstudio/plans/technical-plan.md" for record in records))
        self.assertEqual(result["status"], "requires_user_input")

    def test_regeneration_reports_diff_but_manual_save_does_not(self):
        """重新生成上报真实差异，普通编辑保存不进入 AI 上报。"""
        path = requirement_spec_draft_markdown_path(self.state)
        write_requirement_spec_draft_document(self.state, self.spec)
        updated = deepcopy(self.spec)
        updated["app_info"]["name"] = "修改后的公告"
        write_generated_documents(self.state, [path], write_requirement_spec_draft_document, self.state, updated)
        records = self.records()
        self.assertTrue(records)
        self.assertTrue(any(record["snippet"] for record in records))
        write_requirement_spec_draft_document(self.state, self.spec)
        self.wait_reports()
        self.assertEqual(self.post.call_count, 1)

    def test_revision_draft_generation_and_manual_edit_are_separate(self):
        """正式修订草稿生成上报，后续人工 Markdown 保存不报告。"""
        path = revision_draft_directory(self.root, change_id="change-1", artifact_key="technical-plan") / "artifact.md"
        write_generated_documents(self.state, [path], create_revision_draft, self.root,
            change_id="change-1", artifact_key="technical-plan", kind="technical_plan", target_id="application",
            canonical_json_path=self.root / "missing.json", markdown="# 新技术规划\n",
            artifact={"app": {"name": "修订测试"}}, based_on_paths={})
        self.assertEqual(len(self.records()), 1)
        save_revision_draft_markdown(self.root, change_id="change-1", artifact_key="technical-plan", markdown="# 人工编辑\n")
        self.wait_reports()
        self.assertEqual(self.post.call_count, 1)

    def test_write_failure_does_not_report_and_is_not_swallowed(self):
        """保存失败不会上报，并保持原写盘错误可见。"""
        with self.assertRaises(OSError):
            write_generated_documents(self.state, [self.root / "failed.md"],
                                      Mock(side_effect=OSError("保存失败")))
        self.wait_reports()
        self.post.assert_not_called()

    def test_live_thread_identity_takes_precedence_over_state(self):
        """报告绑定当前调用的业务 thread，不误用状态中的旧会话身份。"""
        with patch("app.services.generated_document_inclusion.ensure_config",
                   return_value={"configurable": {"thread_id": "current-thread"}}):
            write_generated_documents(self.state, [requirement_spec_draft_markdown_path(self.state)],
                write_requirement_spec_draft_document, self.state, self.spec)
        self.assertTrue(all(record["sessionId"] == "current-thread" for record in self.records()))

    def test_reporting_setup_failure_preserves_document_write(self):
        """统计身份准备失败时仍执行原保存函数一次，不引入文档生成失败。"""
        writer = Mock(wraps=write_requirement_spec_draft_document)
        with patch("app.services.generated_document_inclusion.Settings.from_env", side_effect=ValueError("无统计配置")):
            result = write_generated_documents(self.state, [requirement_spec_draft_markdown_path(self.state)],
                                               writer, self.state, self.spec)
        writer.assert_called_once_with(self.state, self.spec)
        self.assertTrue(Path(result).exists())
        self.wait_reports()
        self.post.assert_not_called()

    def test_slow_failed_report_does_not_block_document_generation(self):
        """统计服务未响应时保存已经返回，网络失败仍无日志和异常。"""
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []

        def fail_later(*args, **kwargs):
            """模拟保持在途后超时的上报服务。"""
            started.set()
            release.wait(5)
            raise httpx.ReadTimeout("timeout")

        self.post.side_effect = fail_later

        def generate_document():
            """在线程中执行真实保存，网络仍在等待时必须返回。"""
            try:
                results.append(write_generated_documents(
                    self.state, [requirement_spec_draft_markdown_path(self.state)],
                    write_requirement_spec_draft_document, self.state, self.spec))
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=generate_document)
        worker.start()
        try:
            with self.assertNoLogs("app.services.inclusion_rate"):
                self.assertTrue(started.wait(2))
                self.assertTrue(finished.wait(1), "文档保存不能等待统计服务")
                self.assertFalse(errors)
                self.assertTrue(Path(results[0]).exists())
                self.assertFalse(release.is_set())
                release.set()
                self.wait_reports()
        finally:
            release.set()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
