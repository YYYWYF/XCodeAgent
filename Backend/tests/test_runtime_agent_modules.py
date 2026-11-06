"""覆盖七模块生成职责及原 Build Repair 的身份和正式输入。"""

from copy import deepcopy
import importlib
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.agents.agent_runtime.modules import agent_module_packet, generate_agent_modules_with_deep_agent
from app.services.build_repair_planner import create_build_failure_repair_plan
from app.services.runtime_agent_module_policy import module_source_refs, module_task_errors
from app.services.task_input_freshness import task_input_change_reason


def module_fixture(module: str = "knowledge") -> tuple[dict, dict, dict]:
    """提供与真实失败现场相同的禁用知识、项目模型和精确文件授权。"""

    plan = {"topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
        "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"},
        "pages": [{"pageId": "chat"}], "api_contracts": [], "agent_contracts": [{
            "agentId": "assistant", "name": "助手", "purpose": "通用对话", "pageIds": ["chat"],
            "publicEndpoint": True,
            "interactionContract": {"mode": "conversation", "inputSchema": {"type": "object", "properties": {},
                "required": [], "additionalProperties": False}, "resultSchema": None, "textOutput": True,
                "publicStateSchema": None, "outcomes": [], "interactionKinds": []},
            "agentSettings": {"prompt": {"systemPrompt": "回答当前消息。"}, "model": {"source": "project"},
                "memory": {}, "context": {}, "tools": {"enabled": False, "bindings": []},
                "skills": {"enabled": False}, "knowledge": {"enabled": False}}}]}
    path = "agent-runtime/src/app/agent/factory.py"
    policy = {"policySha256": "current-policy", "modulePaths": {module: {
        "readPaths": ["src/app/agent/factory.py"], "modifyPaths": ["src/app/agent/factory.py"],
        "addRoots": [], "testRoots": ["tests"]}}}
    task = {"id": f"agent:assistant::{module}", "unit_id": "agent:assistant", "owner": "agent",
        "task_type": "agent.code", "target_files": [path], "allowed_paths": [path],
        "can_run_in_parallel": False, "change_scope": [{"path": path, "operation": "modify"}],
        "deliverables": [{"id": "module", "kind": "agent.runtime", "target_id": "assistant", "paths": [path]}],
        "business_acceptance_checks": [{"id": "module-check", "kind": "agent.module_contract",
            "expected": {"module": module, "configuration": {"enabled": False}}, "sources": [], "target_paths": [path]}]}
    task["source_refs"] = module_source_refs(plan, task, policy)
    return plan, task, policy


def repair_task(parent: dict, change_scope: list[dict] | None = None) -> dict:
    """经过真实 Repair 编译器产生原模块的受限修复任务。"""

    result = {"task_id": parent["id"], "status": "failed", "failure_category": "contract_mismatch",
        "scheduler_decision": {"action": "repair"}}
    repaired = create_build_failure_repair_plan(tasks=[parent], failed_results=[result],
        repair_planner=lambda _: {"decision": "repair", "strategy": "修复授权文件中的知识装配。",
            "repair_tasks": [{"description": "移除禁用知识的装配并修复同文件缺失导入。",
                "change_scope": parent["change_scope"] if change_scope is None else change_scope}]})
    return repaired["tasks"][0]


class RuntimeAgentModuleTests(unittest.TestCase):
    """真实合同与 Repair 编译器参与校验，仅替换非确定性的模型调用。"""

    def test_disabled_knowledge_does_not_request_prompt_or_temperature_changes(self) -> None:
        """知识执行输入不能要求使用另一个模块缺失的配置，也不能新增禁用资源。"""

        plan, task, policy = module_fixture()
        with patch("app.agents.agent_runtime.modules.load_agent_runtime_template_policy", return_value=policy), \
             patch("app.agents.agent_runtime.modules.create_agent_runtime_agent"), \
             patch("app.agents.agent_runtime.modules.Settings.from_env", return_value=SimpleNamespace(model_name="test")), \
             patch("app.agents.agent_runtime.modules.invoke_agent_with_tool_activity", return_value=json.dumps({
                 "task_results": [{"task_id": task["id"], "status": "already_satisfied",
                     "satisfaction_evidence": "工厂已无知识资源装配。"}]})) as invoke:
            generate_agent_modules_with_deep_agent(project_plan=plan, build_task_plan={}, tasks=[task], workspace="/tmp/test")
        prompt = invoke.call_args.args[1]["messages"][0]["content"]
        self.assertNotIn("model.model_copy", prompt)
        self.assertNotIn("Use resolvedSystemPrompt", prompt)
        self.assertIn("Do not request resource files for a disabled module", prompt)
        self.assertIn("Preserve other modules", prompt)

    def test_repair_packet_preserves_module_and_result_identity(self) -> None:
        """修复沿父模块校验正式来源，向结果协议返回修复任务自己的 ID。"""

        plan, parent, policy = module_fixture()
        task = repair_task(parent)
        packet = agent_module_packet(plan, task, policy)
        self.assertEqual(packet["task_id"], task["id"])
        self.assertEqual(packet["module"], "knowledge")
        self.assertEqual(packet["configuration"], {"enabled": False})
        self.assertEqual(module_task_errors(task, policy), [])
        self.assertIsNone(task_input_change_reason(task, plan, {"runtime_agent_template_policy": policy}))

    def test_repair_rejects_changed_parent_identity_or_formal_source(self) -> None:
        """修复身份不能自报另一模块，正式输入变更也必须重新规划确认。"""

        plan, parent, policy = module_fixture()
        task = repair_task(parent)
        with self.assertRaises(ValueError):
            agent_module_packet(plan, {**task, "repairs": {"task_id": "agent:assistant::tools"}}, policy)
        changed = deepcopy(plan)
        changed["agent_contracts"][0]["agentSettings"]["knowledge"] = {"enabled": True,
            "files": ["agent-runtime/src/app/knowledge/rules.md"]}
        with self.assertRaises(ValueError):
            agent_module_packet(changed, task, policy)
        with self.assertRaises(ValueError):
            agent_module_packet(plan, {**task, "repairs": ["invalid"]}, policy)

    def test_agent_repair_only_authorizes_its_exact_subset_of_parent_files(self) -> None:
        """多文件模块的单文件修复不能重新授权全部父任务文件。"""

        plan, parent, policy = module_fixture("model")
        path = "agent-runtime/src/app/models/factory.py"
        parent["target_files"].append(path)
        parent["allowed_paths"].append(path)
        parent["deliverables"][0]["paths"].append(path)
        parent["change_scope"].append({"path": path, "operation": "modify"})
        policy["modulePaths"]["model"]["modifyPaths"].append("src/app/models/factory.py")
        task = repair_task(parent, [parent["change_scope"][0]])
        packet = agent_module_packet(plan, task, policy)
        self.assertEqual(packet["target_files"], ["agent-runtime/src/app/agent/factory.py"])
        self.assertEqual(task["allowed_paths"], packet["target_files"])

    def test_repair_planner_receives_current_formal_module_packet(self) -> None:
        """修复模型可核对真实禁用配置，不能仅凭失败回执申请额外知识资源。"""

        plan, task, policy = module_fixture()
        build = importlib.import_module("app.graph.subgraphs.build")
        repair_input = {"original_task": task, "failed_attempt": {"failure_reason": "缺少温度和知识来源"}}
        with patch.object(build, "load_agent_runtime_template_policy", return_value=policy), \
             patch.object(build, "plan_build_failure_repair_with_repair_planner_agent", return_value={}) as planner:
            build._plan_build_repair_with_repair_planner({"workspace": "/tmp/test", "project_plan": plan}, repair_input)
        packet = planner.call_args.kwargs["repair_input"]["agent_module_packet"]
        self.assertEqual(packet["module"], "knowledge")
        self.assertEqual(packet["configuration"], {"enabled": False})
        self.assertEqual(packet["target_files"], task["target_files"])

    def test_explicit_retry_reassesses_repair_when_no_original_task_is_ready(self) -> None:
        """无就绪节点的显式重试仍能重新评估受限修复，普通刷新不重提已拒绝请求。"""

        from tests.test_build_subgraph_scheduler import _ready_build_state
        from app.services.build_task_planner import replace_build_task_plan_tasks

        build = importlib.import_module("app.graph.subgraphs.build")
        formal, task, _ = module_fixture()
        task.update(status="failed", failure_category="contract_mismatch")
        result = {"task_id": task["id"], "status": "failed", "failure_category": "contract_mismatch",
            "scheduler_decision": {"action": "repair"}}
        for retry in (False, True):
            with self.subTest(retry=retry), TemporaryDirectory() as directory:
                plan = replace_build_task_plan_tasks({"schema_version": "build-dag.v4",
                    "build_units": {}, "unit_graph": {"nodes": ["agent:assistant"], "edges": []}}, [task])
                state = _ready_build_state(directory, {"workspace": directory, "project_plan": formal,
                    "build_task_plan": plan, "tasks": [task], "build_results": [result],
                    "retry_failed_tasks": retry, "repair_task_plan": {"source": "build_scheduler",
                        "decision": "terminal_failure", "tasks": []}})
                with patch.object(build, "refresh_build_workspace", side_effect=lambda current: (current, {})), \
                     patch.object(build, "_plan_build_repair_with_repair_planner", return_value={
                         "decision": "requires_user_confirmation", "reason": "新修复范围待审阅"}) as planner, \
                     patch.object(build, "_execute_ready_tasks") as execute:
                    actual = build.run_build_scheduler(state, progress_writer=lambda _: None)
                execute.assert_not_called()
                self.assertEqual(planner.call_count, int(retry))
                self.assertEqual(actual["status"], "requires_user_input" if retry else "failed")
                self.assertEqual(actual["tasks"][0]["status"], "failed")
