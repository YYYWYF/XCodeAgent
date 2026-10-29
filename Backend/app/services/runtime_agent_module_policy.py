"""七模块路径、来源与增量摘要；不拥有调度、确认或恢复状态机。"""

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from app.services.agent_runtime_template_policy import AGENT_RUNTIME_MODULES
from app.services.runtime_agent_contracts import agent_contract_index, agent_endpoint_keys, agent_fact_digest
from app.services.planning_frozen import plain_json


def module_path_allowed(path: str, policy: Mapping[str, Any]) -> bool:
    """只授权模块固定文件或新增/测试根内的普通源码及资源，拒绝脚本、SQL和隐藏路径。"""

    if not isinstance(path, str) or "\\" in path or "\x00" in path:
        return False
    parts = PurePosixPath(path).parts
    if str(PurePosixPath(path)) != path or any(
        part in {"..", "."} or part.startswith(".") or any(char in part for char in "*?[]") for part in parts
    ) or not path.startswith("agent-runtime/"):
        return False
    if not path.endswith((".py", ".md", ".txt", ".json", ".yaml", ".yml")):
        return False
    if path in {f"agent-runtime/{item}" for item in policy.get("modifyPaths", ())}:
        return True
    return any(path.startswith(f"agent-runtime/{root}/")
               for field in ("addRoots", "testRoots") for root in policy.get(field, ()))


def module_source_refs(plan: Mapping, task: Mapping, template_policy: Mapping) -> dict:
    """从固定任务身份编译来源，模型不能自报另一模块或另一个 Agent 的合同。"""

    unit_id = task.get("unit_id", "")
    agent_id = unit_id.removeprefix("agent:")
    module = str(task.get("id", "")).removeprefix(f"{unit_id}::")
    contracts = agent_contract_index(plan)
    if not unit_id.startswith("agent:") or agent_id not in contracts or module not in AGENT_RUNTIME_MODULES:
        raise ValueError("Agent 任务必须使用正式 Agent/模块固定身份。")
    paths = template_policy.get("modulePaths", {})
    if module not in paths or not template_policy.get("policySha256"):
        raise ValueError("Agent DAG 缺少已验证的模板路径策略。")
    facts = agent_module_facts(plan, agent_id, module)
    return {"type": "agent_module", "agent_id": agent_id, "agent_module": module,
            "module_input_sha256": agent_fact_digest(facts),
            "template_policy_sha256": template_policy["policySha256"]}


def agent_module_facts(plan: Mapping, agent_id: str, module: str) -> dict:
    """为规划、Build 验收和缓存共用同一模块正式输入，不引入第二份配置权威。"""

    contracts = agent_contract_index(plan)
    if agent_id not in contracts or module not in AGENT_RUNTIME_MODULES:
        raise ValueError("Agent 验收缺少当前正式 Agent 或模块身份。")
    contract = contracts[agent_id]
    topology = plan.get("topology") or {}
    facts = {"agentId": agent_id, "module": module, "configuration": contract["agentSettings"][module],
             "factoryAgentIds": sorted(contracts),
             "topology": {key: plain_json(topology.get(key)) for key in
                          ("type", "serviceIds", "publicEdgeServiceId", "authenticationTermination")}}
    if module in {"prompt", "context"}:
        facts["identity"] = {key: contract[key] for key in ("name", "purpose", "pageIds")}
    if module in {"tools", "knowledge"}:
        if module == "knowledge":
            facts["toolsConfiguration"] = plain_json(contract["agentSettings"]["tools"])
        keys = set(agent_endpoint_keys(contract))
        facts["endpoints"] = [{"contract": api["id"], "endpoint": plain_json(endpoint), "schemas": plain_json(api.get("schemas", {}))}
                              for api in plan.get("api_contracts", ()) for endpoint in api.get("endpoints", ())
                              if (api["id"], endpoint["id"]) in keys]
    return facts


def module_task_errors(task: Mapping, policy: Mapping) -> list[str]:
    """检查候选目标、允许路径和串行策略，不能通过声明更大目录突破模板策略。"""

    module = str(task.get("id", "")).rsplit("::", 1)[-1]
    paths = policy.get("modulePaths", {}).get(module)
    if not isinstance(paths, Mapping):
        return ["Agent 模块缺少平台模板路径策略。"]
    targets = task.get("target_files", ())
    if not isinstance(targets, (list, tuple)) or not targets or any(not module_path_allowed(path, paths) for path in targets):
        return ["Agent 目标文件越过当前模块模板路径策略。"]
    allowed = task.get("allowed_paths", ())
    # Model Candidate 必须声明精确文件，新增根只由平台策略持有，模型不能授权整个目录。
    if (not isinstance(allowed, (list, tuple)) or any(not isinstance(path, str) for path in allowed)
            or set(allowed) != set(targets) or task.get("can_run_in_parallel") is not False):
        return ["Agent 模块必须使用精确文件授权并保持串行执行。"]
    changes = task.get("change_scope", ())
    if not isinstance(changes, (list, tuple)) or any(
        not isinstance(item, Mapping) or item.get("operation") not in {"add", "modify"} for item in changes
    ):
        return ["Agent 模块不允许删除模板文件。"]
    deliverables = task.get("deliverables")
    if not isinstance(deliverables, (list, tuple)) or len(deliverables) != 1 or not isinstance(deliverables[0], Mapping):
        return ["Agent 模块必须声明唯一交付物。"]
    paths = deliverables[0].get("paths")
    if not isinstance(paths, (list, tuple)) or any(not isinstance(path, str) for path in paths) or set(paths) != set(targets):
        return ["Agent 模块交付物必须覆盖其精确目标文件集合。"]
    return []
