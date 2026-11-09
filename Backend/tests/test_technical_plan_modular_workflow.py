"""从生产 AG-UI Graph 验证分段生成仍提交单份文档并停在原确认门。"""

from copy import deepcopy
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.graph.application_planning_workflow import application_planning_graph_for_request
from app.graph.nodes.application_technical_planning import technical_planning_generate
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.technical_planning_retry import prepare_technical_planning_retry
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from app.workspace.plan_documents import technical_plan_json_path, technical_plan_draft_json_path
from tests.test_technical_plan_modular_generation import fragment_outputs, catalog_bindings
from tests.technical_plan_catalog_fixture import catalog_responses
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class TechnicalPlanModularWorkflowTests(IsolatedAsyncioTestCase):
    """仅替换外部模型响应，保留真实编译、校验、落盘、checkpoint 和确认生命周期。"""

    async def asyncSetUp(self):
        """创建正式上游均已确认的隔离应用。"""

        self.h = TechnicalPlanningRecoveryJourneyHarness()
        await self.h.initialize_graph()
        plan = self.h.candidate
        specs = []
        for contract in plan["api_contracts"]:
            endpoint_ids = {item["id"] for item in contract["endpoints"]}
            specs.append({
                **{key: deepcopy(contract[key]) for key in ("id", "name", "entity_ids", "base_path")},
                "page_ids": [page["pageId"] for page in plan["pages"] if any(
                    dependency["endpoint_id"] in endpoint_ids
                    for dependency in page["references"]["endpoint_dependencies"]
                )],
                "endpoints": [{key: deepcopy(endpoint[key]) for key in ("id", "name", "method", "path", "summary")}
                    for endpoint in contract["endpoints"]],
            })
        self.catalog = {"entities": [
            {key: deepcopy(entity[key]) for key in ("id", "name", "description", "fields")}
            for entity in plan["entities"]
        ], "contracts": specs, "action_bindings": catalog_bindings(plan["pages"])}
        self.outputs = fragment_outputs(self.catalog, plan["api_contracts"], plan["pages"])
        self.catalog_count = len(catalog_responses(self.catalog))

    async def asyncTearDown(self):
        """关闭工作区 checkpoint，随后释放隔离文件。"""

        await close_workflow_checkpointer_for_workspace(workspace=str(self.h.workspace), project_id=self.h.project_id)
        self.h.close()

    async def _run(self, outputs):
        """从原 AG-UI 工作流运行入口执行拆分生成，并投递真实 token 回调。"""

        responses = iter(outputs)

        def invoke(prompt, *, settings, on_token=None):
            """将受控响应送入真实分段解析，并沿原 token 回调投射进度。"""

            response = next(responses)
            if isinstance(response, Exception):
                raise response
            if on_token:
                on_token(response)
            return response

        with patch("app.config.Settings.from_env", side_effect=self.h.current_settings), patch(
            "app.agents.main.planner._invoke_prompt_with_chat_model", side_effect=invoke,
        ) as model:
            frames = [frame async for frame in build_workflow_ag_ui_stream(
                graph=self.h.graph,
                payload={"threadId": self.h.thread_id, "runId": self.h.source_run_id,
                    "request": self.h.request, "resume_from": "technical_planning_begin",
                    "forwardedProps": {"workspaceRoot": str(self.h.workspace), "workflowScope": "application_planning"}},
            )]
        snapshot = await self.h.graph.aget_state({"configurable": {"thread_id": self.h.thread_id}})
        return snapshot, frames, model.call_count

    async def test_assembled_plan_writes_json_and_stops_at_original_confirmation(self):
        """首版拆分后完整校验通过，仍只有单份待确认计划且没有自动进入模板或开发。"""

        config_path = self.h.workspace / ".devagentstudio/application.json"
        config_before = config_path.read_bytes()
        snapshot, frames, count = await self._run(self.outputs)
        self.assertEqual(count, len(self.outputs))
        self.assertEqual(snapshot.next, ("technical_planning_review",), "\n".join(frames))
        self.assertTrue(any(task.interrupts for task in snapshot.tasks))
        plan = snapshot.values["technical_plan"]
        self.assertEqual(plan["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(plan["api_contracts"], self.h.candidate["api_contracts"])
        self.assertEqual(plan["pages"], self.h.candidate["pages"])
        self.assertEqual(snapshot.values["technical_plan_generation_input_sha256"], "")
        self.assertFalse(TechnicalPlanFragmentStore(self.h.workspace).path.exists())
        self.assertTrue(technical_plan_draft_json_path(snapshot.values).is_file())
        self.assertFalse(technical_plan_json_path(snapshot.values).exists())
        self.assertFalse(technical_plan_draft_json_path(snapshot.values).with_suffix(".md").exists())
        self.assertEqual(config_path.read_bytes(), config_before)
        self.assertTrue(any('"RUN_FINISHED"' in frame for frame in frames))
        self.assertFalse(any('"RUN_ERROR"' in frame for frame in frames))
        self.assertFalse(any("technical_plan_generation_input_sha256" in frame for frame in frames))

    async def test_catalog_repair_still_commits_once_and_waits_for_confirmation(self):
        """页面报告目录缺口后沿真实 Graph 修目录，仍不发布半份计划或绕过确认。"""

        pages = self.h.candidate["pages"]
        target_page = next(page for page in pages if page["references"]["endpoint_dependencies"])
        endpoint_id = target_page["references"]["endpoint_dependencies"][0]["endpoint_id"]
        fixed = deepcopy(self.catalog)
        target_spec = next(c for c in fixed["contracts"] if any(e["id"] == endpoint_id for e in c["endpoints"]))
        target_endpoint = next(e for e in target_spec["endpoints"] if e["id"] == endpoint_id)
        target_endpoint["summary"] += "已核对本页业务用途。"
        contract = deepcopy(next(c for c in self.h.candidate["api_contracts"] if c["id"] == target_spec["id"]))
        next(e for e in contract["endpoints"] if e["id"] == endpoint_id)["summary"] = target_endpoint["summary"]
        issue = {"catalog_issue": {"pageId": target_page["pageId"], "kind": "initial_load",
            "actionId": None, "stepId": None, "reason": "当前目录需要明确本页首次读取的业务用途。"}}
        contract_count = len(self.catalog["contracts"])
        page_index = pages.index(target_page)
        related = [c for c in self.catalog["contracts"] if target_page["pageId"] in c["page_ids"]]
        related_ids = {c["id"] for c in related}
        entity_ids = {entity_id for c in related for entity_id in c["entity_ids"]}
        related_pages = {page_id for c in related for page_id in c["page_ids"]}
        repair_outputs = catalog_responses(fixed, entity_ids=entity_ids, contract_ids=related_ids,
            page_ids=[page["pageId"] for page in pages if page["pageId"] in related_pages])
        outputs = self.outputs[:self.catalog_count + contract_count + page_index] + [json.dumps(issue),
            *[json.dumps(value) for value in repair_outputs], json.dumps({"api_contracts": [contract]})] + self.outputs[self.catalog_count + contract_count + page_index:]
        snapshot, frames, _count = await self._run(outputs)
        self.assertEqual(snapshot.next, ("technical_planning_review",), "\n".join(frames))
        self.assertEqual(snapshot.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
        self.assertTrue(any(task.interrupts for task in snapshot.tasks))
        self.assertTrue(technical_plan_draft_json_path(snapshot.values).is_file())
        self.assertFalse(technical_plan_json_path(snapshot.values).exists())
        self.assertFalse(technical_plan_draft_json_path(snapshot.values).with_suffix(".md").exists())
        self.assertFalse(TechnicalPlanFragmentStore(self.h.workspace).path.exists())
        self.assertFalse(any('"RUN_ERROR"' in frame for frame in frames))
        self.assertFalse(any("catalog_repair" in frame for frame in frames if '"name":"llm.token"' not in frame))

    async def test_each_fragment_succeeds_on_third_attempt_and_keeps_confirmation_gate(self):
        """每个模块前两次失败仍能沿真实 AG-UI 完成，最终只提交一份待确认产物。"""

        outputs = [value for output in self.outputs for value in ('{"broken":', '{}', output)]
        snapshot, frames, count = await self._run(outputs)
        self.assertEqual(count, len(self.outputs) * 3)
        self.assertEqual(snapshot.next, ("technical_planning_review",), "\n".join(frames))
        self.assertTrue(any(task.interrupts for task in snapshot.tasks))
        self.assertEqual(snapshot.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(snapshot.values["technical_plan"]["pages"], self.h.candidate["pages"])
        self.assertTrue(technical_plan_draft_json_path(snapshot.values).is_file())
        self.assertFalse(technical_plan_json_path(snapshot.values).exists())
        self.assertFalse(TechnicalPlanFragmentStore(self.h.workspace).path.exists())
        self.assertTrue(any('"RUN_FINISHED"' in frame for frame in frames))
        self.assertFalse(any('"RUN_ERROR"' in frame for frame in frames))

    async def test_fragment_failure_checkpoint_preserves_modules_and_current_input_retry(self):
        """失败审阅保存成功片段，重新读取 checkpoint 后从失败模块继续，不写半份正式产物。"""

        completed = self.catalog_count + 1
        outputs = self.outputs[:completed] + ['{"api_contracts": ['] * 3
        snapshot, frames, count = await self._run(outputs)
        self.assertEqual(count, completed + 3)
        self.assertEqual(snapshot.next, ("technical_planning_review",), "\n".join(frames))
        self.assertEqual(snapshot.values["clarification"]["mode"], "technical_plan_generation_error")
        self.assertFalse(any("technical_plan_generation_input_sha256" in frame for frame in frames))
        self.assertFalse(technical_plan_json_path(snapshot.values).exists())
        self.assertEqual(set(TechnicalPlanFragmentStore(self.h.workspace).load(snapshot.values["technical_plan_generation_input_sha256"])), {
            "catalog", "contract:" + self.catalog["contracts"][0]["id"],
        })
        await close_workflow_checkpointer_for_workspace(workspace=str(self.h.workspace), project_id=self.h.project_id)
        self.h.graph = await application_planning_graph_for_request(workspace=str(self.h.workspace), project_id=self.h.project_id)
        persisted = await self.h.graph.aget_state({"configurable": {"thread_id": self.h.thread_id}})
        update = prepare_technical_planning_retry(persisted.values, target_node="technical_planning_generate")
        state = {**persisted.values, **update}
        with patch("app.config.Settings.from_env", side_effect=self.h.current_settings), patch(
            "app.agents.main.planner._invoke_prompt_with_chat_model", side_effect=['{"broken":'] * 2 + self.outputs[completed:],
        ) as model:
            generated = technical_planning_generate(state)
        self.assertEqual(model.call_count, len(self.outputs) - completed + 2)
        self.assertEqual(generated["technical_plan_candidate"]["confirmation_status"], "pending_user_confirmation")
        self.assertTrue(generated["technical_plan_generation_input_sha256"])
        self.assertFalse(technical_plan_json_path(snapshot.values).exists())

    async def test_connection_error_preserves_fragments_before_node_checkpoint(self):
        """真实节点连接异常未提交增量时，业务节点重执行仍能读取已持久化的成功模块。"""

        completed = self.catalog_count + 1
        snapshot, frames, count = await self._run(self.outputs[:completed] + [ConnectionError("模型连接断开")])
        self.assertEqual(count, completed + 1)
        self.assertTrue(any('"RUN_ERROR"' in frame for frame in frames))
        self.assertFalse(technical_plan_json_path(snapshot.values).exists())
        store = TechnicalPlanFragmentStore(self.h.workspace)
        digest = json.loads(store.path.read_text())["inputSha256"]
        self.assertEqual(set(store.load(digest)), {"catalog", "contract:" + self.catalog["contracts"][0]["id"]})
        update = prepare_technical_planning_retry(snapshot.values, target_node="technical_planning_generate")
        with patch("app.config.Settings.from_env", side_effect=self.h.current_settings), patch(
            "app.agents.main.planner._invoke_prompt_with_chat_model", side_effect=self.outputs[completed:],
        ) as model:
            generated = technical_planning_generate({**snapshot.values, **update})
        self.assertEqual(model.call_count, len(self.outputs) - completed)
        self.assertEqual(generated["technical_plan_candidate"]["confirmation_status"], "pending_user_confirmation")
