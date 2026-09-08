from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.services.agent_development_readiness import agent_contract_sha256
from app.services.agent_settings_revision import execute_agent_settings_revision
from app.services.agent_settings_revision_models import parse_agent_settings_revision_request
from app.services.agent_settings_revision_support import document_sha256
from app.protocols.application_page_planning import (
    build_application_page_planning_ag_ui_stream,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    create_application_lifecycle,
    load_application_lifecycle,
    start_workbench_execution,
    stop_workbench_execution,
    write_application_lifecycle,
)
from app.services.project_plan import create_technical_plan
from app.workspace.plan_documents import render_project_plan_markdown
from tests.test_agent_technical_plan import AgentTechnicalPlanTests


class AgentSettingsRevisionTests(unittest.TestCase):
    """验证 Prompt/Temperature 可视化修改不会绕过正式 Contract 重编译与确认。"""

    def _workspace(self, root: Path) -> tuple[Path, dict, dict]:
        """创建包含已确认 Agent ProductPlan、TechnicalPlan 和 lifecycle 的工作区。"""

        fixture = AgentTechnicalPlanTests()
        requirement = fixture._requirement_with_product_agent()
        product_plan = requirement["confirmed_product_plan"]
        product_plan["confirmation_status"] = "confirmed"
        technical_plan = create_technical_plan(
            requirement,
            agent_plan=fixture._technical_model_plan(requirement),
        )
        technical_plan["confirmation_status"] = "confirmed"
        plans = root / ".xcodeagent" / "plans"
        plans.mkdir(parents=True)
        (plans / "product-plan.json").write_text(
            json.dumps(product_plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (plans / "technical-plan.json").write_text(
            json.dumps(technical_plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (plans / "technical-plan.md").write_text(
            render_project_plan_markdown(technical_plan),
            encoding="utf-8",
        )
        lifecycle = create_application_lifecycle(
            application_id="agent-settings-test",
            application_name="Agent Settings 测试",
            initialization_thread_id="planning-thread",
        ).model_copy(
            update={
                "initialization": ApplicationInitialization(
                    stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    status=ApplicationLifecycleStatus.COMPLETED,
                    threadId="planning-thread",
                )
            }
        )
        write_application_lifecycle(root, lifecycle, expected_revision=0)
        return plans, product_plan, technical_plan

    def _prepare_payload(self, plans: Path, technical_plan: dict) -> dict:
        """构造同时修改 Prompt 与 Temperature 的严格预览请求。"""

        contract = technical_plan["agent_contracts"][0]
        prompt = contract["agentSettings"]["prompt"]
        return {
            "action": "prepare_agent_settings_revision",
            "workspaceRoot": str(plans.parent.parent),
            "agentId": "inventory_assistant",
            "basedOnContractHash": agent_contract_sha256(contract),
            "basedOnTechnicalPlanSha256": document_sha256(
                plans / "technical-plan.json"
            ),
            "changedSections": ["prompt", "model"],
            "settingsPatch": {
                "prompt": {
                    "persona": {
                        "role": prompt["persona"]["role"],
                        "tone": "专业、简洁",
                    },
                    "systemPrompt": "只根据已授权工具结果解释库存状态。",
                    "constraints": ["不得虚构库存数据"],
                },
                "model": {"generation": {"temperature": 0.4}},
            },
        }

    def test_prepare_and_confirm_recompile_formal_technical_plan(self) -> None:
        """预览不覆盖 canonical，确认后更新七段 Contract 并释放正式修订。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans, _product_plan, technical_plan = self._workspace(root)
            old_content = (plans / "technical-plan.json").read_text(encoding="utf-8")
            prepare_request = parse_agent_settings_revision_request(
                self._prepare_payload(plans, technical_plan)
            )
            preview, _message = execute_agent_settings_revision(
                prepare_request,
                source_thread_id="settings-thread",
                source_run_id="settings-run",
            )

            self.assertTrue(preview["active"])
            self.assertEqual(
                (plans / "technical-plan.json").read_text(encoding="utf-8"),
                old_content,
            )
            self.assertEqual(
                [item["field"] for item in preview["fieldDiffs"]],
                [
                    "prompt.persona.tone",
                    "prompt.systemPrompt",
                    "prompt.constraints",
                    "model.generation.temperature",
                ],
            )
            lifecycle = load_application_lifecycle(root)
            self.assertIsNotNone(lifecycle)
            self.assertEqual(lifecycle.active_formal_revision.target.type, "agent")

            confirm_request = parse_agent_settings_revision_request(
                {
                    "action": "confirm_agent_settings_revision",
                    "workspaceRoot": str(root),
                    "agentId": "inventory_assistant",
                    "changeId": preview["changeId"],
                    "basedOnLifecycleRevision": preview["basedOnLifecycleRevision"],
                    "draftSha256": preview["draftSha256"],
                }
            )
            confirmed, _message = execute_agent_settings_revision(
                confirm_request,
                source_thread_id="settings-thread",
                source_run_id="confirm-run",
            )

            saved = json.loads((plans / "technical-plan.json").read_text(encoding="utf-8"))
            settings = saved["agent_contracts"][0]["agentSettings"]
            self.assertEqual(settings["prompt"]["persona"]["tone"], "专业、简洁")
            self.assertEqual(settings["model"]["generation"]["temperature"], 0.4)
            self.assertEqual(saved["confirmation_status"], "confirmed")
            self.assertEqual(confirmed["contractHash"], agent_contract_sha256(saved["agent_contracts"][0]))
            self.assertIsNone(load_application_lifecycle(root).active_formal_revision)

    def test_get_returns_current_formal_identity_without_pending_revision(self) -> None:
        """无待确认预览时也必须返回前端发起 CAS 修改所需的正式身份。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans, _product_plan, technical_plan = self._workspace(root)
            contract = technical_plan["agent_contracts"][0]
            request = parse_agent_settings_revision_request(
                {
                    "action": "get_agent_settings_revision",
                    "workspaceRoot": str(root),
                    "agentId": "inventory_assistant",
                }
            )

            state, _message = execute_agent_settings_revision(
                request,
                source_thread_id="settings-thread",
                source_run_id="get-run",
            )

            self.assertFalse(state["active"])
            self.assertEqual(state["contractHash"], agent_contract_sha256(contract))
            self.assertEqual(
                state["technicalPlanSha256"],
                document_sha256(plans / "technical-plan.json"),
            )

    def test_pending_preview_blocks_agent_build_until_user_decides(self) -> None:
        """Agent Settings 预览等待确认时不得并发启动读取旧 Contract 的 Agent Build。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans, _product_plan, technical_plan = self._workspace(root)
            execute_agent_settings_revision(
                parse_agent_settings_revision_request(
                    self._prepare_payload(plans, technical_plan)
                ),
                source_thread_id="settings-thread",
                source_run_id="settings-run",
            )

            with self.assertRaisesRegex(
                ApplicationLifecycleConflictError,
                "待确认的 Settings 修改",
            ):
                start_workbench_execution(
                    root,
                    scope="agent",
                    target_id="inventory_assistant",
                    page_id=None,
                    thread_id="build-thread",
                    run_id="build-run",
                    phase="development_readiness",
                )

    def test_stopped_agent_task_requires_explicit_end_before_prepare(self) -> None:
        """停止保留恢复能力和资源锁，Settings 修订必须提示用户显式结束。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans, _product_plan, technical_plan = self._workspace(root)
            start_workbench_execution(
                root,
                scope="agent",
                target_id="inventory_assistant",
                page_id=None,
                thread_id="build-thread",
                run_id="build-run",
                phase="build",
            )
            stop_workbench_execution(root, run_id="build-run")

            with self.assertRaisesRegex(
                ApplicationLifecycleConflictError,
                "已停止但尚未结束",
            ):
                execute_agent_settings_revision(
                    parse_agent_settings_revision_request(
                        self._prepare_payload(plans, technical_plan)
                    ),
                    source_thread_id="settings-thread",
                    source_run_id="settings-run",
                )

    def test_orphan_agent_lock_is_cleaned_when_prepare_acquires_revision(self) -> None:
        """没有 active execution 的孤立 Agent 锁不得永久阻止 Settings 修订。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans, _product_plan, technical_plan = self._workspace(root)
            running = start_workbench_execution(
                root,
                scope="agent",
                target_id="inventory_assistant",
                page_id=None,
                thread_id="build-thread",
                run_id="orphan-run",
                phase="build",
            )
            orphaned = running.model_copy(
                update={
                    "revision": running.revision + 1,
                    "active_executions": {},
                }
            )
            write_application_lifecycle(
                root,
                orphaned,
                expected_revision=running.revision,
            )

            preview, _message = execute_agent_settings_revision(
                parse_agent_settings_revision_request(
                    self._prepare_payload(plans, technical_plan)
                ),
                source_thread_id="settings-thread",
                source_run_id="settings-run",
            )

            lifecycle = load_application_lifecycle(root)
            self.assertTrue(preview["active"])
            self.assertIsNotNone(lifecycle)
            self.assertNotIn("inventory_assistant", lifecycle.resource_locks.agents)
            self.assertIsNotNone(lifecycle.active_formal_revision)

    def test_get_action_emits_complete_ag_ui_lifecycle(self) -> None:
        """读取正式身份必须发送消息、结果、状态快照和完整 Run 生命周期。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans, _product_plan, technical_plan = self._workspace(root)
            stream = build_application_page_planning_ag_ui_stream(
                graph=object(),
                payload={
                    "threadId": "settings-thread",
                    "runId": "settings-get-run",
                    "forwardedProps": {
                        "agentSettingsRevision": {
                            "action": "get_agent_settings_revision",
                            "workspaceRoot": str(root),
                            "agentId": "inventory_assistant",
                        }
                    },
                },
            )

            async def collect() -> str:
                """消费 Agent Settings 查询产生的全部 AG-UI 帧。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect())

            self.assertIn("RUN_STARTED", frames)
            self.assertIn("TEXT_MESSAGE_START", frames)
            self.assertIn("TEXT_MESSAGE_CONTENT", frames)
            self.assertIn("TEXT_MESSAGE_END", frames)
            self.assertIn("CUSTOM", frames)
            self.assertIn("STATE_SNAPSHOT", frames)
            self.assertIn("RUN_FINISHED", frames)
            self.assertIn("agent-settings-revision", frames)
            self.assertIn("agentSettingsRevision", frames)
            self.assertIn(agent_contract_sha256(technical_plan["agent_contracts"][0]), frames)
            self.assertIn(document_sha256(plans / "technical-plan.json"), frames)

    def test_invalid_prepare_action_returns_handled_ag_ui_failure(self) -> None:
        """缺少 TechnicalPlan CAS 身份的请求必须以 handled failure 完成 AG-UI Run。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans, _product_plan, technical_plan = self._workspace(root)
            invalid_payload = self._prepare_payload(plans, technical_plan)
            invalid_payload.pop("basedOnTechnicalPlanSha256")
            stream = build_application_page_planning_ag_ui_stream(
                graph=object(),
                payload={
                    "threadId": "settings-thread",
                    "runId": "settings-invalid-run",
                    "forwardedProps": {"agentSettingsRevision": invalid_payload},
                },
            )

            async def collect() -> str:
                """消费 Agent Settings 失败动作产生的全部 AG-UI 帧。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect())

            self.assertIn("RUN_STARTED", frames)
            self.assertIn("TEXT_MESSAGE_START", frames)
            self.assertIn("CUSTOM", frames)
            self.assertIn("STATE_SNAPSHOT", frames)
            self.assertIn("RUN_FINISHED", frames)
            self.assertIn('"status":"failed"', frames)
            self.assertIn("basedOnTechnicalPlanSha256", frames)
            self.assertNotIn("RUN_ERROR", frames)

    def test_abandon_preserves_formal_technical_plan(self) -> None:
        """放弃预览必须删除 draft、释放 lease 且保持正式 TechnicalPlan 不变。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans, _product_plan, technical_plan = self._workspace(root)
            old_content = (plans / "technical-plan.json").read_text(encoding="utf-8")
            preview, _message = execute_agent_settings_revision(
                parse_agent_settings_revision_request(
                    self._prepare_payload(plans, technical_plan)
                ),
                source_thread_id="settings-thread",
                source_run_id="settings-run",
            )
            abandon = parse_agent_settings_revision_request(
                {
                    "action": "abandon_agent_settings_revision",
                    "workspaceRoot": str(root),
                    "agentId": "inventory_assistant",
                    "changeId": preview["changeId"],
                    "basedOnLifecycleRevision": preview["basedOnLifecycleRevision"],
                    "draftSha256": preview["draftSha256"],
                }
            )
            execute_agent_settings_revision(
                abandon,
                source_thread_id="settings-thread",
                source_run_id="abandon-run",
            )

            self.assertEqual(
                (plans / "technical-plan.json").read_text(encoding="utf-8"),
                old_content,
            )
            self.assertIsNone(load_application_lifecycle(root).active_formal_revision)

    def test_patch_rejects_platform_owned_fields(self) -> None:
        """请求不能把 requiredCapabilities 等平台字段伪装成模型修改。"""

        with self.assertRaises(ValueError):
            parse_agent_settings_revision_request(
                {
                    "action": "prepare_agent_settings_revision",
                    "workspaceRoot": "/tmp/example",
                    "agentId": "inventory_assistant",
                    "basedOnContractHash": "sha256:" + "a" * 64,
                    "basedOnTechnicalPlanSha256": "sha256:" + "b" * 64,
                    "changedSections": ["model"],
                    "settingsPatch": {
                        "model": {
                            "generation": {"temperature": 0.2},
                            "requiredCapabilities": {"toolCalling": False},
                        }
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
