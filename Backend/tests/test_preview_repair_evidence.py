"""验证结构化诊断经 AG-UI、规划确认和 SmallTask 消费的完整证据链。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatResult

from app.agents.repair_planner.agent import create_repair_planner_agent
from app.agents.repair_planner.planner import _build_failure_repair_prompt
from app.agents.small_task.agent import create_small_task_agent
from app.protocols.preview_runtime import build_preview_runtime_stream
from app.services.preview_repair_evidence import repair_evidence
from app.services.preview_runtime_state import finish_attempt, read_record, runtime_root
from app.services.preview_runtime_guard import maintenance_owner, release_maintenance
from app.services.small_task import build_small_task_packet


class EvidenceModel(FakeMessagesListChatModel):
    """只替换模型输出，实际 Agent 仍执行工具和中间件。"""

    seen_messages: list[list[BaseMessage]] = []

    def bind_tools(self, tools: object, **kwargs: object) -> "EvidenceModel":
        """保留真实 DeepAgent 的工具调度。"""
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: object) -> ChatResult:
        """记录送入模型的真实证据和工具结果。"""
        self.seen_messages.append(messages)
        result = super()._generate(messages, **kwargs)
        # 每轮工具调用使用新身份；重复复用旧 ID 会被框架直接跳过工具执行。
        for generation in result.generations:
            message = generation.message
            if isinstance(message, AIMessage) and message.tool_calls:
                generation.message = message.model_copy(update={"tool_calls": [
                    {**call, "id": f"{call['id']}:{len(self.seen_messages)}"} for call in message.tool_calls
                ]})
        return result


class PreviewRepairEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """所有修改只针对临时测试工作区，确认前不写用户源码。"""

    def setUp(self) -> None:
        """创建带真实 JSON 语法错误的最小应用。"""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = str(self.root)
        self.thread = self.root.name
        (self.root / ".devagentstudio").mkdir()
        (self.root / ".devagentstudio/application.json").write_text("{}")
        (self.root / "frontend/src").mkdir(parents=True)
        (self.root / "frontend/package.json").write_text('{\n"scripts": {dev": "tool dev"}\n}')

    def tearDown(self) -> None:
        """释放测试目录。"""
        owner = maintenance_owner(self.workspace)
        if owner:
            release_maintenance(self.workspace, owner["threadId"])
        self.temp.cleanup()

    async def request(self, action: str, **fields: str) -> dict:
        """通过实际 AG-UI 消费结构化结果，验证无额外 REST 合同。"""
        payload = {"threadId": self.thread, "runId": action, "forwardedProps": {"previewRuntime": {
            "workspace": self.workspace, "action": action, **fields,
        }}}
        events = []
        async for frame in build_preview_runtime_stream(payload=payload):
            events.extend(json.loads(line[6:]) for line in frame.splitlines() if line.startswith("data: "))
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        return events[-1]["result"]["previewRuntime"]

    async def test_parse_failure_reaches_agent_and_stops_at_confirmation(self) -> None:
        """真实启动解析错误直接成为规划证据，生成计划前后均不得自动写文件。"""
        value = await self.request("restart")
        diagnostic = value["runtime"]["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "JSONDecodeError")
        self.assertEqual(diagnostic["location"]["path"], "frontend/package.json")
        self.assertEqual(diagnostic["location"]["line"], 2)
        self.assertEqual(read_record(self.root)["diagnostics"], value["runtime"]["diagnostics"])
        plan = {"decision": "repair", "reason": "配置解析在第2行失败", "strategy": "修复已定位的 JSON 属性引号", "repair_tasks": [{
            "title": "修正配置格式", "description": "只修复属性键缺失的引号",
            "change_scope": [{"path": "/frontend/package.json", "operation": "modify"}],
        }]}
        model = EvidenceModel(responses=[AIMessage(content=json.dumps(plan, ensure_ascii=False))])
        backend = FilesystemBackend(root_dir=self.workspace, virtual_mode=True)
        agent = create_repair_planner_agent(model, self.workspace, user_skills_backend=backend, agent_memory_backend=backend)
        with patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(repair_planner=agent)), patch(
            "app.protocols.preview_runtime.execute_repair",
        ) as execute:
            prepared = await self.request("diagnose", attemptId=value["runtime"]["attemptId"])
        self.assertEqual(prepared["repair"]["status"], "awaiting_confirmation")
        self.assertEqual(prepared["repair"]["iteration"], 0)
        self.assertIn("JSON", prepared["repair"]["markdown"])
        self.assertIn("frontend/package.json:2:", prepared["repair"]["markdown"])
        self.assertIn('dev":', (self.root / "frontend/package.json").read_text())
        execute.assert_not_called()
        prompt = str(model.seen_messages[0][-1].content)
        evidence = json.loads(prompt.split("RepairPlannerInput:\n", 1)[1])["failure"]
        self.assertEqual(evidence["candidateFiles"], ["frontend/package.json"])
        self.assertIn('dev":', evidence["sourceContext"][0]["content"])
        self.assertEqual(evidence["diagnostics"][0]["location"], diagnostic["location"])

    async def test_diagnosis_tools_reject_runtime_history_but_allow_source(self) -> None:
        """真实工具调用不能把历史修复和自身状态当成根因，源码仍可按需读取。"""
        value = await self.request("restart")
        model = EvidenceModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "old-record", "name": "read_file", "args": {"file_path": "/.devagentstudio/runtime/launch/preview-runtime.json"}}]),
            AIMessage(content="", tool_calls=[{"id": "source", "name": "read_file", "args": {"file_path": "/frontend/package.json"}}]),
            AIMessage(content=json.dumps({"decision": "terminal_failure", "reason": "请提供启动命令所需的额外配置"}, ensure_ascii=False)),
        ])
        backend = FilesystemBackend(root_dir=self.workspace, virtual_mode=True)
        agent = create_repair_planner_agent(model, self.workspace, user_skills_backend=backend, agent_memory_backend=backend)
        with patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(repair_planner=agent)):
            result = await self.request("diagnose", attemptId=value["runtime"]["attemptId"])
        tools = [message for message in model.seen_messages[-1] if isinstance(message, ToolMessage)]
        self.assertEqual(tools[0].status, "error")
        self.assertIn("当前日志", tools[0].content)
        self.assertEqual(tools[1].status, "success")
        self.assertIn('dev":', tools[1].content)
        self.assertEqual(result["repair"]["status"], "failed")
        self.assertIn("额外配置", result["repair"]["message"])

    async def test_confirmed_plan_runs_actual_scoped_edit_and_revalidation(self) -> None:
        """确认后通过真实 SmallTask 文件工具修复，再由平台复验并重启。"""
        value = await self.request("restart")
        plan = {"decision": "repair", "strategy": "修复配置解析错误", "repair_tasks": [{
            "title": "修正引号", "description": "补齐解析器定位的属性引号",
            "change_scope": [{"path": "frontend/package.json", "operation": "modify"}],
        }]}
        with patch("app.agents.repair_planner.plan_build_failure_repair_with_repair_planner_agent", return_value=plan):
            prepared = await self.request("diagnose", attemptId=value["runtime"]["attemptId"])
        model = EvidenceModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "fix-source", "name": "edit_file", "args": {
                "file_path": "/frontend/package.json", "old_string": 'dev":', "new_string": '"dev":',
            }}]),
            AIMessage(content=json.dumps({"status": "completed", "summary": "已修正配置格式", "changedFiles": ["frontend/package.json"], "verification": []}, ensure_ascii=False)),
        ])
        backend = FilesystemBackend(root_dir=self.workspace, virtual_mode=True)
        small_task = create_small_task_agent(model, self.workspace, user_skills_backend=backend, agent_memory_backend=backend)

        def validate(*_args: object, **_kwargs: object) -> dict:
            """模拟平台构建前先真实解析修复后的文件。"""
            self.assertEqual(json.loads((self.root / "frontend/package.json").read_text())["scripts"]["dev"], "tool dev")
            return {"test_results": [{"id": "frontend_build", "passed": True}]}

        def launch(*_args: object, **_kwargs: object) -> dict:
            """将测试就绪进程事实交给真实状态投影，不启动外部服务。"""
            result = {"status": "running", "preview_url": "http://127.0.0.1:3000", "backend": {"status": "skipped"}, "frontend": {
                "status": "running", "preview_url": "http://127.0.0.1:3000", "server": {"pid": os.getpid(), "ready": True},
            }}
            (runtime_root(self.root) / "frontend.pid").write_text(str(os.getpid()))
            finish_attempt(self.root, result)
            return result

        with patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(small_task=small_task)), patch(
            "app.services.integration_test_runner.run_integration_checks", side_effect=validate,
        ) as checks, patch("app.services.project_launcher.launch_project_preview", side_effect=launch), patch(
            "app.services.preview_runtime_state._frontend_runtime_is_ready", return_value=True,
        ):
            result = await self.request("confirm", planId=prepared["repair"]["planId"])
        self.assertEqual(result["repair"]["status"], "completed")
        self.assertEqual(result["repair"]["iteration"], 1)
        self.assertEqual(result["runtime"]["diagnostics"], [])
        self.assertTrue(result["repair"]["codeChangeSets"])
        checks.assert_called_once()

    async def test_repeated_identical_queries_stop_without_consuming_repair_budget(self) -> None:
        """模型反复读取同一份未变化的文件时，诊断明确失败而不是无限运行。"""
        value = await self.request("restart")
        model = EvidenceModel(responses=[AIMessage(content="", tool_calls=[{
            "id": "same-read", "name": "read_file", "args": {"file_path": "/frontend/package.json"},
        }])])
        backend = FilesystemBackend(root_dir=self.workspace, virtual_mode=True)
        agent = create_repair_planner_agent(model, self.workspace, user_skills_backend=backend, agent_memory_backend=backend)
        with patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(repair_planner=agent)):
            result = await self.request("diagnose", attemptId=value["runtime"]["attemptId"])
        self.assertEqual(len(model.seen_messages), 2)
        self.assertEqual(result["status"], "failed")
        self.assertIn("未获得新证据", result["error"]["message"])
        self.assertEqual(read_record(self.workspace)["status"], "failed")
        self.assertIsNone(maintenance_owner(self.workspace))

    def test_failed_validation_replaces_old_launch_error_for_next_plan(self) -> None:
        """修复后出现新编译错误时，下一轮必须围绕新文件诊断。"""
        (self.root / "frontend/src/Next.tsx").write_text("broken source")
        record = {"attemptId": "current", "diagnostics": [{"stage": "old", "message": "old config error"}]}
        previous = {"attemptId": "current", "validation": {"test_results": [{
            "id": "frontend_build", "passed": False, "evidence": "new compile error",
            "execution": {"cwd": "frontend", "argv": ["tool", "build"], "returncode": 1,
                          "stderr_tail": "src/Next.tsx:1:2: syntax error"},
        }]}}
        evidence = repair_evidence(self.workspace, record, previous)
        self.assertEqual(evidence["evidenceSource"], "validation")
        self.assertEqual(evidence["candidateFiles"], ["frontend/src/Next.tsx"])
        self.assertNotIn("old config error", json.dumps(evidence["diagnostics"]))
        task = {"allowed_paths": ["frontend/src/Next.tsx"], "target_files": [], "failure_evidence": evidence}
        packet = build_small_task_packet(task, {"workspace": self.workspace, "repair_task_plan": {"strategy": "修复最新编译失败"}})
        self.assertEqual(packet["candidateFiles"], ["frontend/src/Next.tsx"])
        self.assertEqual(packet["repairStrategy"], "修复最新编译失败")
        # 如果用户已产生新启动尝试，旧验证不得覆盖新故障。
        self.assertEqual(repair_evidence(self.workspace, {**record, "attemptId": "new"}, previous)["evidenceSource"], "launch")

    def test_plan_prompt_is_evidence_first_without_filename_specific_repair(self) -> None:
        """定向诊断指令由流程来源触发，不把某个文件的修复答案写进提示。"""
        prompt = _build_failure_repair_prompt(repair_input={"source": "preview_startup", "failure": {}})
        self.assertIn("sourceContext", prompt)
        self.assertIn("Once evidence supports a bounded fix", prompt)
        self.assertIn("missing information", prompt)
        self.assertNotIn("package.json", prompt)
