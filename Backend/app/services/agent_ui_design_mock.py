"""T18 开发侧 Mock 的准入与身份绑定，不创建新的产品接口或确认动作。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.agent_ui_design_project import AgentUiDesignProject
from app.services.agent_ui_tool_host import AgentUiToolHostInspection, inspect_agent_ui_tool_host


@dataclass(frozen=True)
class AgentUiDesignMock:
    """显式选择官方开发场景，不能通过页面 URL 切换到真实 Runtime。"""

    inspection: AgentUiToolHostInspection
    page_id: str
    revision: str
    scenario_id: str = "simple-chat"


def design_mock_options(project: AgentUiDesignProject, mock: AgentUiDesignMock,
                        node: Path, run_id: str) -> dict[str, Any]:
    """启动前复验工具与当前生成记录；页面、修订或工作区不匹配时直接拒绝。"""
    inspection = mock.inspection
    if inspection.workspace != project.workspace or inspection.node_executable != node:
        raise ValueError("设计 Mock 必须使用当前工作区和同一受检 Node。")
    current = inspect_agent_ui_tool_host(workspace=project.workspace,
        installation_root=inspection.installation_root,
        node_executable=node, creator_version=inspection.creator_version, run_id=run_id)
    if current != inspection:
        raise ValueError("设计 Mock 工具身份已变化。")
    record = project.root / "generation.json"
    if record.is_symlink() or not record.is_file():
        raise ValueError("设计 Mock 仅服务当前成功生成的多文件原型。")
    evidence = json.loads(record.read_text(encoding="utf-8"))
    if (evidence.get("status") != "built" or evidence.get("revision") != mock.revision
            or evidence.get("sourceDigest") != project.source_digest
            or evidence.get("buildDigest") != project.build_digest
            or mock.page_id not in {page.get("pageId") for page in evidence.get("pages", [])}):
        raise ValueError("设计 Mock 页面、修订或构建身份不匹配。")
    return {"installationRoot": str(current.installation_root), "creatorVersion": current.creator_version,
            "packageJsonSha256": current.package_json_sha256, "scenarioId": mock.scenario_id,
            "binding": {"pageId": mock.page_id, "revision": mock.revision,
                        "sourceDigest": project.source_digest, "buildDigest": project.build_digest}}
