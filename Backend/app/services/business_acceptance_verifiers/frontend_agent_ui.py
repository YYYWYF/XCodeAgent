"""共享 Agent 页面静态验收：当前磁盘来源、公开入口和唯一 Runtime 所有权。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

from app.services.agent_ui_dependency_plan import REPOSITORY, safe_path, target_snapshot
from app.services.agent_ui_design_project import command_environment
from app.services.business_acceptance_verifiers.common import verification_result
from app.services.workspace_process_registry import workspace_process_registry


def verify_agent_ui_runtime_source(
    files: dict[str, str], expected: dict[str, Any], *,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """重新投影正式来源后检查当前页面依赖图；不代替真实连接或业务结果验收。"""
    from app.services.agent_ui_build_contract import current_agent_ui_build_contract
    if workspace_root is None:
        return verification_result("blocked", "缺少当前工作区，无法复核共享 Agent UI 来源。")
    contract = expected.get("agent_ui")
    if not isinstance(contract, dict) or contract.get("mode") != "shared_host":
        return verification_result("blocked", "页面需要当前共享 Agent UI 构建合同。")
    try:
        root = Path(workspace_root)
        current = current_agent_ui_build_contract(root, str(contract.get("pageId") or ""))
        if contract != current:
            return verification_result("blocked", "Agent UI 正式来源、包、展示或接线已变化，请重新生成任务。")
        target = safe_path(root, "frontend")
        snapshot = target_snapshot(target)
        entries: list[str] = []
        for name, content in files.items():
            path = safe_path(root, name)
            if not name.startswith("frontend/src/") or path.read_text(encoding="utf-8") != content:
                return verification_result("blocked", "页面读取内容与当前磁盘不一致。")
            if path.suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
                entries.append(name)
        if not entries:
            return verification_result("blocked", "缺少当前页面源码。")
        sources: dict[str, str] = {}
        size = 0
        for name in snapshot:
            if not name.startswith("src/") or Path(name).suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
                continue
            path = safe_path(target, name)
            size += path.stat().st_size
            if len(sources) >= 1500 or size > 8_000_000 or path.stat().st_size > 512_000:
                return verification_result("blocked", "源码范围超过静态验收限制，请缩小工程。")
            sources["frontend/" + name] = path.read_text(encoding="utf-8")
        node = shutil.which("node")
        if not node:
            return verification_result("blocked", "共享页面分析需要已有 Node 与 TypeScript。")
        # 取消轮询会分段 communicate；大管道输入可能在第一次超时后停止写入。
        # 使用本次调用私有的临时文件交付完整快照，继续保留进程 owner 与取消机制。
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stream:
            json.dump({"files": sources, "entries": entries, "contract": current,
                       "toolRoot": str(REPOSITORY / "Frontend")}, stream)
            stream.seek(0)
            result = workspace_process_registry.run(
                [node, str(Path(__file__).parents[1] / "agent_ui_page_inspection.mjs")],
                workspace=root, run_id="agent-ui-build-inspection:" + uuid4().hex,
                cwd=REPOSITORY / "Frontend", env=command_environment(), stdin=stream,
                capture_output=True, text=True, timeout=30, check=False,
            )
        if result.returncode:
            return verification_result("blocked", "共享页面 TypeScript 分析未完成。")
        inspected = json.loads(result.stdout)
        # 只在同一磁盘快照上出具结果；构建命令和模型回执不参与本检查。
        if target_snapshot(target) != snapshot or current_agent_ui_build_contract(root, contract["pageId"]) != current:
            return verification_result("blocked", "来源或页面在验收期间变化，请重新检查。")
        errors = inspected["errors"]
        if errors:
            return verification_result("failed", "；".join(errors))
        return verification_result("passed", "已验证当前包/来源/ownership 和共享页面静态接入；真实连接与业务仍待验收。",
            facts={"verification_scope": "static_source", "real_connection": current["connection"],
                   "business_acceptance": "not_run", "runtime_behavior": "not_run",
                   "page_id": current["pageId"], "agent_id": current["agentId"],
                   "contract_sha256": current["sha256"], "reachable_files": inspected["files"]})
    except subprocess.TimeoutExpired:
        return verification_result("blocked", "共享页面 TypeScript 分析超时，请重试验收；本轮未记录通过。")
    except (OSError, ValueError, KeyError, TypeError):
        return verification_result("blocked", "当前共享来源或页面不可验证，请重新审阅设计转入及构建合同。")
