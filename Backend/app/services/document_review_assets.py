"""从当前资产发现待确认文档；只读文件，不生成、修复或确认内容。"""

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.application_planning_recovery import application_planning_sha256
from app.domain.execution_recovery import RecoveryExecutionError
from app.services.application_lifecycle import load_application_lifecycle
from app.workspace.plan_documents import load_project_plan_json


@dataclass
class DocumentReview:
    """保存一次当前文档读取结果及其版本依据，不另存业务确认状态。"""

    kind: str
    node: str
    stage: str
    state: dict[str, Any]
    sources: dict[str, str] = field(default_factory=dict)
    planning: bool = True

    @property
    def digest(self) -> str:
        """同时绑定原始文档与已展开的详情，防止外置内容变化漏过确认校验。"""
        artifacts = {key: self.state.get(key) for key in (
            "requirement_spec", "product_plan", "ui_designs", "technical_plan", "revision_draft", "build_task_plan")}
        return application_planning_sha256({"kind": self.kind, "sources": self.sources, "artifacts": artifacts})


class ReviewFiles:
    """使用当前工作区相对路径读取资产，并记录原始文件摘要。"""

    def __init__(self, workspace: str):
        """绑定资产根目录，所有新建执行均使用本机路径。"""
        self.workspace = str(Path(workspace).resolve())
        self.root = Path(self.workspace) / WORKSPACE_ARTIFACT_DIR
        self.sources: dict[str, str] = {}

    def track(self, path: Path) -> None:
        """记录参与审阅的当前文件版本，包括 Markdown；不写入文件。"""
        key = str(path.resolve().relative_to(Path(self.workspace)))
        self.sources[key] = hashlib.sha256(path.read_bytes()).hexdigest()

    def read(self, relative: str, *, required: bool = False, track: bool = True) -> tuple[dict, Path]:
        """只读取当前约定路径，缺少必需输入时明确报告，绝不尝试历史格式。"""
        path = self.root / relative
        if not path.is_file():
            if required:
                raise ValueError(f"审阅所需文档不存在：{relative}")
            return {}, path
        if track:
            self.track(path)
        value = load_project_plan_json(path, hydrate_detail_designs=True)
        if not isinstance(value, dict) or not value:
            raise ValueError(f"审阅文档不是有效对象：{relative}")
        return value, path

    def current(self, relative: str) -> tuple[dict, Path]:
        """当前草稿优先于正式文件，避免旧确认版本遮住新的待确认版本。"""
        draft = "drafts/" + relative
        return self.read(draft if (self.root / draft).is_file() else relative)

    def markdown(self, path: Path) -> None:
        """绑定现有 Markdown 编辑；同步仍由显式确认时的原业务逻辑负责。"""
        markdown = path.with_suffix(".md")
        if markdown.is_file():
            self.track(markdown)


def _pending(value: dict) -> bool:
    """只有文件自身明确待确认才建立审阅，不从生命周期阶段猜测确认状态。"""
    return value.get("confirmation_status") == "pending_user_confirmation"


def _require_confirmed(value: dict, name: str) -> None:
    """下游审阅仍须具备已确认的直接上游，不能借重建跳过原确认门。"""
    if not value or value.get("confirmation_status") != "confirmed":
        raise ValueError(f"请先完成{name}确认，保留当前文档后再审阅下游。")


def find_document_review(workspace: str, *, planning: bool) -> DocumentReview | None:
    """按当前资产顺序发现最早的待确认文档，读取只做校验，不调用模型。"""
    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        return None
    files = ReviewFiles(workspace)
    config, _ = files.read("application.json", track=False)
    # 配置只绑定影响规划契约的能力开关与数据源模式，地址、名称、分支及格式变化不撤销审阅。
    datasource = config.get("datasource") or {}
    files.sources["application-planning-capabilities"] = application_planning_sha256({
        "authEnabled": (config.get("auth") or {}).get("enable"),
        "authorizationEnabled": (config.get("authorization") or {}).get("enabled"),
        "datasourceType": datasource.get("type"),
        "builtinDatabase": datasource.get("type") == "database" and (datasource.get("db") or {}).get("useBuiltin", False),
    })
    active = lifecycle.active_formal_revision
    state: dict[str, Any] = {
        "workspace": files.workspace, "project_id": lifecycle.application.id,
        "workflow_scope": "application_planning" if planning else "",
        "request": active.request if active else str(config.get("senario") or "审阅当前已有文档"),
        "editor_mode": "plan", "application_planning_interaction": {}, "error": None,
    }
    if active:
        # 修订身份来自当前持久化元数据；配置提案仍由原确认服务读取，绝不从正文反推。
        state["change_id"] = active.change_id
    spec, spec_path = files.current("specs/requirement-spec.json")
    product, product_path = files.current("plans/product-plan.json")
    state.update(requirement_spec=spec, product_plan=product,
        requirement_spec_json_path=str(spec_path), requirement_spec_path=str(spec_path.with_suffix(".md")),
        product_plan_json_path=str(product_path), product_plan_path=str(product_path.with_suffix(".md")))
    if _pending(spec) or _pending(product):
        if not planning:
            return None
        from app.services.requirement_spec import validate_requirement_spec_confirmation_readiness
        from app.services.product_plan import validate_product_plan
        errors = validate_requirement_spec_confirmation_readiness(dict(spec)) if spec else ["缺少需求文档"]
        errors += validate_product_plan(product, spec) if product else ["缺少产品规划"]
        if errors:
            raise ValueError("现有需求文档尚不能审阅：" + "；".join(errors))
        files.markdown(spec_path)
        files.markdown(product_path)
        from app.graph.nodes.product_planning import _confirmation_payload
        state.update(phase="requirement_document", status="requires_user_input", clarification=_confirmation_payload(product))
        return DocumentReview("requirement_document", "requirement_document_review",
            "awaiting_requirement_document_confirmation", state, files.sources)
    from app.workspace.spec_documents import load_ui_designs_json
    ui_path = files.root / "specs/ui-designs.json"
    ui = load_ui_designs_json(ui_path)
    state.update(ui_designs=ui, ui_designs_json_path=str(ui_path))
    # 已有待审技术规划优先，旧 UI 待确认状态不能挡住它；仅审 UI 时忽略坏的下游。
    try:
        plan, plan_path = files.current("plans/technical-plan.json")
    except ValueError:
        if not _pending(ui):
            raise
        plan, plan_path = {}, files.root / "plans/technical-plan.json"
    if (planning and _pending(ui) and not _pending(plan)
            and (not plan or lifecycle.initialization.stage == "awaiting_ui_design_confirmation")):
        _require_confirmed(spec, "需求文档")
        _require_confirmed(product, "产品规划")
        from app.graph.nodes.ui_confirmation import _ui_design_confirmation_payload
        from app.protocols.application_page_planning import _with_page_code
        ui = _with_page_code(ui, state)
        state.update(ui_designs=ui, phase="ui_confirmation", status="requires_user_input",
                     clarification=_ui_design_confirmation_payload(state, ui))
        return DocumentReview("ui_designs", "ui_confirmation_review", "awaiting_ui_design_confirmation", state, files.sources)
    if not planning:
        from app.services.document_review_workbench import find_workbench_document_review
        return find_workbench_document_review(files, state, lifecycle)
    if not _pending(plan):
        return None
    _require_confirmed(spec, "需求文档")
    _require_confirmed(product, "产品规划")
    from app.graph.nodes.planning import _planning_confirmation_payload, _project_plan_validation_errors
    state.update(technical_plan=plan, project_plan=plan, technical_plan_json_path=str(plan_path),
                 project_plan_json_path=str(plan_path), project_plan_path=str(plan_path))
    errors = _project_plan_validation_errors(plan, state)
    from app.services.artifact_freshness import upstream_errors
    errors += [error for error in upstream_errors(files.root, "technical_plan", plan) if error != "当前版本尚未确认"]
    if errors:
        raise ValueError("现有技术规划尚不能审阅：" + "；".join(errors))
    from app.domain.application_planning_recovery import (
        ApplicationPlanningOperation, ApplicationPlanningRecoveryBoundary, application_planning_boundary_payload,
    )
    state.update(phase="technical_planning", status="requires_user_input",
        clarification=_planning_confirmation_payload(state, plan),
        application_planning_recovery_boundary=application_planning_boundary_payload(
            operation_id="document-review:" + application_planning_sha256(plan),
            operation=ApplicationPlanningOperation.INITIAL,
            boundary=ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED,
            request=state["request"], candidate=plan))
    return DocumentReview("technical_plan", "technical_planning_review",
        "awaiting_technical_plan_confirmation", state, files.sources)


def document_review_is_current(workspace: str, identity: dict) -> bool:
    """重读当前文档及上游，旧电脑的 Run 或确认凭据不能替代内容版本校验。"""
    candidate = find_document_review(workspace, planning=bool(identity.get("planning")))
    return candidate is not None and candidate.kind == identity.get("kind") and candidate.digest == identity.get("digest")


def require_current_document_review(state: dict) -> None:
    """在消费确认输入之前拒绝外部修改后的旧卡片，避免污染原生 interrupt resume。"""
    identity = state.get("document_review")
    if identity and not document_review_is_current(str(state["workspace"]), identity):
        raise RecoveryExecutionError("DOCUMENT_REVIEW_SOURCE_CHANGED", "文档或上游已变化，请重新同步后审阅当前版本。")


async def project_document_review_availability(workspace: str, *, thread_id: str, planning: bool) -> dict | None:
    """仅检查当前文档是否需要重建审阅，不恢复执行结果或签发确认权限。"""
    from app.persistence.checkpoints import workflow_checkpointer
    from app.services.recovery_checkpoint import checkpoint_thread_exists
    from app.services.application_lifecycle import load_application_lifecycle
    present = await checkpoint_thread_exists(workspace, thread_id)
    stale = False
    if present:
        saver = await workflow_checkpointer(workspace=workspace)
        saved = await saver.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
        values = saved.checkpoint.get("channel_values", {}) if saved else {}
        identity = values.get("document_review")
        try:
            stale = bool(identity and not document_review_is_current(workspace, identity))
        except (OSError, ValueError) as exc:
            return {"status": "blocked", "message": str(exc)}
        if not stale:
            return None
    lifecycle = load_application_lifecycle(workspace)
    if not planning and lifecycle:
        for item in lifecycle.active_executions.values():
            if item.thread_id != thread_id and await checkpoint_thread_exists(workspace, item.thread_id):
                return None
    try:
        candidate = find_document_review(workspace, planning=planning)
        return {"status": "available", "kind": candidate.kind, "digest": candidate.digest} if candidate else None
    except (OSError, ValueError) as exc:
        return {"status": "blocked", "message": str(exc)}
