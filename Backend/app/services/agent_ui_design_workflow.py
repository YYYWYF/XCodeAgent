"""T19 将包消费生产者接入现有后台生成池，不确认正式设计。"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
import uuid

from app.services.agent_ui_design_generator import generate_agent_ui_design
from app.services.agent_ui_design_mock import AgentUiDesignMock
from app.services.agent_ui_design_preview import agent_ui_design_previews
from app.services.agent_ui_design_project import REPOSITORY
from app.services.agent_ui_tool_host import AgentUiToolHostInspection, inspect_agent_ui_tool_host
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_generator import UiDesignStreamCancelled, derive_page_key
from app.services.ui_design_manifest import build_ui_page_manifest
from app.services.ui_design_interactions import agent_project_page_bindings
from app.services.agent_ui_design_sources import retained_page_sources, retained_presentations, retained_agent_sources
from app.services.agent_ui_tool_environment import resolve_design_tools

if TYPE_CHECKING:
    from app.services.ui_design_generation_pool import UiDesignGenerationTask


def design_environment(workspace: str, run_id: str = "") -> tuple[AgentUiToolHostInspection, Path]:
    """从项目默认工具链解析运行环境，不接受页面或模型指定工具和配置权限。"""
    node, cli = resolve_design_tools(Path(workspace).resolve(), REPOSITORY / "Frontend/package.json", run_id)
    installation = Path(os.environ.get("DEVAGENTSTUDIO_AGENT_UI_TOOL_ROOT", str(REPOSITORY / "tools/agent-ui")))
    manifest = json.loads((REPOSITORY / "vendor/agent-ui/manifest.json").read_text(encoding="utf-8"))
    version = next(item["version"] for item in manifest["packages"]
                   if item["name"] == "@za38.03/agent-ui-creator")
    inspection = inspect_agent_ui_tool_host(workspace=workspace, installation_root=installation,
        node_executable=node, creator_version=version, run_id=run_id)
    return inspection, cli


def group_agent_tasks(tasks: list[UiDesignGenerationTask]) -> list[UiDesignGenerationTask]:
    """只合并本次明确请求的 Agent 页面，共享工程不扩大页面生成范围。"""
    grouped: list[UiDesignGenerationTask] = []
    seen: set[str] = set()
    for task in tasks:
        if not task.spec_page.get("agent_surfaces") or not task.product_plan:
            grouped.append(task)
            continue
        if task.workspace in seen:
            continue
        seen.add(task.workspace)
        supplied = {item.page_id: item for item in tasks if item.workspace == task.workspace
                    and item.spec_page.get("agent_surfaces")}
        members = list(supplied.values())
        grouped.append(replace(task, members=tuple(members), job_id=f"agent-ui-design-{uuid.uuid4().hex}"))
    return grouped


def generate_agent_project(task: UiDesignGenerationTask,
                           should_cancel: Callable[[], bool] | None) -> dict[str, Any]:
    """生成一组共享页面并启动官方模拟预览；成功仍是 ready，不能自动确认。"""
    if task.action != "regenerate" or task.template_id:
        raise ValueError("Agent UI 使用官方包默认展示，请使用生成入口；普通页面模板不适用于该工程。")
    if not task.members:
        raise ValueError("多文件 Agent UI 生成缺少当前已联合确认的产品事实。")
    inspection, cli = design_environment(task.workspace, task.job_id)
    pages = project_agent_design_pages(task.requirement_spec, task.product_plan)
    retained = retained_page_sources(task, pages)
    presentations = retained_presentations(task)
    agent_sources = retained_agent_sources(task, pages)
    draft = Path(task.workspace) / ".devagentstudio/ui-design/drafts" / uuid.uuid4().hex
    design = generate_agent_ui_design(inspection, draft_root=draft, pnpm_cli=cli,
        requirement_spec=task.requirement_spec, product_plan=task.product_plan,
        run_id=task.job_id, should_cancel=should_cancel, retained_sources=retained, presentations=presentations,
        retained_agent_sources=agent_sources, target_page_ids=[member.page_id for member in task.members])
    if should_cancel and should_cancel():
        raise UiDesignStreamCancelled("设计生成已取消。")
    asset = {"kind": "agent-ui-project", "draft": draft.name, "revision": design.revision,
             "factsDigest": design.facts_digest, "environmentIdentity": design.project.identity,
             "sourceDigest": design.project.source_digest, "buildDigest": design.project.build_digest,
             "pageIds": [page["pageId"] for page in pages]}
    entries: list[dict[str, Any]] = []
    supplied = {member.page_id: member for member in task.members}
    used = {member.page_key for member in task.members}
    for page in pages:
        member = supplied.get(page["pageId"])
        key = member.page_key if member else derive_page_key(page, used)
        used.add(key)
        entry = build_ui_page_manifest(page, page_key=key, status="ready")
        entry["design_asset"] = asset
        entry["bindings"] = agent_project_page_bindings(design.project.project_dir, page, asset["pageIds"])
        entry["verification"] = {"status": "pending_user_confirmation", "checks": [
            {"id": name, "status": "passed"} for name in ("lint", "typecheck", "build")],
                                 "errors": []}
        entries.append(entry)
    # 所有页面的源码交互校验成功后才切换预览，失败时保留当前可审阅的工程。
    previous = agent_ui_design_previews.status(task.workspace)
    launch = agent_ui_design_previews.start(design.project, node_executable=inspection.node_executable,
        run_id=task.job_id, mock=AgentUiDesignMock(inspection, task.page_id, design.revision))
    if should_cancel and should_cancel():
        if previous is None or launch.launch_id != previous.launch_id:
            agent_ui_design_previews.stop(task.workspace, launch_id=launch.launch_id)
        raise UiDesignStreamCancelled("设计预览已取消。")
    return {"project_pages": entries, "affected_page_ids": list(design.affected_page_ids)}
