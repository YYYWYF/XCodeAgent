"""从已确认正式输入交付模板拥有的执行策略，模型任务不能写入该文件。"""
from __future__ import annotations
import ast
from hashlib import sha256
import json
import os
from pathlib import Path

from app.services.runtime_agent_contracts import agent_contract_index, agent_fact_digest
from app.services.runtime_agent_implementation import agent_implementation_facts
from app.services.runtime_endpoint_contracts import agent_execution_capability_snapshot

POLICY_PATH = "agent-runtime/src/app/agent/execution_policy.py"
BEGIN = "# BEGIN PLATFORM EXECUTION POLICIES\n"
END = "# END PLATFORM EXECUTION POLICIES"
RULE = "execution.policy.v2"
BUDGETS = {"requestBytes": 262144, "messages": 128, "userTextBytes": 16384, "textBytes": 131072,
           "threadRuns": 1, "identityRuns": 2, "processRuns": 4, "runSeconds": 120, "cleanupSeconds": 10}


def execution_capabilities(agent: dict) -> dict:
    """把公开文本会话与内部七模块能力分开，工具循环有界但不被全局禁止。"""
    from app.services.runtime_agent_resources import agent_resource_files
    settings = agent["agentSettings"]
    tools = sorted(binding["toolId"] for binding in settings["tools"]["bindings"])
    resources = {module: agent_resource_files(module, settings[module]) for module in ("skills", "knowledge")}
    if resources["skills"] and "read_file" in tools:
        raise ValueError("业务 Tool 不能占用技能文档读取工具的 read_file 名称。")
    return {"toolIds": tools, "resourceFiles": resources,
        "modelRequests": 8 if tools or resources["skills"] else 1}


def resource_digests(root: Path, capabilities: dict) -> dict:
    """精确资源内容也属于执行输入，拒绝越界、链接和过大的文档集合。"""
    result, total = {}, 0
    for paths in capabilities["resourceFiles"].values():
        for relative in paths:
            path = root
            for part in Path(relative).parts:
                path /= part
                if path.is_symlink():
                    raise ValueError("Agent 正式资源不能经过符号链接。")
            if not path.is_file() or path.stat().st_size > 65536:
                raise ValueError(f"Agent 正式资源缺失或超过 64 KiB：{relative}。")
            content = path.read_bytes()
            total += len(content)
            if total > 131072:
                raise ValueError("Agent 技能和知识资源累计不能超过 128 KiB。")
            result[relative] = sha256(content).hexdigest()
    return result


def compile_execution_policies(root: Path, plan: dict) -> dict:
    """只为实际注册、静态依赖可追踪且公开能力合法的正式 Agent 编译策略。"""
    contracts = agent_contract_index(plan)
    implementations = agent_implementation_facts(root, set(contracts))
    result = {}
    for agent_id, implementation in sorted(implementations.items()):
        capabilities = execution_capabilities(contracts[agent_id])
        result[agent_id] = {"agentId": agent_id, "rule": RULE,
            **agent_execution_capability_snapshot(plan, agent_id), **implementation,
            "executionCapabilities": capabilities, "resourceSha256": resource_digests(root, capabilities),
            "budgets": dict(BUDGETS)}
    return result


def execution_policy_operation(plan: dict, scope: dict) -> dict | None:
    """Pending 预先声明精确平台写路径和当前来源，确认后只能执行这项确定性装配。"""
    if not agent_contract_index(plan):
        return None
    return {"path": POLICY_PATH, "rule": RULE, "sourceSha256": agent_fact_digest(plan["agent_contracts"]),
            "scope": dict(scope), "budgets": dict(BUDGETS)}


def policy_content(root: Path, plan: dict) -> bytes:
    """保留模板加载器原始字节，只替换单个明确平台区域；缺失模板直接失败。"""
    path = root / POLICY_PATH
    if path.is_symlink() or not path.is_file():
        raise ValueError("缺少当前 Runtime execution_policy.py 模板，请先同步模板。")
    content = path.read_text()
    if content.count(BEGIN) != 1 or content.count(END) != 1:
        raise ValueError("Runtime 执行策略平台区域无效。")
    prefix, rest = content.split(BEGIN)
    _, suffix = rest.split(END)
    # JSON 作为 Python 字符串常量存储，避免 null/false 的语言差异或代码注入。
    encoded = json.dumps(compile_execution_policies(root, plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (prefix + BEGIN + "POLICIES_JSON = " + repr(encoded) + "\n" + END + suffix).encode()


def publish_execution_policies(root: Path, plan: dict, operation: dict | None, scope: dict) -> dict:
    """在已确认 Build 的共享资源锁内一次提交当前有效条目，保留无关已实现 Agent。"""
    expected = execution_policy_operation(plan, scope)
    if operation != expected:
        raise ValueError("执行策略装配与已确认 Pending 来源不一致，请重新规划确认。")
    if expected is None:
        return {"status": "not_required"}
    path, content = root / POLICY_PATH, policy_content(root, plan)
    temporary = path.with_suffix(".py.platform-tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "applied", "path": POLICY_PATH, "sha256": sha256(content).hexdigest()}


def _startup_capabilities(content: bytes) -> dict:
    """只读平台策略常量，源码摘要和符号清单作为构建记录，不参与启动准入。"""
    try:
        region = content.decode().split(BEGIN, 1)[1].split(END, 1)[0]
        statements = ast.parse(region).body
        if (len(statements) != 1 or not isinstance(statements[0], ast.Assign)
                or len(statements[0].targets) != 1 or not isinstance(statements[0].targets[0], ast.Name)
                or statements[0].targets[0].id != "POLICIES_JSON"):
            raise ValueError("平台区域必须是唯一 POLICIES_JSON 常量。")
        entries = json.loads(ast.literal_eval(statements[0].value))
        if not isinstance(entries, dict) or any(not isinstance(entry, dict) for entry in entries.values()):
            raise ValueError("执行策略必须为 Agent 策略对象集合。")
    except (IndexError, UnicodeDecodeError, SyntaxError, TypeError, ValueError) as exc:
        raise ValueError("Runtime 执行策略平台常量无效。") from exc
    return {agent_id: {key: value for key, value in entry.items()
                       if key not in {"implementationSha256", "symbols"}}
            for agent_id, entry in entries.items()}


def verify_execution_policy(root: Path, plan: dict) -> str:
    """启动校验当前正式能力，允许修改实现源码；交付文件摘要仍绑定本次启动。"""
    expected = policy_content(root, plan)
    actual = (root / POLICY_PATH).read_bytes()
    if _startup_capabilities(actual) != _startup_capabilities(expected):
        raise ValueError("Runtime 执行策略与当前正式能力不一致，请重新构建确认。")
    # Runtime 加载器校验的是磁盘上实际交付文件，不能传入含新源码摘要的重编译文件哈希。
    return sha256(actual).hexdigest()
