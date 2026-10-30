"""Agent 运行接口的确定性准备、严格审阅和双文件提交，不调用模型。"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.domain.agent_endpoint_design import AgentEndpointDraft, AgentEndpointDesign
from app.services.runtime_endpoint_contracts import runtime_endpoint_capability_snapshot


def agent_endpoint_draft(plan: dict, contract_id: str, endpoint_id: str) -> dict:
    """从唯一正式能力编译只读接入基线；不能在详细设计里切换 Agent。"""
    snapshot = runtime_endpoint_capability_snapshot(plan, contract_id, endpoint_id)
    return AgentEndpointDraft.model_validate({
        "apiContractId": contract_id, "endpointId": endpoint_id, "implementationKind": "agent_runtime",
        "sourceBinding": {"sourceType": "agent", "agentId": snapshot["agentId"]}, "inputBindings": [],
        "invocation": snapshot["invocation"], "interactionContract": snapshot["interactionContract"],
        "capabilitySha256": snapshot["capabilitySha256"],
    }).model_dump(mode="json", by_alias=True)


def validate_agent_endpoint_draft(plan: dict, contract_id: str, endpoint_id: str, draft: dict) -> dict:
    """按当前正式来源逐字段复验，额外映射或派生编辑必须明确失败。"""
    parsed = AgentEndpointDraft.model_validate(draft).model_dump(mode="json", by_alias=True)
    expected = agent_endpoint_draft(plan, contract_id, endpoint_id)
    if parsed != expected:
        raise ValueError("运行接口的身份、Agent 绑定或能力快照已变化；请通过正式修订修改，重新审阅当前接入设计。")
    return parsed


def reuse_confirmed_agent_endpoint(workspace: str | Path, plan: dict, contract: dict,
                                  endpoint: dict, draft: dict, existing: dict | None) -> dict | None:
    """显式重复确认同一有效基线时返回原版本，不覆盖其他内容或生成无意义的新版本。"""
    from app.workspace.endpoint_design_documents import endpoint_design_paths

    validate_agent_endpoint_draft(plan, contract["id"], endpoint["id"], draft)
    if existing is None:
        return None
    try:
        validate_agent_endpoint_design(plan, contract, endpoint, existing)
    except ValueError:
        return None
    json_path, markdown_path = endpoint_design_paths(workspace, contract["id"], endpoint["id"])
    return {"status": "confirmed", "apiContractId": contract["id"], "endpointId": endpoint["id"],
            "design": existing, "reusedExisting": True,
            "artifacts": {"json_path": str(json_path), "markdown_path": str(markdown_path),
                          "sha256": sha256(json_path.read_bytes()).hexdigest()}}


def confirm_agent_endpoint(workspace: str | Path, plan: dict, contract: dict, endpoint: dict, draft: dict) -> dict:
    """仅在显式确认动作内提交当前候选，不推进 DAG 或调用运行接口。"""
    from app.workspace.endpoint_design_documents import (
        endpoint_design_paths, read_endpoint_design, technical_plan_sha256, write_endpoint_design,
    )
    clean = validate_agent_endpoint_draft(plan, contract["id"], endpoint["id"], draft)
    existing = read_endpoint_design(workspace, contract["id"], endpoint["id"], require_current=False)
    reused = reuse_confirmed_agent_endpoint(workspace, plan, contract, endpoint, clean, existing)
    if reused is not None:
        return reused
    json_path, markdown_path = endpoint_design_paths(workspace, contract["id"], endpoint["id"])
    if json_path.is_file() and markdown_path.is_file() and existing is None:
        # 不把外部编辑的 Markdown 静默丢弃再沿用 confirmed；用户先修复/正式修订后重审。
        raise ValueError("运行接口双文件不一致或 Markdown 已被编辑；请先处理当前文档，不能覆盖后直接确认。")
    design = AgentEndpointDesign.model_validate({**clean, "endpointContract": endpoint,
        "artifactRevision": uuid4().hex, "confirmedAt": datetime.now(UTC),
        "basedOn": [{"artifactKey": "technical-plan", "sha256": technical_plan_sha256(workspace)}]})
    paths = write_endpoint_design(workspace, design)
    saved = read_endpoint_design(workspace, contract["id"], endpoint["id"])
    if saved is None:
        raise ValueError("运行接口提交后无法读取一致版本。")
    return {"status": "confirmed", "apiContractId": contract["id"], "endpointId": endpoint["id"],
            "design": saved, "artifacts": paths}


def validate_agent_endpoint_design(plan: dict, contract: dict, endpoint: dict, design: dict) -> None:
    """开发和冻结入口重新校验正式快照，不能仅依赖 confirmed 标志。"""
    parsed = AgentEndpointDesign.model_validate(design)
    if parsed.endpoint_contract != endpoint:
        raise ValueError("运行接口的已确认 Endpoint 已变化，请重新审阅。")
    fields = set(AgentEndpointDraft.model_fields)
    draft = parsed.model_dump(mode="json", by_alias=True, include=fields)
    validate_agent_endpoint_draft(plan, contract["id"], endpoint["id"], draft)


def render_agent_endpoint_markdown(design: dict[str, Any]) -> str:
    """以可审阅 Markdown 展示接入约束，JSON 仅保留为内部提交状态。"""
    invocation = design["invocation"]
    return "\n".join([
        "# 智能体运行接口接入设计", "",
        f"- API Contract：`{design['apiContractId']}`", f"- Endpoint：`{design['endpointId']}`",
        f"- Agent：`{design['sourceBinding']['agentId']}`",
        f"- 调用：`{invocation['method']} {invocation['path']}`", "- 协议：AG-UI / SSE，官方客户端。",
        f"- 能力摘要：`{design['capabilitySha256']}`",
        "- 状态：" + ("已确认" if design.get("confirmationStatus") == "confirmed" else "草稿，尚未确认"),
        f"<!-- devagentstudio-artifact-revision: {design.get('artifactRevision', '')} -->", "",
        "## 公开能力", "", "- 通过标准 messages 提问，仅输出文本。",
        "- 不接受结构化业务入参，不公开内部业务状态或结构化成功结果。",
        "- 内部 Tool、Skill、Knowledge 按已确认七模块配置装配，不由文本输出形式禁用。",
        "- 允许的交互：" + ("、".join(design["interactionContract"]["interactionKinds"]) or "无；不接受未声明交互"),
        "- 写入工具在实际执行前继续要求明确确认；接口接入确认不替代业务确认。", "",
        "## 会话与失败", "", "- 按可信 Principal、Agent、thread 隔离；同一线程同时只执行一次。",
        "- 页面上下文仅作非可信业务说明，不能提供身份或权限。",
        "- busy 或容量拒绝不排队；断流或取消后未收到终态按结果未知处理，不自动重放。",
        "- 新会话必须由用户明确创建；只读状态同步不能补记旧运行成功。", "",
        "## 审阅范围", "", "- 本确认只签认当前公开合同和接入基线，不立即调用模型或启动开发。",
        "- 修改目标 Agent、公开能力、路径或身份须进入正式修订，重新确认新版。", "",
    ])
