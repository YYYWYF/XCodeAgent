"""P0.5-C 生产恢复旅程与既有失败节点 Native Recovery 回归合同。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.agents.change_impact_analyzer import ChangeImpactAnalyzer
from app.config import Settings
from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    WorkbenchExecutionStatus,
)
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation,
    ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
)
from app.domain.application_revision import (
    EarliestRevisionArtifact,
    FormalRevisionBranch,
    RevisionImpact,
    RevisionTarget,
)
from app.domain.execution_recovery import (
    DurableExecutionStatus,
    RecoveryActionKind,
    RecoveryDecision,
    RecoveryExecutionError,
)
from app.graph.application_planning_workflow import (
    application_planning_graph_for_request,
    clear_application_planning_graph_cache,
)
from app.graph.workflow import clear_workflow_graph_cache, workflow_graph_for_request
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.persistence.execution_recovery import (
    get_execution,
    get_node_entry_boundary,
    list_recovery_attempts_from_source,
)
from app.protocols.application_page_planning import (
    build_application_page_planning_ag_ui_stream,
)
from app.protocols.application_planning_interrupt import (
    application_planning_interrupt_from_snapshot,
)
from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    start_workbench_execution,
    write_application_lifecycle,
)
from app.services.application_revision_lifecycle import register_revision_impact
from app.services.execution_recovery_coordinator import prepare_continue
from app.services.execution_recovery import observe_execution_started
from app.services.execution_recovery_lineage import resolve_recovery_lineage_head
from app.services.execution_recovery_policies import production_recovery_replay_policies
from app.services.execution_recovery_projection import (
    resolve_execution_recovery_projection,
)
from app.services.execution_recovery_scanner import reconcile_workspace_recovery
from app.services.workflow_reentry import semantic_context_sha256
from app.services.change_contracts import load_confirmed_contract_corpus
from app.workspace.product_plan_documents import (
    write_confirmed_product_plan_documents,
)
from app.workspace.spec_documents import (
    write_confirmed_requirement_spec_document,
    write_ui_designs_json,
)
from app.services.product_plan import create_product_plan
from app.services.requirement_spec import create_requirement_spec
from app.services.unit_generation_contracts import UnitGenerationAttemptResult
from tests.dag_planning_baseline_fixtures import (
    formal_artifacts,
    project_plan,
    write_confirmed_endpoint_designs,
    write_json,
)
from tests.dag_planning_orchestrator_fixtures import model_tasks
from tests.test_build_task_reuse_workspace import _ready_template


class FakeModelConnectionError(Exception):
    """提供可被生产 failure classifier 识别的模型连接错误。"""

    __module__ = "openai"

    def __init__(self, *, model: str, status_code: int = 404) -> None:
        """保存 provider、模型和 HTTP 状态，使测试不访问真实网络。"""

        super().__init__("production journey model failure")
        self.model = model
        self.status_code = status_code


def _settings_for(model_name: str) -> Settings:
    """构造当前模型快照，确保每次真实节点进入边界都读取最新设置。"""

    return Settings(
        model_base_url="https://model.test.invalid/v1",
        model_api_key="test-key",
        model_name=model_name,
    )


def _requirement_model_payload(request: str) -> dict[str, Any]:
    """返回覆盖生产 RequirementSpec 必需业务字段的模型响应。"""

    return {
        "version": "requirements.v1",
        "status": "clear",
        "generated_at": "2026-09-15T00:00:00Z",
        "app_info": {"name": "生产旅程应用", "summary": request},
        "user_roles": [
            {
                "id": "operator",
                "name": "运营人员",
                "description": "维护并查看业务信息。",
                "isSystemRole": False,
                "isInitialAdminRole": False,
            }
        ],
        "feature_modules": [
            {"id": "core", "name": "核心模块", "description": "核心业务能力。"}
        ],
        "pages": [
            {
                "pageId": "home",
                "name": "首页",
                "path": "/",
                "module_id": "core",
                "description": "展示应用核心信息。",
            }
        ],
        "business_flows": [
            {
                "id": "view_home",
                "name": "查看首页",
                "description": "运营人员打开首页查看信息。",
                "steps": ["打开首页", "查看核心信息"],
            }
        ],
        "authorization_requirements": {
            "enabled": False,
            "restrictedPages": [],
            "restrictedOperations": [],
        },
    }


def _product_model_payload(requirement_spec: dict[str, Any]) -> dict[str, Any]:
    """返回严格匹配当前 RequirementSpec 页面身份的 ProductPlan 模型响应。"""

    source_page = requirement_spec["pages"][0]
    return {
        "app": {"name": "生产旅程应用", "summary": "生产旅程应用产品规划。"},
        "business_flows": requirement_spec["business_flows"],
        "pages": [
            {
                "pageId": source_page["pageId"],
                "name": source_page["name"],
                "path": source_page["path"],
                "module_id": source_page["module_id"],
                "description": source_page["description"],
                "goal": "让运营人员快速查看核心信息。",
                "information_items": [
                    {
                        "itemId": "home_summary",
                        "label": "核心信息",
                        "description": "应用当前核心业务信息。",
                    }
                ],
                "actions": [],
                "navigation_targets": [],
                "state_requirements": {
                    "loading": "显示加载状态。",
                    "empty": "显示空状态。",
                    "error": "显示错误状态。",
                    "success": "显示核心信息。",
                    "validation": "无输入校验。",
                },
                "acceptance_criteria": ["用户可以查看首页核心信息。"],
            }
        ],
        "product_acceptance_criteria": ["首页展示核心业务信息。"],
    }


def _authorization_fact_payload() -> dict[str, Any]:
    """返回权限事实提取模型的严格空控制响应。"""

    return {
        "user_roles": [
            {
                "id": "operator",
                "name": "运营人员",
                "description": "维护并查看业务信息。",
            }
        ],
        "authorization_requirements": {
            "restrictedPages": [],
            "restrictedOperations": [],
            "dataAuthorizationIssues": [],
        },
    }


class _ProductionModelBoundary:
    """模拟最低层 ChatModel transport，不替换任何生产业务节点。"""

    def __init__(
        self,
        *,
        settings: Settings,
        request: str,
        calls: list[tuple[str, str]],
        failed_model: str | None,
    ) -> None:
        """保存本次 transport 调用的 Settings 与可变故障模型。"""

        self.settings = settings
        self.request = request
        self.calls = calls
        self.failed_model = failed_model

    def bind_tools(self, _tools: list[Any]) -> "_ProductionModelBoundary":
        """保持 production requirements runnable 的 tool-binding 形状。"""

        return self

    def _kind(self, prompt: str) -> str:
        """按生产 prompt 所属的最低模型边界标记调用，不改变业务路由。"""

        if "extract explicit authorization business facts" in prompt:
            return "authorization"
        if "product-planning model" in prompt:
            return "product_planning"
        return "requirements"

    def _response_text(self, kind: str) -> str:
        """为真实 analyzer/planner 返回当前合同要求的完整 JSON。"""

        if kind == "requirements":
            return json.dumps(
                _requirement_model_payload(self.request), ensure_ascii=False
            )
        if kind == "authorization":
            return json.dumps(_authorization_fact_payload(), ensure_ascii=False)
        return json.dumps(
            _product_model_payload(_requirement_model_payload(self.request)),
            ensure_ascii=False,
        )

    def _invoke(self, prompt: str) -> AIMessage:
        """记录一次最低层 provider 调用并模拟可切换的首次模型失败。"""

        kind = self._kind(prompt)
        self.calls.append((self.settings.model_name, kind))
        if kind == "requirements" and self.settings.model_name == self.failed_model:
            raise FakeModelConnectionError(model=self.settings.model_name)
        return AIMessage(content=self._response_text(kind))

    def invoke(self, prompt: str) -> AIMessage:
        """模拟非流式权限事实模型调用。"""

        return self._invoke(prompt)

    def stream(self, prompt: str):
        """模拟 production 节点消费的流式模型调用。"""

        yield self._invoke(prompt)


class _BlockingProductionModelBoundary(_ProductionModelBoundary):
    """在指定的最低模型 transport 边界阻塞一次，保留真实 Node/Runtime。"""

    def __init__(
        self,
        *,
        settings: Settings,
        request: str,
        calls: list[tuple[str, str]],
        block_state: dict[str, bool],
        dependency_started: threading.Event,
        dependency_release: threading.Event,
        dependency_finished: threading.Event,
    ) -> None:
        """保存一次性阻塞门及其完成信号。"""

        super().__init__(
            settings=settings,
            request=request,
            calls=calls,
            failed_model=None,
        )
        self.block_state = block_state
        self.dependency_started = dependency_started
        self.dependency_release = dependency_release
        self.dependency_finished = dependency_finished

    def _invoke(self, prompt: str) -> AIMessage:
        """在首个 Requirements transport 调用处制造可控的真实运行窗口。"""

        kind = self._kind(prompt)
        if kind == "requirements" and not self.block_state["consumed"]:
            self.block_state["consumed"] = True
            self.dependency_started.set()
            if not self.dependency_release.wait(timeout=30):
                raise TimeoutError("blocking production model dependency timed out")
            self.dependency_finished.set()
        return super()._invoke(prompt)


def _blocking_model_factory(
    *,
    request: str,
    calls: list[tuple[str, str]],
    dependency_started: threading.Event,
    dependency_release: threading.Event,
    dependency_finished: threading.Event,
):
    """创建只在首个 Requirements transport 调用阻塞一次的 production factory。"""

    block_state = {"consumed": False}

    def factory(settings: Settings, **_kwargs: Any) -> _BlockingProductionModelBoundary:
        """为每次真实 Node 调用创建共享一次性阻塞 transport。"""

        return _BlockingProductionModelBoundary(
            settings=settings,
            request=request,
            calls=calls,
            block_state=block_state,
            dependency_started=dependency_started,
            dependency_release=dependency_release,
            dependency_finished=dependency_finished,
        )

    return factory


class _InvalidTechnicalPlanTransport:
    """只在 TechnicalPlan 生成的模型 transport 边界返回无效 JSON。"""

    def __init__(self, calls: list[str]) -> None:
        """保存模型请求次数，供测试确认 production Node 自己完成重试。"""

        self.calls = calls

    def stream(self, _prompt: str):
        """返回无法形成 TechnicalPlan 根结构的模型响应。"""

        self.calls.append("technical_planning_generate")
        yield AIMessage(content="{}")


def _invalid_technical_plan_model_factory(calls: list[str]):
    """创建只替换 TechnicalPlan 最低模型 transport 的 production factory。"""

    def factory(_settings: Settings, **_kwargs: Any) -> _InvalidTechnicalPlanTransport:
        """为每次 production Node 调用返回一个无效响应 transport。"""

        return _InvalidTechnicalPlanTransport(calls)

    return factory


class _ImpactModel:
    """返回一次真实 ChangeImpactAnalyzer 使用的只读模型 JSON。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        """保存模型结果并记录 analyzer 调用次数。"""

        self.payload = payload
        self.calls = 0

    def invoke(self, _messages: list[Any]) -> SimpleNamespace:
        """模拟 analyzer 的底层模型返回，不触碰 Coordinator 或 lifecycle。"""

        self.calls += 1
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


def _model_factory(
    *,
    request: str,
    calls: list[tuple[str, str]],
    failed_model: str | None,
):
    """创建只替换 provider transport 的 factory，保留 production analyzer/planner。"""

    def factory(settings: Settings, **_kwargs: Any) -> _ProductionModelBoundary:
        """按节点调用时传入的 Settings 创建新的 transport boundary。"""

        return _ProductionModelBoundary(
            settings=settings,
            request=request,
            calls=calls,
            failed_model=failed_model,
        )

    return factory


async def _consume(stream: Any) -> list[str]:
    """完整消费 AG-UI 流，确保测试驱动真实 Runtime 到终态。"""

    return [frame async for frame in stream]


async def _execute_current_recovery_action(
    workspace: Path,
    *,
    run_id: str,
) -> Any:
    """从公开 Recovery Projection 取得 Backend action 并执行唯一 public retry。"""

    projection = await resolve_execution_recovery_projection(str(workspace))
    candidate = next(
        candidate
        for candidate in projection.candidates
        if candidate.source_run_id == run_id
    )
    assert candidate.recovery_action_plan is not None
    action_plan = candidate.recovery_action_plan.model_dump(
        mode="json",
        by_alias=True,
    )
    primary_action = action_plan["primaryAction"]
    assert primary_action["kind"] == "retry_failed_node"
    await _consume(
        build_execution_recovery_ag_ui_stream(
            payload={
                "forwardedProps": {
                    "workspaceRoot": str(workspace),
                    "executionRecovery": {
                        "action": "execute",
                        "incidentId": action_plan["incidentId"],
                        "actionId": primary_action["actionId"],
                    },
                }
            }
        )
    )
    resolution = await resolve_recovery_lineage_head(
        str(workspace),
        thread_id=candidate.thread_id,
        execution_kind=candidate.execution_kind,
    )
    assert resolution.head is not None
    return resolution.head


async def _read_current_continue_action(
    workspace: Path,
    *,
    run_id: str,
) -> Any:
    """从真实 Recovery projection 读取 CONTINUE_CHECKPOINT，不创建 child。"""

    projection = await resolve_execution_recovery_projection(str(workspace))
    candidate = next(
        candidate
        for candidate in projection.candidates
        if candidate.source_run_id == run_id
    )
    assert candidate.recovery_action_plan is not None
    action_plan = candidate.recovery_action_plan.model_dump(
        mode="json",
        by_alias=True,
    )
    primary_action = action_plan["primaryAction"]
    assert primary_action["kind"] == RecoveryActionKind.CONTINUE_CHECKPOINT.value
    return action_plan


async def _execute_current_continue_action(
    workspace: Path,
    *,
    run_id: str,
) -> Any:
    """执行 Projection 签发的 CONTINUE_CHECKPOINT，并返回新的 lineage head。"""

    projection = await resolve_execution_recovery_projection(str(workspace))
    candidate = next(
        candidate
        for candidate in projection.candidates
        if candidate.source_run_id == run_id
    )
    assert candidate.recovery_action_plan is not None
    action_plan = candidate.recovery_action_plan.model_dump(
        mode="json",
        by_alias=True,
    )
    primary_action = action_plan["primaryAction"]
    assert primary_action["kind"] == RecoveryActionKind.CONTINUE_CHECKPOINT.value
    frames = await _consume(
        build_execution_recovery_ag_ui_stream(
            payload={
                "forwardedProps": {
                    "workspaceRoot": str(workspace),
                    "executionRecovery": {
                        "action": "execute",
                        "incidentId": action_plan["incidentId"],
                        "actionId": primary_action["actionId"],
                    },
                }
            }
        )
    )
    if any('"status":"failed"' in frame for frame in frames):
        attempts = await list_recovery_attempts_from_source(workspace, run_id)
        raise AssertionError(
            "CONTINUE_CHECKPOINT failed: "
            + " | ".join(frames[-6:])
            + f" attempts={attempts}"
        )
    resolution = await resolve_recovery_lineage_head(
        str(workspace),
        thread_id=candidate.thread_id,
        execution_kind=candidate.execution_kind,
    )
    assert resolution.head is not None
    return resolution.head


def _write_real_workbench_formal_artifacts(
    workspace: Path,
    plan: dict[str, Any],
) -> None:
    """写入真实 Workbench 运行所需的最小完整正式产物与 Endpoint Design。"""

    for page in plan.get("pages", []):
        references = page.setdefault("references", {})
        references["endpoint_dependencies"] = [
            {"endpoint_id": f"{page['pageId']}.list"}
        ]
    technical_plan = dict(formal_artifacts(plan)["technical_plan"])
    requirement_spec = {
        "confirmation_status": "confirmed",
        "app_info": {
            "name": "生产旅程应用",
            "summary": "验证真实 Workbench 工作区执行。",
        },
    }
    product_plan = {
        "confirmation_status": "confirmed",
        "app": {"name": "生产旅程应用", "summary": "生产旅程应用产品规划。"},
        "pages": [
            {
                "pageId": page["pageId"],
                "path": page["path"],
                "name": page["pageId"],
                "description": "生产旅程页面。",
                "actions": [],
            }
            for page in plan.get("pages", [])
        ],
    }
    write_json(
        workspace,
        ".xcodeagent/specs/requirement-spec.json",
        requirement_spec,
    )
    write_json(workspace, ".xcodeagent/plans/product-plan.json", product_plan)
    write_json(
        workspace,
        ".xcodeagent/specs/ui-designs.json",
        {"confirmation_status": "skipped", "pages": []},
    )
    write_json(
        workspace,
        ".xcodeagent/plans/technical-plan.json",
        technical_plan,
    )
    write_confirmed_endpoint_designs(workspace, plan)


def _planning_payload(
    workspace: Path,
    *,
    thread_id: str,
    run_id: str,
    project_id: str,
    resume_from: str | None = None,
) -> dict[str, Any]:
    """构造真实 application planning AG-UI 首次请求。"""

    payload = {
        "threadId": thread_id,
        "runId": run_id,
        "projectId": project_id,
        "sessionId": f"{thread_id}-session",
        "message": "创建一个生产旅程应用",
        "forwardedProps": {
            "workspaceRoot": str(workspace),
            "workflowScope": "application_planning",
            "application": {"id": project_id, "name": "生产旅程应用"},
        },
    }
    if resume_from is not None:
        payload["resumeFrom"] = resume_from
    return payload


def _write_planning_lifecycle(
    workspace: Path,
    *,
    thread_id: str,
    run_id: str,
    project_id: str,
    stage: ApplicationLifecycleStage,
    status: ApplicationLifecycleStatus,
) -> None:
    """写入生产 Graph 所需的当前 application lifecycle ownership。"""

    lifecycle = create_application_lifecycle(
        application_id=project_id,
        application_name="生产旅程应用",
        initialization_thread_id=thread_id,
        active_run_id=run_id,
    ).model_copy(
        update={
            "initialization": ApplicationInitialization(
                stage=stage,
                status=status,
                threadId=thread_id,
            )
        }
    )
    write_application_lifecycle(workspace, lifecycle, expected_revision=0)


async def _seed_planning_checkpoint(
    workspace: Path,
    *,
    thread_id: str,
    run_id: str,
    project_id: str,
    lifecycle_stage: ApplicationLifecycleStage = ApplicationLifecycleStage.READY_FOR_WORKBENCH,
    lifecycle_status: ApplicationLifecycleStatus = ApplicationLifecycleStatus.COMPLETED,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """写入正式 baseline artifacts 与原 planning thread 的 production checkpoint。"""

    request = "创建一个生产旅程应用"
    requirement_spec = create_requirement_spec(
        request,
        agent_spec=_requirement_model_payload(request),
        allow_inferred_defaults=False,
    )
    requirement_spec["confirmation_status"] = "confirmed"
    product_plan = create_product_plan(
        requirement_spec,
        agent_plan=_product_model_payload(requirement_spec),
    )
    product_plan["confirmation_status"] = "confirmed"
    ui_designs = {
        "schema_version": "ui-manifest.v3",
        "confirmation_status": "confirmed",
        "pages": [],
    }
    _write_planning_lifecycle(
        workspace,
        thread_id=thread_id,
        run_id=run_id,
        project_id=project_id,
        stage=lifecycle_stage,
        status=lifecycle_status,
    )
    state = {"workspace": str(workspace)}
    write_confirmed_requirement_spec_document(state, requirement_spec)
    write_confirmed_product_plan_documents(state, product_plan)
    write_ui_designs_json(state, ui_designs)
    write_json(
        workspace,
        ".xcodeagent/plans/technical-plan.json",
        {
            "confirmation_status": "confirmed",
            "version": "baseline",
            "pages": [],
            "api_contracts": [],
        },
    )
    graph = await application_planning_graph_for_request(
        workspace=str(workspace),
        project_id=project_id,
    )
    await graph.aupdate_state(
        {"configurable": {"thread_id": thread_id}},
        {
            "workspace": str(workspace),
            "project_id": project_id,
            "workflow_scope": "application_planning",
            "request": request,
            "application_name": "生产旅程应用",
            "active_thread_id": thread_id,
            "active_run_id": run_id,
            "requirement_spec": requirement_spec,
            "product_plan": product_plan,
            "ui_designs": ui_designs,
            "technical_plan": {"version": "baseline"},
            "phase": "completed",
            "status": "completed",
            "product_stage_conversation": True,
            "resume_from": "",
        },
    )
    return graph, requirement_spec, product_plan


async def _seed_native_planning_interrupt_checkpoint(
    workspace: Path,
    *,
    thread_id: str,
    run_id: str,
    project_id: str,
) -> tuple[Any, Any]:
    """用真实 Application Planning review Node 提交可恢复的原生 interrupt。"""

    _write_planning_lifecycle(
        workspace,
        thread_id=thread_id,
        run_id=run_id,
        project_id=project_id,
        stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        status=ApplicationLifecycleStatus.AWAITING_USER,
    )
    graph = await application_planning_graph_for_request(
        workspace=str(workspace),
        project_id=project_id,
    )
    await observe_execution_started(
        workspace=str(workspace),
        project_id=project_id,
        thread_id=thread_id,
        run_id=run_id,
        workflow_scope="application_planning",
        first_node="requirements",
        owner_session_id=f"{thread_id}-session",
        lease_ttl=1,
    )
    config = {"configurable": {"thread_id": thread_id}}
    await graph.aupdate_state(
        config,
        {
            "workspace": str(workspace),
            "project_id": project_id,
            "workflow_scope": "application_planning",
            "request": "创建一个生产旅程应用",
            "active_thread_id": thread_id,
            "active_run_id": run_id,
            "owner_session_id": f"{thread_id}-session",
            "phase": "requirements",
            "status": "requires_user_input",
            "requirement_spec": {
                "confirmation_status": "pending_user_confirmation",
                "app_info": {"name": "生产旅程应用", "summary": "需要补充角色信息。"},
            },
            "clarification": {
                "mode": "ask_user_question",
                "status": "requires_user_input",
                "questions": [{"id": "role", "prompt": "请补充用户角色。"}],
            },
            "resume_from": "",
        },
        as_node="requirements",
    )
    async for _update in graph.astream(None, config=config, stream_mode="updates"):
        pass
    snapshot = await graph.aget_state(config)
    return graph, snapshot


async def _seed_workbench_middle_crash_checkpoint(
    workspace: Path,
    *,
    thread_id: str,
    run_id: str,
    project_id: str,
) -> tuple[Any, Any]:
    """为 C4 reconciliation characterization 写入 deterministic terminal 前 checkpoint。"""

    plan = project_plan()
    for key, payload in formal_artifacts(plan).items():
        write_json(
            workspace,
            {
                "requirement_spec": ".xcodeagent/specs/requirement-spec.json",
                "product_plan": ".xcodeagent/plans/product-plan.json",
                "ui_designs": ".xcodeagent/specs/ui-designs.json",
                "technical_plan": ".xcodeagent/plans/technical-plan.json",
            }[key],
            payload,
        )
    write_confirmed_endpoint_designs(workspace, plan)
    _write_planning_lifecycle(
        workspace,
        thread_id=thread_id,
        run_id=run_id,
        project_id=project_id,
        stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
        status=ApplicationLifecycleStatus.COMPLETED,
    )
    start_workbench_execution(
        workspace,
        scope="application",
        target_id="application",
        page_id=None,
        thread_id=thread_id,
        run_id=run_id,
        phase="inspect_workspace",
    )
    graph = await workflow_graph_for_request(
        workspace=str(workspace),
        project_id=project_id,
    )
    await observe_execution_started(
        workspace=str(workspace),
        project_id=project_id,
        thread_id=thread_id,
        run_id=run_id,
        workflow_scope=None,
        first_node="inspect_workspace",
        owner_session_id=f"{thread_id}-session",
        lease_ttl=1,
    )
    await graph.aupdate_state(
        {"configurable": {"thread_id": thread_id}},
        {
            "workspace": str(workspace),
            "project_id": project_id,
            "request": "开始构建生产旅程应用",
            "active_thread_id": thread_id,
            "active_run_id": run_id,
            "owner_session_id": f"{thread_id}-session",
            "phase": "inspect_workspace",
            "status": "completed",
            "workspace_snapshot_summary": {
                "file_manifest": {
                    "total_files_indexed": 1,
                    "source_files_indexed": 1,
                },
                "code_graph": {"available": False},
            },
            "workspace_revision": "workspace-revision-c3",
        },
        as_node="inspect_workspace",
    )
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    return graph, snapshot


class P04GProductionJourneyTests(unittest.IsolatedAsyncioTestCase):
    """验证真实生产入口、checkpoint 对账、公开 Recovery action 与 child context 闭环。"""

    async def test_c2_first_node_crash_restarts_at_requirements_checkpoint(self) -> None:
        """真实首节点 transport 阻塞后，CONTINUE action 必须启动 requirements child。"""

        project_id = "p0-5c-first-node-app"
        thread_id = "p0-5c-first-node-thread"
        source_run_id = "p0-5c-first-node-source"
        model_calls: list[tuple[str, str]] = []
        dependency_started = threading.Event()
        dependency_release = threading.Event()
        dependency_finished = threading.Event()
        runtime_task: asyncio.Task[list[str]] | None = None
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            try:
                with (
                    patch(
                        "app.config.Settings.from_env",
                        side_effect=lambda: _settings_for("MiMo"),
                    ),
                    patch(
                        "app.agents.main.requirements_analyzer.create_chat_model",
                        new=_blocking_model_factory(
                            request="创建一个生产旅程应用",
                            calls=model_calls,
                            dependency_started=dependency_started,
                            dependency_release=dependency_release,
                            dependency_finished=dependency_finished,
                        ),
                    ),
                    patch(
                        "app.agents.main.product_planner.create_chat_model",
                        new=_model_factory(
                            request="创建一个生产旅程应用",
                            calls=model_calls,
                            failed_model=None,
                        ),
                    ),
                ):
                    old_graph = await application_planning_graph_for_request(
                        workspace=str(workspace),
                        project_id=project_id,
                    )
                    _write_planning_lifecycle(
                        workspace,
                        thread_id=thread_id,
                        run_id=source_run_id,
                        project_id=project_id,
                        stage=ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
                        status=ApplicationLifecycleStatus.PENDING,
                    )
                    runtime_task = asyncio.create_task(
                        _consume(
                            build_application_page_planning_ag_ui_stream(
                                graph=application_planning_graph_for_request,
                                payload=_planning_payload(
                                    workspace,
                                    thread_id=thread_id,
                                    run_id=source_run_id,
                                    project_id=project_id,
                                    resume_from="requirements",
                                ),
                            )
                        )
                    )
                    self.assertTrue(
                        await asyncio.wait_for(
                            asyncio.to_thread(dependency_started.wait, 5),
                            timeout=10,
                        )
                    )
                    # transport 线程发出信号后，Durable execution 的 current_node 可能
                    # 仍在提交边界；轮询确保断言观察到已落盘的 Requirements 执行状态。
                    running = None
                    for _ in range(100):
                        running = await get_execution(workspace, source_run_id)
                        if (
                            running is not None
                            and running.status is DurableExecutionStatus.RUNNING
                            and running.current_node == "requirements"
                        ):
                            break
                        await asyncio.sleep(0.01)
                    self.assertIsNotNone(running)
                    assert running is not None
                    self.assertEqual(running.status, DurableExecutionStatus.RUNNING)
                    self.assertEqual(running.current_node, "requirements")
                    source_snapshot = await old_graph.aget_state(
                        {"configurable": {"thread_id": thread_id}}
                    )
                    self.assertEqual(tuple(source_snapshot.next), ("requirements",))

                    scan = await reconcile_workspace_recovery(
                        workspace,
                        locally_active_run_ids=set(),
                        current_backend_instance_id="backend-after-c2-restart",
                        now=datetime.now(timezone.utc) + timedelta(days=1),
                    )
                    self.assertEqual(scan.interrupted_run_ids, [source_run_id])
                    source = await get_execution(workspace, source_run_id)
                    self.assertIsNotNone(source)
                    assert source is not None
                    self.assertEqual(source.status, DurableExecutionStatus.INTERRUPTED)

                    runtime_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await runtime_task
                    runtime_task = None

                    clear_application_planning_graph_cache()
                    self.assertTrue(
                        await close_workflow_checkpointer_for_workspace(
                            workspace=str(workspace),
                            project_id=project_id,
                        )
                    )
                    restarted_graph = await application_planning_graph_for_request(
                        workspace=str(workspace),
                        project_id=project_id,
                    )
                    self.assertIsNot(restarted_graph, old_graph)
                    action_plan = await _read_current_continue_action(
                        workspace,
                        run_id=source_run_id,
                    )
                    self.assertEqual(
                        action_plan["primaryAction"]["kind"],
                        RecoveryActionKind.CONTINUE_CHECKPOINT.value,
                    )
                    # 同步 transport 在被取消的旧线程中仍可能持有执行权；先完成
                    # child fork，再释放它，避免旧线程在 fork 前推进 lifecycle。
                    child = await _execute_current_continue_action(
                        workspace,
                        run_id=source_run_id,
                    )
                    dependency_release.set()
                    self.assertTrue(
                        await asyncio.wait_for(
                            asyncio.to_thread(dependency_finished.wait, 5),
                            timeout=10,
                        )
                    )

                self.assertNotEqual(child.run_id, source_run_id)
                self.assertEqual(child.first_node, "requirements")
                self.assertNotIn(child.first_node, {"START", "workflow_entry"})
                child_boundary = await get_node_entry_boundary(
                    workspace,
                    source_run_id=child.run_id,
                    thread_id=thread_id,
                    target_node="requirements",
                )
                self.assertIsNotNone(child_boundary)
                assert child_boundary is not None
                child_snapshot = await restarted_graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": child_boundary.checkpoint_ns,
                            "checkpoint_id": child_boundary.checkpoint_id,
                        }
                    }
                )
                self.assertEqual(tuple(child_snapshot.next), ("requirements",))
                self.assertTrue(
                    any(kind == "requirements" for _model, kind in model_calls)
                )
            finally:
                dependency_release.set()
                if runtime_task is not None:
                    if not runtime_task.done():
                        runtime_task.cancel()
                    try:
                        await runtime_task
                    except asyncio.CancelledError:
                        pass
                clear_application_planning_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )

    async def test_c3_workbench_middle_node_crash_continues_prepare_without_rescan(
        self,
    ) -> None:
        """真实 inspect→prepare 阻塞后 owner loss，CONTINUE child 不得重跑 inspect。"""

        project_id = "p0-5c-middle-node-app"
        thread_id = "p0-5c-middle-node-thread"
        source_run_id = "p0-5c-middle-node-source"
        dependency_started = asyncio.Event()
        dependency_release = asyncio.Event()
        generation_calls: list[str] = []
        inspection_calls: list[str] = []
        runtime_task: asyncio.Task[list[str]] | None = None

        async def generate_once(job: Any, **_kwargs: Any) -> UnitGenerationAttemptResult:
            """在真实 prepare_build_tasks 的首个外部规划调用处阻塞一次。"""

            generation_calls.append(job.identity.planning_run_id)
            if len(generation_calls) == 1:
                dependency_started.set()
                await dependency_release.wait()
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        def inspection_spy(*args: Any, **kwargs: Any) -> Any:
            """观察真实 inspect_workspace service 调用，不替换 production Node。"""

            inspection_calls.append("inspect_workspace")
            from app.services.workspace_inspector import inspect_workspace as real_inspect

            return real_inspect(*args, **kwargs)

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            plan = project_plan()
            _write_real_workbench_formal_artifacts(workspace, plan)
            template_ready = _ready_template(workspace)
            _write_planning_lifecycle(
                workspace,
                thread_id=thread_id,
                run_id=source_run_id,
                project_id=project_id,
                stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                status=ApplicationLifecycleStatus.COMPLETED,
            )
            try:
                with (
                    patch(
                        "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
                        return_value=template_ready,
                    ),
                    patch(
                        "app.graph.nodes.tasks.inspect_template_generation_readiness",
                        return_value=template_ready,
                    ),
                    patch(
                        "app.services.dag_planning_orchestrator.generate_unit_candidate_once",
                        new=generate_once,
                    ),
                    patch(
                        "app.graph.nodes.workspace_inspection.inspect_workspace_service",
                        new=inspection_spy,
                    ),
                ):
                    old_graph = await workflow_graph_for_request(
                        workspace=str(workspace),
                        project_id=project_id,
                    )
                    runtime_task = asyncio.create_task(
                        _consume(
                            build_workflow_ag_ui_stream(
                                graph=workflow_graph_for_request,
                                payload={
                                    "threadId": thread_id,
                                    "runId": source_run_id,
                                    "projectId": project_id,
                                    "sessionId": "p0-5c-middle-node-session",
                                    "request": "开始构建生产旅程应用",
                                    "resumeFrom": "inspect_workspace",
                                    "forwardedProps": {
                                        "workspaceRoot": str(workspace),
                                        "buildExecutionScope": {
                                            "type": "page",
                                            "targetId": "customers",
                                        },
                                        "application": {
                                            "id": project_id,
                                            "name": "生产旅程应用",
                                        },
                                    },
                                },
                            )
                        )
                    )
                    try:
                        await asyncio.wait_for(dependency_started.wait(), timeout=10)
                    except asyncio.TimeoutError as exc:
                        current = await get_execution(workspace, source_run_id)
                        self.fail(
                            "prepare external dependency was not reached: "
                            f"execution={current} task_done={runtime_task.done()} "
                            f"inspection_calls={inspection_calls} generation_calls={generation_calls}"
                        )
                        raise exc
                    running = await get_execution(workspace, source_run_id)
                    self.assertIsNotNone(running)
                    assert running is not None
                    self.assertEqual(running.status, DurableExecutionStatus.RUNNING)
                    self.assertEqual(
                        running.current_node,
                        "prepare_build_tasks",
                        f"status={running.status} task_done={runtime_task.done()}",
                    )
                    source_snapshot = await old_graph.aget_state(
                        {"configurable": {"thread_id": thread_id}}
                    )
                    self.assertEqual(tuple(source_snapshot.next), ("prepare_build_tasks",))
                    self.assertEqual(len(inspection_calls), 1)

                    scan = await reconcile_workspace_recovery(
                        workspace,
                        locally_active_run_ids=set(),
                        current_backend_instance_id="backend-after-c3-restart",
                        now=datetime.now(timezone.utc) + timedelta(days=1),
                    )
                    self.assertEqual(scan.interrupted_run_ids, [source_run_id])
                    source_after_scan = await get_execution(workspace, source_run_id)
                    self.assertIsNotNone(source_after_scan)
                    assert source_after_scan is not None
                    self.assertEqual(
                        source_after_scan.status,
                        DurableExecutionStatus.INTERRUPTED,
                    )

                    runtime_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await runtime_task
                    runtime_task = None
                    dependency_release.set()

                    clear_workflow_graph_cache()
                    self.assertTrue(
                        await close_workflow_checkpointer_for_workspace(
                            workspace=str(workspace),
                            project_id=project_id,
                        )
                    )
                    restarted_graph = await workflow_graph_for_request(
                        workspace=str(workspace),
                        project_id=project_id,
                    )
                    self.assertIsNot(restarted_graph, old_graph)
                    action_plan = await _read_current_continue_action(
                        workspace,
                        run_id=source_run_id,
                    )
                    self.assertEqual(
                        action_plan["primaryAction"]["kind"],
                        RecoveryActionKind.CONTINUE_CHECKPOINT.value,
                    )
                    child = await _execute_current_continue_action(
                        workspace,
                        run_id=source_run_id,
                    )
                    self.assertNotEqual(child.run_id, source_run_id)
                    self.assertEqual(child.first_node, "prepare_build_tasks")
                    self.assertNotIn(child.first_node, {"START", "workflow_entry"})
            finally:
                dependency_release.set()
                if runtime_task is not None:
                    if not runtime_task.done():
                        runtime_task.cancel()
                    try:
                        await runtime_task
                    except asyncio.CancelledError:
                        pass
                clear_workflow_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )

        self.assertEqual(len(inspection_calls), 1)

    async def test_c5_native_interrupt_restarts_as_awaiting_user_without_child(self) -> None:
        """原生交互 checkpoint 提交后崩溃，重启保留 interaction 且不创建 child。"""

        project_id = "p0-5c-native-interrupt-app"
        thread_id = "p0-5c-native-interrupt-thread"
        source_run_id = "p0-5c-native-interrupt-source"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            old_graph, snapshot = await _seed_native_planning_interrupt_checkpoint(
                workspace,
                thread_id=thread_id,
                run_id=source_run_id,
                project_id=project_id,
            )
            try:
                self.assertTrue(
                    any(
                        bool(getattr(task, "interrupts", ()))
                        for task in snapshot.tasks
                    )
                )
                scan = await reconcile_workspace_recovery(
                    workspace,
                    locally_active_run_ids=set(),
                    current_backend_instance_id="backend-after-c5-restart",
                    now=datetime.now(timezone.utc) + timedelta(days=1),
                )
                self.assertEqual(scan.interrupted_run_ids, [source_run_id])
                clear_application_planning_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )
                restarted_graph = await application_planning_graph_for_request(
                    workspace=str(workspace),
                    project_id=project_id,
                )
                self.assertIsNot(restarted_graph, old_graph)
                projection = await resolve_execution_recovery_projection(str(workspace))
                self.assertFalse(
                    any(item.source_run_id == source_run_id for item in projection.candidates)
                )
                source = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(source.status, DurableExecutionStatus.AWAITING_USER)
                restored = await restarted_graph.aget_state(
                    {"configurable": {"thread_id": thread_id}}
                )
                self.assertTrue(
                    any(bool(getattr(task, "interrupts", ())) for task in restored.tasks)
                )
                lifecycle = load_application_lifecycle(workspace)
                self.assertIsNotNone(lifecycle)
                assert lifecycle is not None
                self.assertEqual(
                    lifecycle.initialization.status,
                    ApplicationLifecycleStatus.AWAITING_USER,
                )
                self.assertEqual(
                    await list_recovery_attempts_from_source(workspace, source_run_id),
                    [],
                )
            finally:
                clear_application_planning_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )

    async def test_c4_terminal_commit_reconciles_workbench_before_new_execution(
        self,
    ) -> None:
        """保留 terminal checkpoint 对账 characterization，不将其宣称为 Runtime crash E2E。"""

        project_id = "p0-5c-terminal-finalization-app"
        thread_id = "p0-5c-terminal-finalization-thread"
        source_run_id = "p0-5c-terminal-finalization-source"
        next_run_id = "p0-5c-terminal-finalization-next"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            graph, _pending_snapshot = await _seed_workbench_middle_crash_checkpoint(
                workspace,
                thread_id=thread_id,
                run_id=source_run_id,
                project_id=project_id,
            )
            try:
                terminal_config = await graph.aupdate_state(
                    {"configurable": {"thread_id": thread_id}},
                    {
                        "active_run_id": source_run_id,
                        "active_thread_id": thread_id,
                        "phase": "finalize_project",
                        "status": "completed",
                    },
                    as_node="finalize_project",
                )
                terminal_snapshot = await graph.aget_state(terminal_config)
                self.assertEqual(tuple(terminal_snapshot.next), ())
                scan = await reconcile_workspace_recovery(
                    workspace,
                    locally_active_run_ids=set(),
                    current_backend_instance_id="backend-after-c4-restart",
                    now=datetime.now(timezone.utc) + timedelta(days=1),
                )
                self.assertEqual(scan.interrupted_run_ids, [source_run_id])

                clear_workflow_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )
                await workflow_graph_for_request(
                    workspace=str(workspace),
                    project_id=project_id,
                )
                projection = await resolve_execution_recovery_projection(str(workspace))
                self.assertFalse(
                    any(item.source_run_id == source_run_id for item in projection.candidates)
                )
                source = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(source.status, DurableExecutionStatus.COMPLETED)
                self.assertEqual(
                    await list_recovery_attempts_from_source(workspace, source_run_id),
                    [],
                )
                lifecycle = load_application_lifecycle(workspace)
                self.assertIsNotNone(lifecycle)
                assert lifecycle is not None
                self.assertNotIn(source_run_id, lifecycle.active_executions)
                self.assertIsNone(lifecycle.active_run_id)
                self.assertIsNone(lifecycle.resource_locks.application)
                new_execution = start_workbench_execution(
                    workspace,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id=thread_id,
                    run_id=next_run_id,
                    phase="inspect_workspace",
                )
                self.assertIn(next_run_id, new_execution.active_executions)
            finally:
                clear_workflow_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )

    async def test_initial_requirements_uses_real_node_and_public_child_retry(self) -> None:
        """首次 Requirements 真实节点 404 后只重入 requirements，并读取 MiMo。"""

        model_config = {"name": "DeepSeek"}
        model_calls: list[tuple[str, str]] = []
        project_id = "p0-4g-requirements-app"
        thread_id = "p0-4g-requirements-thread"
        source_run_id = "p0-4g-requirements-source"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            _write_planning_lifecycle(
                workspace,
                thread_id=thread_id,
                run_id=source_run_id,
                project_id=project_id,
                stage=ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
                status=ApplicationLifecycleStatus.PENDING,
            )
            try:
                with (
                    patch(
                        "app.config.Settings.from_env",
                        side_effect=lambda: _settings_for(model_config["name"]),
                    ),
                    patch(
                        "app.agents.main.requirements_analyzer.create_chat_model",
                        new=_model_factory(
                            request="创建一个生产旅程应用",
                            calls=model_calls,
                            failed_model="DeepSeek",
                        ),
                    ),
                    patch(
                        "app.agents.main.product_planner.create_chat_model",
                        new=_model_factory(
                            request="创建一个生产旅程应用",
                            calls=model_calls,
                            failed_model=None,
                        ),
                    ),
                ):
                    await _consume(
                        build_workflow_ag_ui_stream(
                            graph=application_planning_graph_for_request,
                            payload=_planning_payload(
                                workspace,
                                thread_id=thread_id,
                                run_id=source_run_id,
                                project_id=project_id,
                            ),
                        )
                    )
                    source = await get_execution(workspace, source_run_id)
                    self.assertIsNotNone(source)
                    assert source is not None
                    self.assertEqual(source.status, DurableExecutionStatus.FAILED)
                    self.assertEqual(source.current_node, "requirements")
                    self.assertIsNotNone(source.failure)
                    assert source.failure is not None
                    self.assertEqual(source.failure.operation, "requirements")
                    self.assertEqual(source.failure.http_status, 404)
                    source_boundary = await get_node_entry_boundary(
                        workspace,
                        source_run_id=source_run_id,
                        thread_id=thread_id,
                        target_node="requirements",
                    )
                    self.assertIsNotNone(source_boundary)
                    assert source_boundary is not None
                    source_graph = await application_planning_graph_for_request(
                        workspace=str(workspace),
                        project_id=project_id,
                    )
                    source_snapshot = await source_graph.aget_state(
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": source_boundary.checkpoint_ns,
                                "checkpoint_id": source_boundary.checkpoint_id,
                            }
                        }
                    )
                    self.assertEqual(tuple(source_snapshot.next), ("requirements",))

                    model_config["name"] = "MiMo"
                    child = await _execute_current_recovery_action(
                        workspace,
                        run_id=source_run_id,
                    )
                    self.assertNotEqual(child.run_id, source_run_id)
                    self.assertEqual(child.first_node, "requirements")
                    self.assertNotEqual(child.status, DurableExecutionStatus.FAILED)
                    child_boundary = await get_node_entry_boundary(
                        workspace,
                        source_run_id=child.run_id,
                        thread_id=thread_id,
                        target_node="requirements",
                    )
                    self.assertIsNotNone(child_boundary)
                    assert child_boundary is not None
                    child_snapshot = await source_graph.aget_state(
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": child_boundary.checkpoint_ns,
                                "checkpoint_id": child_boundary.checkpoint_id,
                            }
                        }
                    )
                    self.assertEqual(tuple(child_snapshot.next), ("requirements",))
            finally:
                clear_application_planning_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )

        self.assertEqual(
            [model for model, kind in model_calls if kind == "requirements"],
            ["DeepSeek", "MiMo"],
        )

    async def test_c6_formal_revision_interrupted_continue_preserves_context_after_backend_restart(
        self,
    ) -> None:
        """Formal Revision R1 在 Requirements 阻塞时中断，恢复仍只进入同一 target。"""

        model_calls: list[tuple[str, str]] = []
        dependency_started = threading.Event()
        dependency_release = threading.Event()
        dependency_finished = threading.Event()
        runtime_task: asyncio.Task[list[str]] | None = None
        project_id = "p0-4g-formal-revision-app"
        thread_id = "p0-4g-formal-revision-thread"
        baseline_run_id = "p0-4g-formal-baseline"
        source_run_id = "p0-4g-formal-source"
        change_request = "修改首页展示核心信息"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            graph, _requirement_spec, _product_plan = await _seed_planning_checkpoint(
                workspace,
                thread_id=thread_id,
                run_id=baseline_run_id,
                project_id=project_id,
            )
            try:
                # 这里只替换 ChangeImpactAnalyzer 的只读模型结果；Analyzer 本身、impact
                # 登记、确认消费、Formal Revision 创建和 public start action 均走真实协议。
                corpus = load_confirmed_contract_corpus(workspace)
                homepage_fact = next(
                    fact
                    for fact in corpus.facts
                    if fact.artifact_key == "requirement-spec"
                    and "首页" in fact.existing_fact
                )
                impact_model = _ImpactModel(
                    {
                        "analysisStatus": "completed",
                        "requestSummary": change_request,
                        "atomicChanges": [
                            {
                                "changeId": "C1",
                                "requestedChange": change_request,
                                "contractImpact": "invalidates",
                                "contractEvidence": [
                                    {
                                        **homepage_fact.reference(),
                                        "requestedChange": change_request,
                                        "conflictRelation": "modifies",
                                        "reason": "首页展示内容发生变化。",
                                    }
                                ],
                            }
                        ],
                    }
                )
                impact_analysis = ChangeImpactAnalyzer(model=impact_model).analyze(
                    change_request,
                    workspace,
                    target={"type": "application"},
                )
                self.assertEqual(impact_model.calls, 1)
                self.assertEqual(
                    impact_analysis.earliest_affected_contract_stage.value,
                    "requirement_design",
                )
                impact = RevisionImpact(
                    formalBranch=FormalRevisionBranch.DESIGN_STAGE_REVISION,
                    revisionType="requirement_scope_change",
                    earliestArtifact=EarliestRevisionArtifact.REQUIREMENT_SPEC,
                    affectedArtifacts=[
                        "requirement-spec",
                        "product-plan",
                        "ui-design",
                        "technical-plan",
                    ],
                    affectedResources=["application"],
                    reason="首页修改影响需求及全部下游设计产物。",
                )
                pending = register_revision_impact(
                    workspace,
                    interaction_id="p0-4g-impact",
                    source_thread_id=thread_id,
                    source_run_id=baseline_run_id,
                    request=change_request,
                    target=RevisionTarget(type="application"),
                    impact=impact,
                )
                start_payload = {
                    "threadId": thread_id,
                    "runId": source_run_id,
                    "projectId": project_id,
                    "sessionId": f"{thread_id}-session",
                    "request": change_request,
                    "forwardedProps": {
                        "workspaceRoot": str(workspace),
                        "workflowAction": "start_design_revision",
                        "revisionRequest": {
                            "source": "conversation_handoff",
                            "formalBranch": "design_stage_revision",
                            "target": {"type": "application"},
                            "request": change_request,
                            "confirmedImpact": {"interactionId": "p0-4g-impact"},
                        },
                    },
                }
                with (
                    patch(
                        "app.config.Settings.from_env",
                        side_effect=lambda: _settings_for("MiMo"),
                    ),
                    patch(
                        "app.agents.main.requirements_analyzer.create_chat_model",
                        new=_blocking_model_factory(
                            request=change_request,
                            calls=model_calls,
                            dependency_started=dependency_started,
                            dependency_release=dependency_release,
                            dependency_finished=dependency_finished,
                        ),
                    ),
                    patch(
                        "app.agents.main.product_planner.create_chat_model",
                        new=_model_factory(
                            request=change_request,
                            calls=model_calls,
                            failed_model=None,
                        ),
                    ),
                ):
                    runtime_task = asyncio.create_task(
                        _consume(
                            build_application_page_planning_ag_ui_stream(
                                graph=application_planning_graph_for_request,
                                payload=start_payload,
                            )
                        )
                    )
                    self.assertTrue(
                        await asyncio.wait_for(
                            asyncio.to_thread(dependency_started.wait, 5),
                            timeout=10,
                        )
                    )
                    # Analyzer transport 的信号先于 Runtime mirror 更新，等待同一
                    # execution 的 current_node 稳定为目标节点再模拟 owner loss。
                    running = None
                    for _ in range(100):
                        running = await get_execution(workspace, source_run_id)
                        if (
                            running is not None
                            and running.status is DurableExecutionStatus.RUNNING
                            and running.current_node == "requirements"
                        ):
                            break
                        await asyncio.sleep(0.01)
                    self.assertIsNotNone(running)
                    assert running is not None
                    self.assertEqual(running.status, DurableExecutionStatus.RUNNING)
                    self.assertEqual(
                        running.current_node,
                        "requirements",
                        f"status={running.status} task_done={runtime_task.done()}",
                    )

                    source_snapshot = await graph.aget_state(
                        {"configurable": {"thread_id": thread_id}}
                    )
                    self.assertEqual(tuple(source_snapshot.next), ("requirements",))
                    self.assertEqual(
                        source_snapshot.values.get("request"),
                        change_request,
                    )
                    source_context_digest = semantic_context_sha256(
                        dict(source_snapshot.values)
                    )

                    lifecycle = load_application_lifecycle(workspace)
                    self.assertIsNotNone(lifecycle)
                    assert lifecycle is not None
                    self.assertIsNotNone(lifecycle.active_formal_revision)
                    assert lifecycle.active_formal_revision is not None
                    change_id = lifecycle.active_formal_revision.change_id
                    revision_target = lifecycle.active_formal_revision.target.model_dump(
                        mode="json"
                    )
                    revision_request = lifecycle.active_formal_revision.request
                    self.assertEqual(
                        lifecycle.active_formal_revision.current_artifact,
                        "requirement-spec",
                    )
                    self.assertEqual(
                        lifecycle.active_formal_revision.impact_interaction_id,
                        pending.interaction_id,
                    )

                    # 新 Backend 看不到旧 owner，且读取时间已越过旧 lease，模拟 owner loss。
                    scan = await reconcile_workspace_recovery(
                        workspace,
                        locally_active_run_ids=set(),
                        current_backend_instance_id="backend-after-c6-restart",
                        now=datetime.now(timezone.utc) + timedelta(days=1),
                    )
                    self.assertEqual(scan.interrupted_run_ids, [source_run_id])
                    source_after_scan = await get_execution(workspace, source_run_id)
                    self.assertIsNotNone(source_after_scan)
                    assert source_after_scan is not None
                    self.assertEqual(
                        source_after_scan.status,
                        DurableExecutionStatus.INTERRUPTED,
                    )
                    self.assertEqual(source_after_scan.current_node, "requirements")

                    # 旧 Runtime 被杀死后只释放阻塞 transport，不把旧执行当成恢复动作。
                    runtime_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await runtime_task
                    runtime_task = None
                    dependency_release.set()
                    self.assertTrue(
                        await asyncio.wait_for(
                            asyncio.to_thread(dependency_finished.wait, 5),
                            timeout=10,
                        )
                    )

                    clear_application_planning_graph_cache()
                    self.assertTrue(
                        await close_workflow_checkpointer_for_workspace(
                            workspace=str(workspace),
                            project_id=project_id,
                        )
                    )
                    restarted_graph = await application_planning_graph_for_request(
                        workspace=str(workspace),
                        project_id=project_id,
                    )
                    self.assertIsNot(restarted_graph, graph)
                    action_plan = await _read_current_continue_action(
                        workspace,
                        run_id=source_run_id,
                    )
                    self.assertEqual(
                        action_plan["primaryAction"]["kind"],
                        RecoveryActionKind.CONTINUE_CHECKPOINT.value,
                    )
                    child = await _execute_current_continue_action(
                        workspace,
                        run_id=source_run_id,
                    )
                    self.assertNotEqual(child.run_id, source_run_id)
                    self.assertEqual(child.first_node, "requirements")
                    self.assertNotIn(child.first_node, {"START", "workflow_entry"})

                    child_boundary = await get_node_entry_boundary(
                        workspace,
                        source_run_id=child.run_id,
                        thread_id=thread_id,
                        target_node="requirements",
                    )
                    self.assertIsNotNone(child_boundary)
                    assert child_boundary is not None
                    child_snapshot = await restarted_graph.aget_state(
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": child_boundary.checkpoint_ns,
                                "checkpoint_id": child_boundary.checkpoint_id,
                            }
                        }
                    )
                    self.assertEqual(tuple(child_snapshot.next), ("requirements",))
                    self.assertEqual(
                        source_context_digest,
                        semantic_context_sha256(dict(child_snapshot.values)),
                    )
                    final_lifecycle = load_application_lifecycle(workspace)
                    self.assertIsNotNone(final_lifecycle)
                    assert final_lifecycle is not None
                    self.assertIsNone(final_lifecycle.pending_revision_impact)
                    self.assertIsNotNone(final_lifecycle.active_formal_revision)
                    assert final_lifecycle.active_formal_revision is not None
                    # 同一 active formal revision 仍是唯一 R1，不产生第二个 revision identity。
                    self.assertEqual(
                        final_lifecycle.active_formal_revision.change_id,
                        change_id,
                    )
                    self.assertEqual(
                        final_lifecycle.active_formal_revision.target.model_dump(mode="json"),
                        revision_target,
                    )
                    self.assertEqual(
                        final_lifecycle.active_formal_revision.request,
                        revision_request,
                    )
                    self.assertEqual(
                        final_lifecycle.active_formal_revision.source_run_id,
                        baseline_run_id,
                    )
                    self.assertEqual(impact_model.calls, 1)
                    self.assertEqual(
                        [kind for _model, kind in model_calls if kind == "requirements"],
                        ["requirements", "requirements"],
                    )
                    source_after_recovery = await get_execution(workspace, source_run_id)
                    self.assertIsNotNone(source_after_recovery)
                    assert source_after_recovery is not None
                    self.assertEqual(
                        source_after_recovery.status,
                        DurableExecutionStatus.INTERRUPTED,
                    )
            finally:
                dependency_release.set()
                if runtime_task is not None:
                    if not runtime_task.done():
                        runtime_task.cancel()
                    try:
                        await runtime_task
                    except asyncio.CancelledError:
                        pass
                clear_application_planning_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )

    async def test_workbench_retries_prepare_build_tasks_without_rerunning_inspection(self) -> None:
        """真实 Workbench A→B 中 B 外部规划失败后 child 从 B 重入且 A 只执行一次。"""

        workspace_root = tempfile.TemporaryDirectory()
        self.addCleanup(workspace_root.cleanup)
        workspace = Path(workspace_root.name)
        project_id = "p0-4g-workbench-app"
        thread_id = "p0-4g-workbench-thread"
        source_run_id = "p0-4g-workbench-source"
        plan = project_plan()
        for key, payload in formal_artifacts(plan).items():
            write_json(
                workspace,
                {
                    "requirement_spec": ".xcodeagent/specs/requirement-spec.json",
                    "product_plan": ".xcodeagent/plans/product-plan.json",
                    "ui_designs": ".xcodeagent/specs/ui-designs.json",
                    "technical_plan": ".xcodeagent/plans/technical-plan.json",
                }[key],
                payload,
            )
        write_confirmed_endpoint_designs(workspace, plan)
        _write_planning_lifecycle(
            workspace,
            thread_id=thread_id,
            run_id=source_run_id,
            project_id=project_id,
            stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            status=ApplicationLifecycleStatus.COMPLETED,
        )
        generation_calls: list[str] = []
        inspection_calls: list[str] = []

        async def generate_once(job: Any, **_kwargs: Any) -> UnitGenerationAttemptResult:
            """只在 Unit generation 外部边界首次失败，child 调用时返回合法候选。"""

            generation_calls.append(job.identity.planning_run_id)
            if len(generation_calls) == 1:
                raise FakeModelConnectionError(model="MiMo", status_code=503)
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        def inspection_spy(*args: Any, **kwargs: Any) -> Any:
            """观察真实 inspect_workspace 外部扫描次数，不替换 production Node。"""

            inspection_calls.append("inspect_workspace")
            from app.services.workspace_inspector import inspect_workspace as real_inspect

            return real_inspect(*args, **kwargs)

        try:
            with (
                patch(
                    "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
                    return_value=_ready_template(workspace),
                ),
                patch(
                    "app.services.dag_planning_orchestrator.generate_unit_candidate_once",
                    new=generate_once,
                ),
                patch(
                    "app.graph.nodes.workspace_inspection.inspect_workspace_service",
                    new=inspection_spy,
                ),
            ):
                await _consume(
                    build_workflow_ag_ui_stream(
                        graph=workflow_graph_for_request,
                        payload={
                            "threadId": thread_id,
                            "runId": source_run_id,
                            "projectId": project_id,
                            "sessionId": "p0-4g-workbench-session",
                            "request": "开始构建生产旅程应用",
                            "resumeFrom": "inspect_workspace",
                            "forwardedProps": {
                                "workspaceRoot": str(workspace),
                                "application": {
                                    "id": project_id,
                                    "name": "生产旅程应用",
                                },
                            },
                        },
                    )
                )
                source = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(source.execution_kind, "workbench")
                self.assertEqual(source.status, DurableExecutionStatus.FAILED)
                self.assertEqual(source.current_node, "prepare_build_tasks")
                source_boundary = await get_node_entry_boundary(
                    workspace,
                    source_run_id=source_run_id,
                    thread_id=thread_id,
                    target_node="prepare_build_tasks",
                )
                self.assertIsNotNone(source_boundary)
                assert source_boundary is not None
                source_graph = await workflow_graph_for_request(
                    workspace=str(workspace),
                    project_id=project_id,
                )
                source_snapshot = await source_graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": source_boundary.checkpoint_ns,
                            "checkpoint_id": source_boundary.checkpoint_id,
                        }
                    }
                )
                self.assertEqual(tuple(source_snapshot.next), ("prepare_build_tasks",))

                clear_workflow_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )
                child = await _execute_current_recovery_action(
                    workspace,
                    run_id=source_run_id,
                )
                self.assertNotEqual(child.run_id, source_run_id)
                self.assertEqual(child.first_node, "prepare_build_tasks")
                self.assertNotEqual(child.status, DurableExecutionStatus.FAILED)
                child_boundary = await get_node_entry_boundary(
                    workspace,
                    source_run_id=child.run_id,
                    thread_id=thread_id,
                    target_node="prepare_build_tasks",
                )
                self.assertIsNotNone(child_boundary)
                assert child_boundary is not None
                child_graph = await workflow_graph_for_request(
                    workspace=str(workspace),
                    project_id=project_id,
                )
                child_snapshot = await child_graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": child_boundary.checkpoint_ns,
                            "checkpoint_id": child_boundary.checkpoint_id,
                        }
                    }
                )
                self.assertEqual(tuple(child_snapshot.next), ("prepare_build_tasks",))
        finally:
            clear_workflow_graph_cache()
            await close_workflow_checkpointer_for_workspace(
                workspace=str(workspace),
                project_id=project_id,
            )

        self.assertEqual(len(inspection_calls), 1)
        self.assertEqual(len(generation_calls), 2)
        self.assertNotEqual(generation_calls[0], generation_calls[1])

    async def test_backend_crash_reloads_durable_checkpoint_and_continues_node(self) -> None:
        """真实 Backend 崩溃窗口重启后从最新 checkpoint 继续 ui_confirmation Node。"""

        project_id = "p0-5-d-crash-app"
        thread_id = "p0-5-d-crash-thread"
        source_run_id = "p0-5-d-crash-source"
        dependency_started = threading.Event()
        dependency_release = threading.Event()
        dependency_finished = threading.Event()
        runtime_task: asyncio.Task[list[str]] | None = None

        def blocked_setup(workspace_root: str) -> dict[str, str]:
            """在真实 ui_confirmation Node 的最低 setup 依赖处制造未完成窗口。"""

            dependency_started.set()
            dependency_release.wait(timeout=30)
            dependency_finished.set()
            return {
                "project_dir": str(Path(workspace_root) / ".xcodeagent" / "ui-design")
            }

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            old_graph, _, _ = await _seed_planning_checkpoint(
                workspace,
                thread_id=thread_id,
                run_id=source_run_id,
                project_id=project_id,
                lifecycle_stage=ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
                lifecycle_status=ApplicationLifecycleStatus.RUNNING,
            )
            try:
                with patch(
                    "app.graph.nodes.ui_confirmation.setup_ui_design_project",
                    side_effect=blocked_setup,
                ):
                    runtime_task = asyncio.create_task(
                        _consume(
                            build_workflow_ag_ui_stream(
                                graph=application_planning_graph_for_request,
                                payload=_planning_payload(
                                    workspace,
                                    thread_id=thread_id,
                                    run_id=source_run_id,
                                    project_id=project_id,
                                    resume_from="ui_confirmation",
                                ),
                            )
                        )
                    )
                    self.assertTrue(
                        await asyncio.wait_for(
                            asyncio.to_thread(dependency_started.wait, 5),
                            timeout=10,
                        )
                    )
                    running = await get_execution(workspace, source_run_id)
                    self.assertIsNotNone(running)
                    assert running is not None
                    self.assertEqual(running.status, DurableExecutionStatus.RUNNING)
                    self.assertEqual(running.current_node, "ui_confirmation")

                    # 新 Backend 看不到旧 owner，且读取时间已经越过旧 lease，模拟 owner 消失。
                    scan = await reconcile_workspace_recovery(
                        workspace,
                        locally_active_run_ids=set(),
                        current_backend_instance_id="backend-after-restart",
                        now=datetime.now(timezone.utc) + timedelta(days=1),
                    )
                    self.assertEqual(scan.interrupted_run_ids, [source_run_id])
                    source_after_scan = await get_execution(workspace, source_run_id)
                    self.assertIsNotNone(source_after_scan)
                    assert source_after_scan is not None
                    self.assertEqual(
                        source_after_scan.status,
                        DurableExecutionStatus.INTERRUPTED,
                    )

                    # 旧 runtime 的取消只代表旧进程已经死亡；不把它当成恢复动作。
                    runtime_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await runtime_task
                    runtime_task = None
                    dependency_release.set()
                    self.assertTrue(
                        await asyncio.wait_for(
                            asyncio.to_thread(dependency_finished.wait, 5),
                            timeout=10,
                        )
                    )

                points = await list_recovery_points(workspace, source_run_id)
                entry = next(
                    point
                    for point in reversed(points)
                    if point.completed_node is None
                    and point.next_nodes == ["ui_confirmation"]
                    and point.checkpoint_id is not None
                )
                self.assertEqual(entry.thread_id, thread_id)
                self.assertEqual(entry.checkpoint_ns, "")

                # 必须清掉旧 Graph/cache 并关闭旧 SQLite connection，随后才模拟新 Backend。
                clear_application_planning_graph_cache()
                self.assertTrue(
                    await close_workflow_checkpointer_for_workspace(
                        workspace=str(workspace),
                        project_id=project_id,
                    )
                )
                restarted_graph = await application_planning_graph_for_request(
                    workspace=str(workspace),
                    project_id=project_id,
                )
                self.assertIsNot(restarted_graph, old_graph)

                restarted_source = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(restarted_source)
                assert restarted_source is not None
                self.assertEqual(
                    restarted_source.status,
                    DurableExecutionStatus.INTERRUPTED,
                )
                self.assertEqual(restarted_source.current_node, "ui_confirmation")
                checkpoint = await restarted_graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": entry.checkpoint_ns,
                            "checkpoint_id": entry.checkpoint_id,
                        }
                    }
                )
                self.assertEqual(tuple(checkpoint.next), ("ui_confirmation",))

                projection = await resolve_execution_recovery_projection(str(workspace))
                candidate = next(
                    item
                    for item in projection.candidates
                    if item.source_run_id == source_run_id
                )
                assert candidate.recovery_action_plan is not None
                action_plan = candidate.recovery_action_plan.model_dump(
                    mode="json",
                    by_alias=True,
                )
                assert action_plan["primaryAction"] is not None
                self.assertEqual(
                    action_plan["primaryAction"]["kind"],
                    RecoveryActionKind.CONTINUE_CHECKPOINT.value,
                )
                recovery_frames = await _consume(
                    build_execution_recovery_ag_ui_stream(
                        payload={
                            "forwardedProps": {
                                "workspaceRoot": str(workspace),
                                "executionRecovery": {
                                    "action": "execute",
                                    "incidentId": action_plan["incidentId"],
                                    "actionId": action_plan["primaryAction"]["actionId"],
                                },
                            }
                        }
                    )
                )
                self.assertFalse(any("RUN_ERROR" in frame for frame in recovery_frames))
                resolution = await resolve_recovery_lineage_head(
                    str(workspace),
                    thread_id=thread_id,
                    execution_kind="application_planning",
                )
                assert resolution.head is not None
                self.assertNotEqual(resolution.head.run_id, source_run_id)
                self.assertEqual(resolution.head.first_node, "ui_confirmation")
            finally:
                dependency_release.set()
                if runtime_task is not None:
                    if not runtime_task.done():
                        runtime_task.cancel()
                    try:
                        await runtime_task
                    except asyncio.CancelledError:
                        pass
                clear_application_planning_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )

    async def test_technical_planning_business_failure_uses_native_repair_interaction(
        self,
    ) -> None:
        """真实 TechnicalPlan 生成失败必须停在原生 repair interaction 而非 FAILED。"""

        project_id = "p0-5-e-business-failure-app"
        thread_id = "p0-5-e-business-failure-thread"
        source_run_id = "p0-5-e-business-failure-source"
        model_calls: list[str] = []
        request = "创建一个生产旅程应用"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            graph, _requirement_spec, _product_plan = await _seed_planning_checkpoint(
                workspace,
                thread_id=thread_id,
                run_id=source_run_id,
                project_id=project_id,
                lifecycle_stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
                lifecycle_status=ApplicationLifecycleStatus.RUNNING,
            )
            await graph.aupdate_state(
                {"configurable": {"thread_id": thread_id}},
                {
                    "active_thread_id": thread_id,
                    "active_run_id": source_run_id,
                    "workflow_scope": "application_planning",
                    "phase": "technical_planning_generate",
                    "status": "running",
                    "application_planning_recovery_boundary": application_planning_boundary_payload(
                        operation_id="p0-5-e-technical-plan",
                        operation=ApplicationPlanningOperation.INITIAL,
                        boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
                        request=request,
                    ),
                },
            )
            try:
                with patch(
                    "app.agents.main.planner.create_chat_model",
                    new=_invalid_technical_plan_model_factory(model_calls),
                ):
                    frames = await _consume(
                        build_workflow_ag_ui_stream(
                            graph=application_planning_graph_for_request,
                            payload=_planning_payload(
                                workspace,
                                thread_id=thread_id,
                                run_id=source_run_id,
                                project_id=project_id,
                                resume_from="technical_planning_generate",
                            ),
                        )
                    )

                execution = await get_execution(workspace, source_run_id)
                self.assertIsNotNone(execution)
                assert execution is not None
                self.assertNotEqual(execution.status, DurableExecutionStatus.FAILED)
                self.assertEqual(
                    execution.status,
                    DurableExecutionStatus.AWAITING_USER,
                )
                self.assertIsNone(execution.failure)
                self.assertEqual(model_calls, ["technical_planning_generate"] * 3)

                text = "".join(frames)
                self.assertIn('"type":"RUN_FINISHED"', text)
                self.assertNotIn('"type":"RUN_ERROR"', text)
                snapshot = await graph.aget_state(
                    {"configurable": {"thread_id": thread_id}}
                )
                interaction = application_planning_interrupt_from_snapshot(snapshot)
                self.assertIsNotNone(interaction)
                assert interaction is not None
                self.assertEqual(interaction["type"], "application_planning_review")
                self.assertEqual(interaction["artifact"], "technical_plan")
                self.assertEqual(interaction["phase"], "technical_planning")
                self.assertEqual(
                    interaction["clarification"]["mode"],
                    "technical_plan_generation_error",
                )
                self.assertEqual(
                    interaction["clarification"]["status"],
                    "requires_user_input",
                )

                recovery_plan = await prepare_continue(
                    workspace=str(workspace),
                    source_run_id=source_run_id,
                    graph=graph,
                    replay_policies=production_recovery_replay_policies(),
                )
                self.assertEqual(
                    recovery_plan.decision,
                    RecoveryDecision.AWAITING_USER,
                )
                projection = await resolve_execution_recovery_projection(
                    str(workspace)
                )
                self.assertFalse(
                    any(
                        candidate.source_run_id == source_run_id
                        for candidate in projection.candidates
                    )
                )
            finally:
                clear_application_planning_graph_cache()
                await close_workflow_checkpointer_for_workspace(
                    workspace=str(workspace),
                    project_id=project_id,
                )
