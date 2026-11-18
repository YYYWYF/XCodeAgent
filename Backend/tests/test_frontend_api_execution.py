"""覆盖单一 API 新建任务的真实 Deep Agent 工具、AST 和 Build 验收。"""

import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deepagents.backends import StateBackend
from langchain_core.messages import AIMessage
from langsmith import tracing_context

from app.agents.frontend.agent import create_frontend_agent
from app.agents.frontend.generator import generate_frontend_with_deep_agent
from app.agents.frontend.task_packet import SERVICE
from app.graph.subgraphs.build import _execute_owner_tasks, _execute_ready_tasks
from app.services.business_acceptance import compile_business_acceptance
from app.services.build_scheduler import verify_task_file_changes
from tests.test_build_observability import ToolCapableTestModel
from tests.test_business_acceptance import _formal_context, _task
from tests.test_frontend_build_execution import _create_file

TARGET = "frontend/src/apis/ordersApi.ts"
VALID_SOURCE = """
import service from './service';
type OrderRequest = { status: string };
type OrderResponse = { id: string };
export function listOrders(params: OrderRequest): Promise<OrderResponse> {
    return service.get<OrderResponse>('/orders', { params });
}
"""
ENV = {
    "DEVAGENTSTUDIO_DAG_BUSINESS_SELF_CHECK_ENABLED": "false",
}


def _fixture(root: Path) -> tuple[dict, dict]:
    """创建干净模板与由正式编译器生成的单任务契约。"""
    context = _formal_context()
    plan = context["project_plan"]
    plan["frontend_pages"] = []
    task = _task("frontend.api_module", owner="frontend", unit_id="frontend:api-client",
                 path=TARGET, target_id="orders.list")
    task.update(target_files=[TARGET], allowed_paths=[TARGET],
                change_scope=[{"path": TARGET, "operation": "add"}])
    task["source_refs"]["api_contract_ids"] = ["orders-api"]
    _create_file(root, SERVICE, """class Service {
      get<T>(url: string, config: object): Promise<T> { throw new Error(); }
    }
    const service = new Service();
export default service;
""")
    return compile_business_acceptance([task], context)[0], {
        "project_plan": plan, "build_task_plan": {}, "workspace": str(root),
    }


def _tool(name: str, call_id: str, **args) -> dict:
    """构造脚本模型触发的真实工具调用。"""
    return {"name": name, "id": call_id, "args": args}


class RecordingModel(ToolCapableTestModel):
    """记录真实模型调用次数及最终绑定的工具集合。"""

    turns: int = 0
    tool_names: list[str] = []

    def bind_tools(self, tools, **kwargs):
        """保存模型实际收到的工具规格，而非仅检查 scope 的工具工厂。"""
        self.tool_names = [item.name for item in tools]
        return self

    def _generate(self, *args, **kwargs):
        """独立累计轮次，避免脚本模型响应游标循环造成误判。"""
        self.turns += 1
        return super()._generate(*args, **kwargs)


class FrontendApiExecutionTests(unittest.TestCase):
    """检查无步骤 API 执行、真实权限和平台验收后的最终裁决。"""

    def setUp(self):
        """创建独立工作区，关闭可选通用自检以验证 API 验收仍执行。"""
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, ENV).start()
        self.enterContext(tracing_context(enabled=False))
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.task, self.state = _fixture(self.root)
        self.trace = Mock()
        trace_context = patch("app.agents.frontend.generator.frontend_agent_trace").start()
        trace_context.return_value.__enter__.return_value = self.trace

    def _run(self, *calls, after_agent=None, status="completed"):
        """通过生产 Build 入口运行真实文件工具，模型仅提供固定编码回合。"""
        responses = [AIMessage(content="", tool_calls=list(calls))] if calls else []
        responses.append(AIMessage(content=json.dumps({"task_results": [{
            "task_id": self.task["id"], "status": status, "summary": "编码结束",
        }]})))
        model = RecordingModel(responses=responses)

        def factory(scope):
            """确认首个模型调用前已启用任务授权，工具仍由生产 Agent 装配。"""
            self.assertEqual(scope.step, 0)
            self.assertTrue(scope.active)
            self.assertEqual(scope.current["id"], self.task["id"])
            self.assertEqual(scope.control_tools(), [])
            self.scope = scope
            agent = create_frontend_agent(model, str(self.root), user_skills_backend=StateBackend(),
                                          agent_memory_backend=StateBackend(), build_scope=scope)
            return agent

        from app.agents.frontend import generator
        invoke = generator._invoke_live_frontend_agent

        def invoke_with_change(**kwargs):
            """在 Agent 确认退出后模拟正式产物或文件在核验前变化。"""
            note = invoke(**kwargs)
            if after_agent:
                after_agent()
            return note

        bundle = SimpleNamespace(frontend_build_factory=factory)
        with (patch("app.agents.create_agent_bundle", return_value=bundle),
              patch.object(generator, "_invoke_live_frontend_agent", side_effect=invoke_with_change) as run):
            results, changes = _execute_owner_tasks(self.state, "frontend", [self.task])
        self.assertEqual(run.call_count, 1)
        self.assertIsNone(self.scope.current)
        for name in ("begin_frontend_task", "advance_frontend_step", "complete_frontend_task"):
            self.assertNotIn(name, model.tool_names)
        return results[0], changes, model.turns

    def test_real_tools_then_two_acceptance_boundaries(self):
        """首轮无需启动工具即可读 Skill、创建文件，并通过双层平台验收。"""
        with patch("app.graph.subgraphs.build.verify_task_file_changes", wraps=verify_task_file_changes) as engineering:
            result, changes, model_calls = self._run(
                _tool("read_file", "skill",
                      file_path="/.devagentstudio/builtin-skills/frontend-engineering-standards/SKILL.md"),
                _tool("write_file", "denied", file_path="/frontend/src/apis/other.ts", content=VALID_SOURCE),
                _tool("write_file", "created", file_path=f"/{TARGET}", content=VALID_SOURCE),
            )
        self.assertEqual(engineering.call_count, 1)
        self.assertTrue(engineering.call_args.kwargs["authorization_checks_only"])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["executed_by"]["source"], "frontend_deep_agent")
        self.assertEqual(result["changed_files"], [TARGET])
        self.assertEqual(result["acceptance_status"], {"engineering": "skipped", "business": "passed"})
        self.assertEqual(changes["files"][0]["changeType"], "added")
        self.assertEqual(model_calls, 2)
        events = [(call.args[0], call.kwargs) for call in self.trace.frontend_event.call_args_list]
        transitions = [event for name, event in events if name == "frontend_step_transition"]
        self.assertEqual(transitions, [])
        permissions = {event["virtual_path"]: event["permission_decision"] for name, event in events
                       if name == "frontend_tool_permission"}
        self.assertEqual(permissions["/frontend/src/apis/other.ts"], "denied")
        self.assertEqual(permissions["/.devagentstudio/builtin-skills/frontend-engineering-standards/SKILL.md"],
                         "allowed")
        self.assertFalse((self.root / "frontend/src/apis/other.ts").exists())

    def test_invalid_ast_blocks_completion(self):
        """不支持的 AST 语法保留 blocked 证据及真实变更。"""
        result, changes, _ = self._run(_tool("write_file", "created", file_path=f"/{TARGET}",
                                             content="export function malformed( {"))
        self.assertEqual(result["failure_category"], "business_acceptance_blocked")
        self.assertEqual(result["business_acceptance_evidence"][0]["facts"]["reason_code"],
                         "verifier_unsupported_syntax")
        self.assertEqual(changes["files"][0]["changeType"], "added")

    def test_written_file_must_pass_contract(self):
        """真实写入错误路径的模块仍进入既有 Repair。"""
        result, changes, _ = self._run(_tool("write_file", "created", file_path=f"/{TARGET}",
                                             content=VALID_SOURCE.replace("'/orders'", "'/wrong'")))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_category"], "business_acceptance_failed")
        self.assertEqual(result["scheduler_decision"]["action"], "repair")
        self.assertEqual(result["business_acceptance_evidence"][0]["status"], "failed")
        self.assertEqual(changes["files"][0]["changeType"], "added")

    def test_service_name_without_template_binding_is_rejected(self):
        """同名对象、错误模块和类型导入均不足以证明统一请求路径。"""
        for declaration in ("const service = { get() {} };",
                            "import service from 'other-service';",
                            "import type service from './service';"):
            with self.subTest(declaration=declaration), TemporaryDirectory() as directory:
                self.root = Path(directory)
                self.task, self.state = _fixture(self.root)
                source = VALID_SOURCE.replace("import service from './service';", declaration)
                result, _, _ = self._run(_tool("write_file", "created", file_path=f"/{TARGET}", content=source))
                self.assertEqual(result["failure_category"], "business_acceptance_failed")

    def test_service_default_import_alias_is_accepted(self):
        """端点匹配依据真实默认导入绑定，允许合法 service 别名。"""
        source = VALID_SOURCE.replace("import service", "import transport").replace("service.get", "transport.get")
        result, _, _ = self._run(_tool("write_file", "created", file_path=f"/{TARGET}", content=source))
        self.assertEqual(result["status"], "completed", result)

    def test_formal_hash_change_after_write_is_blocked(self):
        """工具成功之后出现正式契约变化，原 verifier 阻断收敛。"""
        def change_plan():
            """改变原 Endpoint 的正式哈希。"""
            self.state["project_plan"]["api_contracts"][0]["endpoints"][0]["path"] = "/changed"

        result, _, _ = self._run(_tool("write_file", "created", file_path=f"/{TARGET}", content=VALID_SOURCE),
                                  after_agent=change_plan)
        self.assertEqual(result["failure_category"], "business_acceptance_blocked")
        self.assertEqual(result["business_acceptance_evidence"][0]["facts"]["reason_code"],
                         "formal_source_changed")

    def test_no_write_cannot_complete(self):
        """只返回 completed 而没有文件，平台拒绝完成。"""
        result, _, _ = self._run()
        self.assertEqual(result["failure_category"], "acceptance_verification_failed")

    def test_final_engineering_rejects_missing_diff(self):
        """新建候选即使磁盘内容合规，外层差异缺失仍被平台拒绝。"""
        with patch("app.graph.subgraphs.build._filter_change_set_for_tasks", return_value=None):
            result, _, _ = self._run(_tool("write_file", "created", file_path=f"/{TARGET}", content=VALID_SOURCE))
        self.assertEqual(result["failure_category"], "acceptance_verification_failed")
        self.assertEqual(result["acceptance_status"]["engineering"], "failed")

    def test_existing_target_modify_and_retry_are_task_level(self):
        """已有模块和修复任务使用同样的任务权限与结构化结果。"""
        _create_file(self.root, TARGET, VALID_SOURCE.replace("'/orders'", "'/wrong'"))
        self.task["change_scope"][0]["operation"] = "modify"
        self.task["kind"] = "repair"
        self.task["description"] = "修复错误路径"
        result, _, _ = self._run(_tool("edit_file", "edited", file_path=f"/{TARGET}",
                                       old_string="'/wrong'", new_string="'/orders'"))
        self.assertEqual(result["status"], "completed", result)

    def test_missing_business_checks_cannot_complete(self):
        """任务缺少 API 契约检查时仍被平台阻断，不能通过空集合完成。"""
        self.task["business_acceptance_checks"] = []
        result, _, _ = self._run(_tool("write_file", "created", file_path=f"/{TARGET}", content=VALID_SOURCE))
        self.assertEqual(result["failure_category"], "business_acceptance_blocked")

    def test_already_satisfied_still_requires_contract_acceptance(self):
        """已有合规文件允许无修改完成，已有错误实现不能借 already_satisfied 绕过验收。"""
        for correct in (False, True):
            with self.subTest(correct=correct):
                _create_file(self.root, TARGET, VALID_SOURCE if correct else VALID_SOURCE.replace("'/orders'", "'/wrong'"))
                result, _, _ = self._run(status="already_satisfied")
                self.assertEqual(result["status"], "already_satisfied" if correct else "failed")

    def test_parallel_ready_batch_uses_same_dispatch(self):
        """并行调度仍按原顺序拆成独立任务，不再区分新旧 API 分支。"""
        second = {**self.task, "id": "second", "owner": "backend"}
        with patch("app.graph.subgraphs.build._execute_task", return_value=([], None)) as execute:
            _execute_ready_tasks(self.state, [self.task, second])
        self.assertEqual(execute.call_count, 2)
