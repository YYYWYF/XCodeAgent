"""把阶段必需产物问题转换为可执行的生成入口，不承担 Agent 生成或确认逻辑。"""

from dataclasses import dataclass
from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR


@dataclass(frozen=True)
class ArtifactRegeneration:
    """描述不完整产物以及用户可选择的重新生成步骤。"""

    key: str
    node: str
    name: str
    filename: str
    detail: str = ""

    @property
    def label(self) -> str:
        """为恢复按钮提供明确业务名称。"""
        return f"重新生成{self.name}"

    @property
    def message(self) -> str:
        """说明缺少什么、下一步做什么以及必须重新确认。"""
        return f"{self.name}不完整，暂时无法继续。请重新生成并确认新版本，再继续当前流程。"

    @property
    def diagnostic(self) -> str:
        """把文件名及底层读取错误留给可展开的诊断详情。"""
        return f"{self.filename}: {self.detail or '文件缺失、为空或结构不符合当前产物契约。'}"


class StagePrerequisiteError(ValueError):
    """携带确定性重新生成入口的前置产物提示。"""

    def __init__(self, issue: ArtifactRegeneration):
        """保留结构化问题供平台重试协调器使用。"""
        self.issue = issue
        super().__init__(issue.message)


_PRODUCERS = {
    "requirement_spec": ("requirements", "需求文档"),
    "product_plan": ("product_planning", "产品计划"),
    "ui_designs": ("ui_confirmation", "UI 设计"),
    "technical_plan": ("technical_planning_begin", "技术规划"),
    "build_task_plan": ("prepare_build_tasks", "任务计划"),
}


def name_for_node(node: str) -> str:
    """返回平台生成入口的用户名称。"""
    return next((name for producer, name in _PRODUCERS.values() if producer == node), "前置文档")


def artifact_issue(key: str, path: Path, detail: str = "") -> ArtifactRegeneration:
    """把当前产物定位到已有业务生成节点。"""
    node, name = _PRODUCERS[key]
    return ArtifactRegeneration(key, node, name, path.name, detail)


def required_artifacts(node: str) -> set[str]:
    """共用阶段加载的依赖声明，自身可选旧稿不作为生成前置条件。"""
    from app.services.stage_artifacts import (
        _ARTIFACTS, _GENERATED_BASELINES, _PLANNING_INPUTS,
        _RUNTIME_STAGES, _TASK_PLAN_STAGES, _TECHNICAL_STAGES,
    )
    keys = (set(_ARTIFACTS) if node in _TECHNICAL_STAGES | _RUNTIME_STAGES
            else set(_PLANNING_INPUTS.get(node, set())))
    keys.discard(_GENERATED_BASELINES.get(node))
    # 技术事务的 generate/commit/confirm 消费本轮候选；正式文件要到确认时才提交。
    if node in {"technical_planning_begin", "technical_planning_generate", "technical_planning_commit",
                "technical_planning_confirm", "project_planning"}:
        keys.discard("technical_plan")
    if node in _TASK_PLAN_STAGES and node != "prepare_build_tasks":
        keys.add("build_task_plan")
    return keys


def regeneration_for_retry(workspace: str, node: str, graph=None) -> ArtifactRegeneration | None:
    """按上游顺序寻找最早不完整输入，递归前移时不依赖旧 State。"""
    from app.services.stage_artifacts import _ARTIFACTS, _REQUIREMENT_STAGES
    root = Path(workspace) / WORKSPACE_ARTIFACT_DIR
    # 正式应用以 application.json 标识；独立工具 Graph 没有正式规划产物前置契约。
    if not (root / "application.json").is_file():
        return None
    paths = {**_ARTIFACTS, "build_task_plan": "plans/build-task-plan.json"}
    required = required_artifacts(node)
    for key in _PRODUCERS:
        if key not in required:
            continue
        path = root / paths[key]
        if node in _REQUIREMENT_STAGES and (root / "drafts" / paths[key]).is_file():
            path = root / "drafts" / paths[key]
        detail = ""
        try:
            if key == "build_task_plan":
                from app.workspace.task_documents import load_confirmed_build_task_plan
                value = load_confirmed_build_task_plan(workspace)
            else:
                from app.workspace.plan_documents import load_project_plan_json
                value = load_project_plan_json(path, hydrate_detail_designs=True) if path.is_file() else None
            valid = isinstance(value, dict) and bool(value)
        except (OSError, ValueError) as exc:
            valid = False
            detail = str(exc)
        if not valid:
            issue = artifact_issue(key, path, detail)
            # 先修复生成该产物自身所需的上游，避免点击后才发现另一个缺失文件。
            upstream = regeneration_for_retry(workspace, issue.node)
            return upstream or issue
    return None


def require_stage_inputs(state: dict, node: str) -> None:
    """在进入业务节点前发布缺失产物提示，原文件和原确认门保持完整。"""
    workspace = str(state.get("workspace") or state.get("workspace_path") or "")
    issue = regeneration_for_retry(workspace, node) if workspace else None
    if issue:
        raise StagePrerequisiteError(issue)
