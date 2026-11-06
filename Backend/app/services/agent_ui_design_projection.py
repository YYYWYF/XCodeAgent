"""当前工程身份的 AG-UI 投影；临时 URL 不落盘、不作为文件权限。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.agent_ui_design_mock import AgentUiDesignMock
from app.services.agent_ui_design_preview import agent_ui_design_previews
from app.services.agent_ui_design_project import AgentUiDesignProject, validate_design_project
from app.services.agent_ui_design_workflow import design_environment
from app.workspace.spec_documents import workspace_root


def current_project(state: dict[str, Any], page: dict[str, Any]) -> tuple[AgentUiDesignProject, dict[str, Any]]:
    """只解析固定草稿 ID，并复核正式事实、源码、构建及生成记录。"""
    asset = page.get("design_asset")
    if not isinstance(asset, dict) or asset.get("kind") != "agent-ui-project":
        raise ValueError("当前页面没有 Agent UI 工程设计。")
    draft = asset.get("draft")
    if not isinstance(draft, str) or not re.fullmatch(r"[a-f0-9]{32}", draft):
        raise ValueError("Agent UI 草稿身份无效。")
    documents = {"requirement": state.get("requirement_spec"), "product": state.get("product_plan")}
    if any(not isinstance(value, dict) or value.get("confirmation_status") != "confirmed"
           for value in documents.values()):
        raise ValueError("请先联合确认当前需求和产品规划。")
    facts = hashlib.sha256(json.dumps(documents, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
    if asset.get("factsDigest") != facts:
        raise ValueError("Agent UI 设计对应的产品事实已变化，请重新生成。")
    workspace = workspace_root(state).resolve()
    root = workspace / WORKSPACE_ARTIFACT_DIR / "ui-design/drafts" / draft / "agent-ui-project"
    project = AgentUiDesignProject(workspace, root, root / "tools/agent-ui-consumer",
        str(asset.get("environmentIdentity") or ""), str(asset.get("sourceDigest") or ""),
        str(asset.get("buildDigest") or ""))
    validate_design_project(project)
    record = root / "generation.json"
    if record.is_symlink():
        raise ValueError("设计生成记录不能使用符号链接。")
    evidence = json.loads(record.read_text(encoding="utf-8"))
    if (evidence.get("status") != "built" or any(evidence.get(key) != asset.get(key)
            for key in ("revision", "factsDigest", "environmentIdentity", "sourceDigest", "buildDigest"))
            or asset.get("pageIds", []) != [item["pageId"] for item in evidence.get("pages", [])]
            or page.get("pageId") not in asset.get("pageIds", [])):
        raise ValueError("当前页面、修订与生成记录不匹配。")
    return project, asset


def attach_agent_previews(state: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """只观察当前 owner，任何缺失/陈旧证据均不启动进程或重复模型调用。"""
    pages: list[dict[str, Any]] = []
    checked: dict[str, tuple[AgentUiDesignProject, dict[str, Any]]] = {}
    launch = agent_ui_design_previews.status(workspace_root(state))
    for original in manifest.get("pages", []):
        if not isinstance(original, dict):
            continue
        page = {key: value for key, value in original.items() if key not in {"project_preview", "preview_error"}}
        if page.get("design_asset"):
            try:
                asset = page["design_asset"]
                if not isinstance(asset, dict):
                    raise ValueError("Agent UI 设计资产身份无效。")
                revision = str(asset.get("revision") or "")
                if revision not in checked:
                    checked[revision] = current_project(state, page)
                project, verified = checked[revision]
                if asset != verified or page.get("pageId") not in verified["pageIds"]:
                    raise ValueError("页面与当前设计工程身份不匹配。")
                binding = launch.mock_evidence.get("binding", {}) if launch and launch.mock_evidence else {}
                if (not launch or binding.get("revision") != revision
                        or launch.source_digest != project.source_digest or launch.build_digest != project.build_digest):
                    # 显式启动失败的原因优先于只读观察到的关闭态；有效 launch 会清除旧错误。
                    raise ValueError(original.get("preview_error") or
                                     "当前预览已关闭，请点击「打开预览」重新启动；不会重新生成设计。")
                page["project_preview"] = {"kind": "agent-ui-project", "url": launch.url,
                    "workspace": str(project.workspace), "pageId": page["pageId"], "revision": revision,
                    "launchId": launch.launch_id, "sourceDigest": project.source_digest,
                    "buildDigest": project.build_digest, "source": "official-mock"}
            except (ValueError, OSError, KeyError, TypeError) as exc:
                page["preview_error"] = str(exc)
        pages.append(page)
    return {**manifest, "pages": pages}


def reopen_agent_preview(state: dict[str, Any], manifest: dict[str, Any], page_id: str) -> dict[str, Any]:
    """显式 AG-UI 预览动作只启动当前已校验构建，不重新生成、不确认。"""
    page = next((item for item in manifest.get("pages", []) if item.get("pageId") == page_id), None)
    if not page or page.get("status") not in {"ready", "confirmed"}:
        raise ValueError("请先生成当前 Agent UI 页面。")
    project, asset = current_project(state, page)
    inspection, _cli = design_environment(str(project.workspace))
    launch = agent_ui_design_previews.status(project.workspace)
    if (not launch or not launch.mock_evidence or launch.mock_evidence.get("binding", {}).get("revision") != asset["revision"]):
        agent_ui_design_previews.start(project, node_executable=inspection.node_executable,
            mock=AgentUiDesignMock(inspection, page_id, asset["revision"]))
    return manifest


def project_agent_workflow_preview(result: dict[str, Any]) -> dict[str, Any]:
    """在 AG-UI 输出边界附加活跃 launch，Graph checkpoint 与设计清单只保留稳定资产。"""
    manifest = result.get("ui_designs")
    if not isinstance(manifest, dict) or not any(
            isinstance(page, dict) and page.get("design_asset") for page in manifest.get("pages", [])):
        return result
    projected = attach_agent_previews(result, manifest)
    previews = {page["pageId"]: page for page in projected["pages"]}
    clarification = result.get("clarification")
    if isinstance(clarification, dict) and isinstance(clarification.get("pages"), list):
        clarification = {**clarification, "pages": [
            {**page, **{key: previews.get(page.get("pageId"), {}).get(key)
                       for key in ("project_preview", "preview_error")}}
            for page in clarification["pages"]]}
    return {**result, "ui_designs": projected, "clarification": clarification}
