"""在公共 Build 执行阶段实现已确认的七模块任务，不参与 DAG 规划和确认。"""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from app.agents.agent_runtime.agent import create_agent_runtime_agent
from app.agents.tool_activity_stream import ToolActivityCallback, invoke_agent_with_tool_activity
from app.config import Settings
from app.services.agent_runtime_template_policy import load_agent_runtime_template_policy
from app.services.build_result_coordinator import create_agent_task_results
from app.services.runtime_agent_contracts import agent_contract_index, agent_endpoint_keys, agent_invocation
from app.services.runtime_agent_module_policy import module_source_refs, module_task_errors
from app.services.runtime_agent_editable_config import compile_agent_prompt


def agent_module_packet(plan: dict, task: dict, policy: dict) -> dict:
    """执行前从当前正式合同重算模块摘要和路径，拒绝过期或被改写的确认任务。"""

    if task.get("owner") != "agent" or task.get("task_type") != "agent.code":
        raise ValueError("Agent 模块执行器只接受正式 agent.code 任务。")
    expected = module_source_refs(plan, task, policy)
    if task.get("source_refs") != expected:
        raise ValueError("Agent 合同或模板路径策略已变化，请重新规划并确认任务。")
    errors = module_task_errors(task, policy)
    changes = task.get("change_scope") or []
    targets = task.get("target_files") or []
    if len(changes) != len(targets) or set(targets) != {item.get("path") for item in changes}:
        errors.append("Agent 模块目标文件与确认写范围不一致。")
    if errors:
        raise ValueError("；".join(errors))
    contract = agent_contract_index(plan)[expected["agent_id"]]
    module = expected["agent_module"]
    keys = set(agent_endpoint_keys(contract)) if module == "tools" else set()
    return {
        "task_id": task["id"], "agentId": contract["agentId"], "module": module,
        "identity": {key: contract[key] for key in ("name", "purpose")},
        "formalAgentIds": sorted(agent_contract_index(plan)),
        "configuration": deepcopy(contract["agentSettings"][module]),
        "resolvedSystemPrompt": compile_agent_prompt(contract["agentSettings"]["prompt"]),
        "description": task.get("description"), "target_files": targets,
        "change_scope": changes, "module_policy": policy["modulePaths"][module],
        "invocation": agent_invocation(contract["agentId"]),
        "tool_contracts": [{"id": api["id"], "schemas": api.get("schemas", {}),
                            "endpoints": [endpoint for endpoint in api.get("endpoints", ())
                                          if (api["id"], endpoint["id"]) in keys]}
                           for api in plan.get("api_contracts", ()) if any(key[0] == api["id"] for key in keys)],
    }


def generate_agent_modules_with_deep_agent(
    *, project_plan: dict[str, Any], build_task_plan: dict[str, Any], tasks: list[dict[str, Any]],
    workspace: str | None = None, selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """逐模块调用相同工作区模型、文件工具和严格结果协议，失败后不继续本批调用。"""

    del build_task_plan
    if not tasks:
        return []
    if not workspace:
        raise ValueError("Agent 模块执行必须绑定真实工作区。")
    policy = load_agent_runtime_template_policy(Path(workspace) / "agent-runtime")
    packets = [agent_module_packet(project_plan, task, policy) for task in tasks]
    results = []
    for index, (task, packet) in enumerate(zip(tasks, packets)):
        agent = create_agent_runtime_agent(
            workspace, tuple(packet["target_files"]), selected_skill_names, module_policy=packet["module_policy"],
        )
        prompt = (
            "Implement only the approved Agent module packet. Read module_policy.readPaths and the "
            "actual target files first. Reuse the existing DeepAgents factory, injected project model, "
            "Preserve existing implemented builders for other formalAgentIds. Register only actual implementations; "
            "do not generate placeholders for other Agents. Remove the template chat registration when formal business Agents exist. "
            "Use resolvedSystemPrompt exactly for the prompt module; it already composes persona, tone and constraints. "
            "For model.generation.temperature, use a local model.model_copy(update={\"temperature\": value}); "
            "otherwise pass the injected model unchanged. Never mutate the shared project model. "
            "trusted RuntimeContext, checkpoint and AG-UI lifecycle. Do not create a second model, "
            "server, workflow, database, user system or per-Agent wrapper. Memory must remain isolated "
            "by trusted Principal/thread. Tools import only the declared ApplicationService behind the "
            "exact Endpoint identity, never loopback REST; require confirmation for configured writes. "
            "Never trust client-supplied ownership. Disabled tools/skills/knowledge must actually remain "
            "disabled, including removal of stale wiring when required; enabled=false is not itself "
            "proof that existing code satisfies the contract. Modify only the exact target files; "
            "a path in module_policy is not extra write authorization. Never install dependencies, "
            "start services, run commands, change environment or planning artifacts. A contract gap "
            "must return a structured failure and change_request instead of an invented behavior.\n"
            "Return only {\"task_results\":[{\"task_id\":...,\"status\":...,\"summary\":...}]}. "
            "Use completed after actual writes; already_satisfied only with non-empty "
            "satisfaction_evidence describing inspected files and concrete findings; failed requires "
            "failure_category and failure_reason. A contract_mismatch or plan_mismatch also requires "
            "a concrete change_request. Do not return implementation_action, changed_files or commands.\n"
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
        note = invoke_agent_with_tool_activity(
            agent, {"messages": [{"role": "user", "content": prompt}]},
            workspace=workspace, on_tool_activity=on_tool_activity,
        )
        current = create_agent_task_results(
            [task], note, require_structured=True, strict_schema=True,
            executed_by={"agent": "agent-runtime-generation-agent", "mode": "live",
                         "model": Settings.from_env().model_name, "source": "agent_runtime_module",
                         "requiredSkillsLoaded": list(selected_skill_names or [])},
        )
        results.extend(current)
        if any(item.get("status") == "failed" for item in current):
            results.extend({"task_id": pending["id"], "owner": "agent", "status": "failed",
                            "failure_category": "dependency_failed", "failure_reason": "前序模块失败，未执行。"}
                           for pending in tasks[index + 1:])
            break
    return results
