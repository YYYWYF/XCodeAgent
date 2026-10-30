"""只在临时工作区验证右侧提示词确认，不启动 Studio、应用或外部资源。"""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.application_lifecycle import ApplicationLifecycle, WorkbenchExecution, utc_now
from app.services.application_lifecycle import (
    application_lifecycle_path, load_application_lifecycle, write_application_lifecycle,
)
from app.services.runtime_agent_contracts import agent_fact_digest
from app.services.runtime_agent_workbench_settings import execute_settings
from app.services.technical_plan_topology_selection import rebind_selected_topology, validate_selected_topology
from app.services.runtime_agent_settings_requests import settings_request_adapter
from app.workspace.plan_documents import render_project_plan_markdown


class RuntimeAgentSettingsTests(unittest.TestCase):
    """覆盖正式确认、影响范围、取消恢复、并发漂移和写入失败回滚。"""

    def setUp(self):
        """构造两个已完成页面，确保只影响选定 Agent 的关联页面。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        base = self.root / WORKSPACE_ARTIFACT_DIR
        (base / "plans").mkdir(parents=True)
        self.path = base / "plans/technical-plan.json"
        self.agent = {
            "agentId": "policy", "name": "政策助手", "purpose": "解答政策问题", "pageIds": ["policy_page"],
            "agentSettings": {"prompt": {"systemPrompt": "依据公司政策回答。"}, "model": {"source": "project"},
                "memory": {}, "context": {}, "tools": {"enabled": False, "bindings": []},
                "skills": {"enabled": False}, "knowledge": {"enabled": False}},
        }
        self.application = {"schemaVersion": 6, "configRevision": 1, "appName": "测试应用",
            "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database"}}
        self.plan = {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "sourceConfigRevision": 1,
            "topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
                "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"},
            "pages": [{"pageId": "policy_page"}, {"pageId": "other_page"}],
            "api_contracts": [], "entities": [], "agent_contracts": [self.agent]}
        self.plan = rebind_selected_topology(self.plan, self.application)
        self.path.write_text(json.dumps(self.plan, ensure_ascii=False))
        self.path.with_suffix(".md").write_text(render_project_plan_markdown(self.plan))
        (base / "application.json").write_text(json.dumps(self.application))
        (base / "plans/product-plan.json").write_text(json.dumps({
            "confirmation_status": "confirmed", "pages": self.plan["pages"]}))
        # 与实际工作区一致，不虚构配置编辑并不消费的 ui-designs.json。
        completed = {"initialDevelopmentStatus": "completed", "completedAt": utc_now(),
            "completedRunId": "first-run", "completedThreadId": "first-thread", "completedBranchName": "v1_0"}
        self.lifecycle = write_application_lifecycle(self.root, ApplicationLifecycle(
            application={"id": "test", "name": "测试应用"}, updatedAt=utc_now(), revision=1,
            initialization={"stage": "ready_for_workbench", "status": "completed", "threadId": "planning"},
            developmentArtifacts={"pages": {key: deepcopy(completed) for key in ("policy_page", "other_page")}},
            extensions={"reviewStatus": "passed", "keep": True},
        ))

    def request(self, action: str, **extra):
        """沿真实严格请求模型调用设置协调服务。"""

        request = settings_request_adapter.validate_python({"action": action, "workspaceRoot": str(self.root),
            "agentId": "policy", **extra})
        return execute_settings(request, thread_id="settings-thread", run_id="settings-run")

    def prepare(self, settings_patch=None):
        """读取正式身份后创建一次候选，不隐式确认。"""

        current = self.request("get_settings")
        return self.request("preview_settings", contractHash=current["contractHash"],
            technicalPlanSha256=current["technicalPlanSha256"], settingsPatch=settings_patch or {
                "prompt": {"systemPrompt": "请使用简体中文，依据公司政策回答。精简说明。"}})["preview"]

    def submit(self, action: str, preview: dict, **extra):
        """提交服务端签发的精确预览身份，不回传候选配置。"""

        return self.request(action, **{key: preview[key] for key in ("changeId", "draftSha256", "lifecycleRevision")}, **extra)

    def snapshot(self):
        """只读取本次事务涉及的三个正式文件。"""

        return {path: path.read_bytes() for path in (self.path, self.path.with_suffix(".md"), application_lifecycle_path(self.root))}

    def test_preview_is_recoverable_and_confirm_commits_only_prompt(self):
        """预览不落正式配置，确认同步文档并让关联页面及质量结果失效。"""

        before = self.snapshot()
        preview = self.prepare()
        self.assertEqual(self.path.read_bytes(), before[self.path])
        self.assertEqual(self.path.with_suffix(".md").read_bytes(), before[self.path.with_suffix(".md")])
        self.assertEqual(self.request("get_settings")["preview"], preview)
        self.assertEqual(load_application_lifecycle(self.root).extensions, self.lifecycle.extensions)
        result = self.submit("confirm_settings", preview)
        saved = json.loads(self.path.read_text())
        expected = deepcopy(self.plan)
        expected["agent_contracts"][0]["agentSettings"]["prompt"] = preview["after"]["prompt"]
        expected = rebind_selected_topology(expected, self.application)
        self.assertEqual(saved, expected)
        validate_selected_topology(saved, self.application)
        self.assertEqual(self.path.with_suffix(".md").read_text(), render_project_plan_markdown(expected))
        self.assertTrue(result["saved"])
        self.assertEqual(result["agent"]["contractHash"], agent_fact_digest(expected["agent_contracts"][0]))
        current = load_application_lifecycle(self.root)
        self.assertIsNone(current.active_formal_revision)
        self.assertEqual(current.development_artifacts.pages["policy_page"].change_status, "changed")
        self.assertEqual(current.development_artifacts.pages["other_page"], self.lifecycle.development_artifacts.pages["other_page"])
        self.assertEqual(current.development_artifacts.pages["policy_page"].completed_run_id, "first-run")
        self.assertEqual(current.extensions["reviewStatus"], "pending")
        self.assertTrue(current.extensions["keep"])
        self.assertFalse(result["lifecycle"]["testEntryGate"]["allowed"])
        self.assertIsNone(self.request("get_settings")["preview"])
        self.assertNotEqual(agent_fact_digest(self.agent), result["agent"]["contractHash"])

    def test_cancel_preserves_formal_plan_and_completion(self):
        """重开后可放弃候选，原配置、完成事实及质量结果均不改变。"""

        preview = self.prepare()
        self.assertTrue(self.submit("cancel_settings", preview)["cancelled"])
        self.assertEqual(json.loads(self.path.read_text()), self.plan)
        current = load_application_lifecycle(self.root)
        self.assertIsNone(current.active_formal_revision)
        self.assertEqual(current.extensions, self.lifecycle.extensions)
        self.assertEqual(current.development_artifacts, self.lifecycle.development_artifacts)

    def test_ui_design_is_not_a_settings_dependency(self):
        """UI 文件缺失可正常预览，预览后 UI 独立变化也不阻止当前配置确认。"""

        from app.workspace.revision_draft_documents import load_revision_draft

        ui_path = self.path.parent / "ui-designs.json"
        self.assertFalse(ui_path.exists())
        preview = self.prepare()
        metadata, _, _ = load_revision_draft(self.root, change_id=preview["changeId"], artifact_key="agent-settings")
        self.assertEqual({item.artifact_key for item in metadata.based_on_canonical}, {"application", "product-plan"})
        ui_path.write_text("独立 UI 文档的未完成内容")
        self.assertTrue(self.submit("confirm_settings", preview)["saved"])
        self.assertEqual(ui_path.read_text(), "独立 UI 文档的未完成内容")

    def test_product_plan_drift_still_blocks_settings_confirmation(self):
        """裁剪 UI 依赖后仍保护实际 ProductPlan 基线，不吞掉真实上游变更。"""

        preview = self.prepare()
        (self.path.parent / "product-plan.json").write_text('{"confirmation_status":"pending_user_confirmation"}')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "上游文档已变化"):
            self.submit("confirm_settings", preview)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.submit("cancel_settings", preview)["cancelled"])

    def test_all_five_fields_round_trip_and_temperature_can_inherit_again(self):
        """完整配置可预览及确认，清空 Temperature 只移除当前 Agent 的覆盖。"""

        prompt = {"systemPrompt": "请依据政策回答。", "persona": {"role": "政策助手", "tone": "简洁专业"},
            "constraints": ["不编造政策", "说明适用条件"]}
        preview = self.prepare({"prompt": prompt, "model": {"generation": {"temperature": 0.7}}})
        self.assertEqual({item["field"] for item in preview["fieldDiffs"]},
            {"role", "tone", "systemPrompt", "constraints", "temperature"})
        self.assertEqual(preview["after"], {"prompt": prompt, "temperature": 0.7})
        self.submit("confirm_settings", preview)
        saved = json.loads(self.path.read_text())["agent_contracts"][0]
        self.assertEqual(saved["agentSettings"]["model"], {"source": "project", "generation": {"temperature": 0.7}})
        self.assertEqual(saved["agentSettings"]["prompt"], prompt)
        self.assertEqual(saved["pageIds"], self.agent["pageIds"])
        reset = self.prepare({"model": {"generation": None}})
        self.assertEqual([item["field"] for item in reset["fieldDiffs"]], ["temperature"])
        self.submit("confirm_settings", reset)
        saved = json.loads(self.path.read_text())["agent_contracts"][0]
        self.assertEqual(saved["agentSettings"]["model"], {"source": "project"})
        self.assertEqual(saved["agentSettings"]["prompt"], prompt)

    def execution(self, page_id):
        """构造实际页面占用，让配置准入沿当前生命周期判断。"""

        return WorkbenchExecution(scope="page", targetId=page_id, pageId=page_id,
            runId="page-run", threadId="page-thread", phase="build", status="awaiting_user",
            resourceKeys=[f"page:{page_id}"], startedAt=utc_now(), updatedAt=utc_now())

    def test_unrelated_execution_does_not_block_or_become_settings_owner(self):
        """无关页面保持原执行身份；配置保存不能清除或接管它。"""

        execution = self.execution("other_page")
        write_application_lifecycle(self.root, self.lifecycle.model_copy(update={
            "active_run_id": execution.run_id, "active_executions": {execution.run_id: execution}}))
        preview = self.prepare()
        current = load_application_lifecycle(self.root)
        self.assertEqual(current.active_formal_revision.planning_run_id, "settings-run")
        self.submit("confirm_settings", preview)
        current = load_application_lifecycle(self.root)
        self.assertEqual(current.active_run_id, execution.run_id)
        self.assertEqual(current.active_executions, {execution.run_id: execution})

    def test_related_execution_blocks_but_does_not_trap_cancel(self):
        """共享 Agent 工厂的任务仍受保护，放弃草稿不需要结束其他任务。"""

        execution = self.execution("policy_page")
        occupied = self.lifecycle.model_copy(update={"active_run_id": execution.run_id,
            "active_executions": {execution.run_id: execution}})
        write_application_lifecycle(self.root, occupied)
        with self.assertRaisesRegex(ValueError, "占用 Agent 工厂"):
            self.prepare()
        write_application_lifecycle(self.root, self.lifecycle)
        preview = self.prepare()
        current = load_application_lifecycle(self.root)
        write_application_lifecycle(self.root, current.model_copy(update={
            "active_executions": {execution.run_id: execution}}))
        self.assertTrue(self.submit("cancel_settings", preview)["cancelled"])

    def test_patch_rejects_unsupported_parameters_and_invalid_temperature(self):
        """只开放已实现字段；非法数字、凭据和空提交在请求边界拒绝。"""

        for patch_value in ({}, {"model": {"source": "other", "generation": {"temperature": 0.3}}},
                            {"model": {"generation": {"temperature": 3}}},
                            {"model": {"generation": {"temperature": True}}},
                            {"prompt": {"systemPrompt": "规则", "unknown": "不支持"}}):
            with self.subTest(patch=patch_value), self.assertRaises(ValidationError):
                current = self.request("get_settings")
                self.request("preview_settings", contractHash=current["contractHash"],
                    technicalPlanSha256=current["technicalPlanSha256"], settingsPatch=patch_value)

    def test_pending_revision_and_wrong_confirmation_identity_are_rejected(self):
        """当前修订不能重入，错误的预览身份不得提交或清除已有草稿。"""

        preview = self.prepare()
        with self.assertRaisesRegex(ValueError, "未完成的正式修订"):
            self.prepare()
        stale = {**preview, "draftSha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "预览或应用状态已变化"):
            self.submit("confirm_settings", stale)
        self.assertEqual(self.request("get_settings")["preview"], preview)

    def test_drift_blocks_confirmation_without_overwriting_files(self):
        """预览后上游变化不得覆盖，仍允许显式放弃该草稿。"""

        preview = self.prepare()
        upstream = self.root / WORKSPACE_ARTIFACT_DIR / "application.json"
        upstream.write_text('{"name":"另一份配置"}')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "上游文档已变化"):
            self.submit("confirm_settings", preview)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.submit("cancel_settings", preview)["cancelled"])

    def test_transaction_failure_rolls_back_and_retains_preview(self):
        """生命周期写入失败恢复三个文件，确认草稿保留供用户处理。"""

        preview = self.prepare()
        before = self.snapshot()
        with patch("app.services.runtime_agent_workbench_settings.write_application_lifecycle", side_effect=OSError("写入失败")):
            with self.assertRaisesRegex(OSError, "写入失败"):
                self.submit("confirm_settings", preview)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.request("get_settings")["preview"], preview)

    def test_stale_identity_and_unconfirmed_markdown_are_rejected(self):
        """拒绝过期配置身份以及未正式同步的人工作文档。"""

        current = self.request("get_settings")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.request("preview_settings", contractHash="sha256:" + "0" * 64,
                technicalPlanSha256=current["technicalPlanSha256"], settingsPatch={"prompt": {"systemPrompt": "新的提示词"}})
        self.path.with_suffix(".md").write_text("# 用户尚未确认的人工修改")
        with self.assertRaisesRegex(ValueError, "未同步"):
            self.request("preview_settings", contractHash=current["contractHash"],
                technicalPlanSha256=current["technicalPlanSha256"], settingsPatch={"prompt": {"systemPrompt": "新的提示词"}})

    def test_java_and_extra_configuration_cannot_enter_settings_writer(self):
        """Java 不进入 Agent 配置写入；确认请求不能夹带任何未预览配置。"""

        preview = self.prepare()
        with self.assertRaises(ValidationError):
            self.submit("confirm_settings", preview, systemPrompt="替换提示词")
        self.submit("cancel_settings", preview)
        self.path.write_text(json.dumps({"confirmation_status": "confirmed", "api_contracts": []}))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "不存在这个 Agent"):
            self.request("get_settings")
        self.assertEqual(self.snapshot(), before)

    def test_settings_success_and_failure_emit_complete_agui_lifecycle(self):
        """成功及输入错误都通过真实 AG-UI 编码器发出快照和正常收尾。"""

        from app.protocols.agent_runtime_workbench import build_runtime_workbench_stream

        async def collect(extra):
            """消费独立设置动作，不访问 Electron 或任何业务服务。"""

            payload = {"threadId": "settings-thread", "forwardedProps": {"agentRuntimeWorkbench": {
                "action": "get_settings", "workspaceRoot": str(self.root), "agentId": "policy", **extra}}}
            return [json.loads(frame.removeprefix("data: ").strip())
                async for frame in build_runtime_workbench_stream(payload=payload)]

        for extra, status in [({}, "completed"), ({"password": "forbidden"}, "failed")]:
            with self.subTest(status=status):
                events = asyncio.run(collect(extra))
                self.assertEqual(events[0]["type"], "RUN_STARTED")
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                self.assertIn("TEXT_MESSAGE_END", [event["type"] for event in events])
                state = next(event["snapshot"]["agentRuntimeWorkbench"] for event in events if event["type"] == "STATE_SNAPSHOT")
                self.assertEqual(state["status"], status)
                self.assertEqual(state["runId"], events[0]["runId"])
