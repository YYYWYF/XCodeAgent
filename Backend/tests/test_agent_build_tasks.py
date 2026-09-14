from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from app.services.agent_build_tasks import (
    build_agent_unit_candidate,
    compile_agent_build_tasks,
)
from app.services.agent_runtime_template_policy import AGENT_RUNTIME_MODULES
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.dag_planning_inputs import assemble_mainline_planning_inputs
from app.services.dag_planning_orchestrator import plan_dag_sequential
from app.services.frozen_contract_store import (
    FormalContractInput,
    PlanningFormalInputs,
)
from app.services.unit_generation_contracts import UnitGenerationPolicy
from app.services.unit_generation_requirements import resolve_generation_requirements


def _contract() -> dict:
    """构造七模块任务编译使用的正式 Agent Contract。"""

    return {
        "agentId": "inventory_assistant",
        "source": {"productPlanSha256": "sha256:" + "1" * 64},
        "identity": {"name": "库存助手", "purpose": "解释库存", "boundaries": []},
        "capabilities": [],
        "interaction": {"supportsMultiTurn": True},
        "agentSettings": {
            "prompt": {"systemPrompt": "解释库存", "persona": {}, "constraints": []},
            "model": {"selection": "project_default"},
            "memory": {"shortTerm": {"enabled": True, "store": "sqlite"}},
            "tools": {"enabled": False, "bindings": []},
            "skills": {"enabled": False, "bindings": []},
            "knowledge": {"enabled": False, "sources": []},
            "context": {"compression": {"strategy": "none"}},
        },
        "invocation": {"transport": "ag-ui-sse"},
        "runtime": {"framework": "DeepAgents"},
        "security": {"platformPromptMode": "locked_prefix"},
        "evaluation": {},
        "artifacts": {
            "compositionPath": "agent-runtime/src/app/agent/factory.py",
            "testRoot": "agent-runtime/tests",
        },
    }


def _write_workspace(root: Path) -> None:
    """写入任务编译依赖的 Runtime 入口和当前 TemplateState。"""

    runtime = root / "agent-runtime"
    files = (
        "src/app/agent/factory.py",
        "src/app/agent/context.py",
        "src/app/models/factory.py",
        "src/app/settings.py",
        "src/app/persistence/checkpointer.py",
        "src/app/tools/__init__.py",
        "src/app/interaction/schemas.py",
        "src/app/interaction/service.py",
    )
    for relative_path in files:
        target = runtime / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# test\n", encoding="utf-8")
    (runtime / "tests").mkdir()
    metadata = root / ".xcodeagent"
    metadata.mkdir()
    (metadata / "template-state.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "templateRevision": "2026.09.04.1",
                "requested": {},
                "effective": {},
                "appliedAdditions": {},
            }
        ),
        encoding="utf-8",
    )


class AgentBuildTasksTests(unittest.TestCase):
    """验证七模块任务由平台稳定编译而不是由规划模型生成。"""

    def test_compiles_seven_serial_agent_code_tasks(self) -> None:
        """单个 Agent 必须得到顺序稳定的七个 agent.code 任务。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root)
            tasks = compile_agent_build_tasks(
                [_contract()],
                unit_ids={"agent:inventory_assistant"},
                workspace=root,
            )

        self.assertEqual(
            [task["source_refs"]["agent_module"] for task in tasks],
            list(AGENT_RUNTIME_MODULES),
        )
        self.assertTrue(all(task["task_type"] == "agent.code" for task in tasks))
        self.assertEqual(tasks[0]["dependencies"], [])
        self.assertEqual(tasks[1]["dependencies"], [tasks[0]["id"]])
        self.assertTrue(
            all(
                path.startswith("agent-runtime/")
                for task in tasks
                for path in task["allowed_paths"]
            )
        )

    def test_builds_deterministic_candidate_for_agent_generation_requirements(self) -> None:
        """新 DAG 规划链必须复用七模块编译器生成 Agent Candidate。"""

        contract = _contract()
        plan = {
            "confirmation_status": "confirmed",
            "page_implementation_contracts": [],
            "api_contracts": [],
            "agent_contracts": [contract],
        }
        requirements = resolve_generation_requirements(
            required_unit_ids=["agent:runtime", "agent:inventory_assistant"],
            build_execution_scope={
                "type": "agent",
                "targetId": "inventory_assistant",
            },
            unit_skeleton=ensure_build_unit_skeleton(plan, {}),
            reuse_facts=ReuseFacts(
                retained_task_ids_by_unit={},
                reusable_capabilities_by_unit={},
                retained_endpoint_owners=[],
                external_capabilities=[],
                issues=[],
            ),
            formal_target=plan,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root)
            candidate = build_agent_unit_candidate(
                unit_id="agent:inventory_assistant",
                contracts=[contract],
                workspace=root,
                generation_requirements=requirements,
            )

        self.assertIsNotNone(candidate)
        tasks = candidate["tasks"] if candidate is not None else []
        self.assertEqual(len(tasks), len(AGENT_RUNTIME_MODULES))
        self.assertEqual(
            {
                capability
                for task in tasks
                for capability in task["provides_capabilities"]
            },
            {
                f"agent.inventory_assistant.{module_name}"
                for module_name in AGENT_RUNTIME_MODULES
            },
        )

    def test_agent_scope_completes_new_dag_planning_without_model_call(self) -> None:
        """Agent Scope 应通过新分 Unit 规划链生成七模块累计 DAG，且不调用模型。"""

        contract = _contract()
        plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "confirmed",
            "architecture": {"agentRuntime": "Python 3.12"},
            "page_implementation_contracts": [],
            "api_contracts": [],
            "agent_contracts": [contract],
        }
        scope = {"type": "agent", "targetId": "inventory_assistant"}
        required = ["agent:runtime", "agent:inventory_assistant"]
        skeleton = ensure_build_unit_skeleton(plan, {})
        reuse_facts = ReuseFacts(
            retained_task_ids_by_unit={},
            reusable_capabilities_by_unit={},
            retained_endpoint_owners=[],
            external_capabilities=[],
            issues=[],
        )
        formal_inputs = PlanningFormalInputs(
            product_plan=FormalContractInput(
                content={"agents": [{"agentId": "inventory_assistant"}]},
                source={"artifact": "product-plan"},
            ),
            technical_plan=FormalContractInput(
                content=plan,
                source={"artifact": "technical-plan"},
            ),
            page_contracts=[],
            api_contracts=[],
            endpoint_api_designs=[],
            authorization_slices=[],
        )
        inputs = assemble_mainline_planning_inputs(
            project_plan=plan,
            base_confirmed_plan=None,
            skeleton_plan=skeleton,
            build_context={"scope": scope, "required_unit_ids": required},
            build_execution_scope=scope,
            workspace_snapshot={"workspace_revision": "agent-snapshot"},
            reuse_facts=reuse_facts,
            formal_contract_inputs=formal_inputs,
            owner_session_id="agent-session",
            workflow_run_id="agent-workflow-run",
            thread_id="agent-thread",
        )
        policy = UnitGenerationPolicy(
            request_timeout=1,
            unit_session_timeout=2,
            model_turn_limit=1,
            frozen_contract_read_limits={
                "max_reads": 2,
                "max_total_bytes": 20_000,
                "max_bytes_per_read": 10_000,
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root)
            result = asyncio.run(
                plan_dag_sequential(
                    inputs.sequential_inputs(),
                    workspace_state={"workspace": str(root)},
                    planning_run_id="agent-planning-run",
                    workflow_run_id="agent-workflow-run",
                    thread_id="agent-thread",
                    policy=policy,
                )
            )

        registry = result.assembly.assembled_plan["task_registry"]
        self.assertEqual(len(registry), len(AGENT_RUNTIME_MODULES))
        self.assertTrue(
            all(task["owner"] == "agent" for task in registry.values())
        )

    def test_disabled_optional_modules_are_skipped_without_completing_others(self) -> None:
        """关闭的 Tools/Skills/Knowledge 必须单独跳过，其余模块仍保持 pending。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root)
            tasks = compile_agent_build_tasks(
                [_contract()],
                unit_ids={"agent:inventory_assistant"},
                workspace=root,
            )
        by_module = {task["source_refs"]["agent_module"]: task for task in tasks}
        self.assertEqual(by_module["tools"]["status"], "already_satisfied")
        self.assertEqual(by_module["skills"]["status"], "already_satisfied")
        self.assertEqual(by_module["knowledge"]["status"], "already_satisfied")
        self.assertEqual(by_module["prompt"]["status"], "pending")
        self.assertFalse((root / "agent-runtime/config").exists())
        self.assertTrue(
            all("template_policy_sha256" in task["source_refs"] for task in tasks)
        )
        self.assertTrue(
            all(
                task["source_refs"]["template_revision"] == "2026.09.04.1"
                for task in tasks
            )
        )

    def test_ignores_agents_outside_requested_units(self) -> None:
        """当前 Build 范围外的 Agent 不得产生七模块任务或额外配置文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root)
            tasks = compile_agent_build_tasks(
                [_contract()],
                unit_ids={"agent:another_agent"},
                workspace=root,
            )

        self.assertEqual(tasks, [])
        self.assertFalse((root / "agent-runtime/config").exists())


if __name__ == "__main__":
    unittest.main()
