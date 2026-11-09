from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import Body, FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.environment import load_backend_environment
from app.observability.backend_logs import install_backend_log_capture

# 在加载业务模块前接入开发服务和冻结后端的统一日志窗口。
install_backend_log_capture()

from app.graph import clear_workflow_graph_cache, workflow_graph_for_request
from app.graph.application_planning_workflow import (
    application_planning_graph_for_request,
    clear_application_planning_graph_cache,
)
from app.graph.direct_modification_workflow import clear_direct_modification_graph_cache
from app.middleware.approvals import approval_store
from app.persistence.checkpoints import close_workflow_checkpointer
from app.protocols.preview_runtime import preview_runtime_capabilities, build_preview_runtime_stream
from app.protocols.builtin_authorization import router as builtin_authorization_router, builtin_authorization_capabilities
from app.protocols.agent_files import (
    agent_files_capabilities,
    build_agent_files_ag_ui_stream,
)
from app.protocols.application_development_planning import (
    application_development_planning_capabilities,
    build_application_development_planning_ag_ui_stream,
)
from app.protocols.application_deletion import (
    ApplicationDeletionCompletionRequest,
    ApplicationDeletionRequest,
    application_deletion_capabilities,
    build_application_deletion_ag_ui_stream,
    complete_application_deletion,
    prepare_application_deletion,
)
from app.protocols.application_lifecycle import (
    application_lifecycle_capabilities,
    build_application_lifecycle_ag_ui_stream,
)
from app.protocols.application_config_operation import (
    application_config_capabilities,
    build_application_config_ag_ui_stream,
)
from app.protocols.application_page_planning import (
    application_page_planning_capabilities,
    build_application_page_planning_ag_ui_stream,
)
from app.protocols.code_changes import (
    build_code_changes_ag_ui_stream,
    code_changes_capabilities,
)
from app.protocols.data_sources import data_sources_capabilities
from app.protocols.endpoint_designs import endpoint_designs_capabilities
from app.routes.data_sources import data_sources_router
from app.routes.endpoint_designs import endpoint_designs_router
from app.routes.workflow_artifacts import workflow_artifacts_router, artifact_capabilities
from app.routes.page_artifacts import page_artifacts_router, page_artifacts_capabilities
from app.routes.entity_tables import entity_tables_router
from app.routes.telemetry import telemetry_router
from app.routes.backend_logs import backend_logs_router
from app.protocols.backend_logs import backend_logs_capabilities
from app.protocols.ui_design_generation import build_ui_design_generation_stream, ui_design_generation_capabilities
from app.protocols.telemetry import telemetry_capabilities
from app.protocols.app_update import app_update_capabilities, build_app_update_ag_ui_stream
from app.protocols.entity_tables import entity_tables_capabilities
from app.protocols.revision_preview import (
    build_revision_preview_ag_ui_stream,
    revision_preview_capabilities,
)
from app.protocols.version_control import (
    build_version_control_ag_ui_stream,
    version_control_capabilities,
)
from app.protocols.version_publish import (
    build_version_publish_ag_ui_stream,
    version_publish_capabilities,
)
from app.protocols.repository_branch import (
    build_repository_branch_ag_ui_stream,
    repository_branch_capabilities,
)
from app.protocols.iteration_service import (
    build_iteration_service_ag_ui_stream,
    iteration_service_capabilities,
)
from app.protocols.direct_modification import (
    build_conversation_ag_ui_stream,
    conversation_capabilities,
)
from app.protocols.temporary_chat import (
    build_temporary_chat_ag_ui_stream,
    temporary_chat_capabilities,
)
from app.protocols.execution_recovery import (
    build_execution_recovery_ag_ui_stream,
    execution_recovery_capabilities,
)
from app.protocols.build_task_retry import build_task_retry_stream, build_task_retry_capabilities
from app.protocols.user_skills import (
    build_user_skills_ag_ui_stream,
    user_skills_capabilities,
)
from app.protocols.workflow import (
    build_workflow_ag_ui_stream,
    workflow_capabilities,
)
from app.services.detached_execution import build_detached_execution_stream, shutdown_detached_executions
from app.services.backend_runtime import runtime as backend_runtime, runtime_busy_count
from app.protocols.backend_runtime import build_backend_runtime_stream
from app.middleware.backend_runtime import BackendRuntimeAdmission
from app.services.agent_file_documents import ensure_agents_document
from app.services.builtin_skills import available_builtin_skills
from app.services.database_crypto import (
    database_encryption_metadata,
    ensure_database_platform_key,
)
from app.services.backend_instance import current_backend_instance, initialize_backend_instance
from app.services.workspace_bootstrap.service import workspace_bootstrap_service
from app.tools import database_tools
from app.workspace import workspace as workspace_tools

settings = Settings.from_env()
bootstrap_service = workspace_bootstrap_service(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """初始化平台级持久资源，并在退出时释放工作流缓存。"""

    from app.services.builtin_authorization_orchestrator import start_builtin_authorization_tasks

    start_builtin_authorization_tasks()
    ensure_database_platform_key()
    ensure_agents_document()
    backend_instance = initialize_backend_instance()
    logging.getLogger("uvicorn.error").info(
        "backend.instance.started instanceId=%s pid=%s startedAt=%s",
        backend_instance.instance_id,
        backend_instance.pid,
        backend_instance.started_at.isoformat(),
    )
    try:
        await backend_runtime.start(lambda: runtime_busy_count(bootstrap_service))
        yield
    finally:
        await backend_runtime.close()
        from app.services.builtin_authorization_orchestrator import shutdown_builtin_authorization_tasks

        await asyncio.to_thread(shutdown_builtin_authorization_tasks)
        await shutdown_detached_executions()
        if backend_runtime.managed:
            from app.services.frontend_project_launcher import stop_owned_frontend_previews
            from app.services.backend_process_registry import stop_owned_backend_previews
            await asyncio.to_thread(stop_owned_frontend_previews)
            await asyncio.to_thread(stop_owned_backend_previews)
        clear_workflow_graph_cache()
        clear_application_planning_graph_cache()
        clear_direct_modification_graph_cache()
        await close_workflow_checkpointer()


app = FastAPI(
    title="Local LangGraph Agent",
    description="A minimal local FastAPI backend powered by LangGraph and an OpenAI-compatible model.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(BackendRuntimeAdmission)
app.include_router(data_sources_router)
app.include_router(endpoint_designs_router)
app.include_router(workflow_artifacts_router)
app.include_router(page_artifacts_router)
app.include_router(entity_tables_router)
app.include_router(builtin_authorization_router)
app.include_router(telemetry_router)
app.include_router(backend_logs_router)


class ApprovalActionRequest(BaseModel):
    scope: Literal["once", "operation"] = Field(default="once")
    reason: Optional[str] = Field(default=None)


@app.get("/health")
async def health() -> dict[str, object]:
    """返回后端健康状态、基础设施能力和数据库加密公钥。"""

    return {
        "status": "ok",
        "app_env": load_backend_environment().app_env,
        "backendInstanceId": current_backend_instance().instance_id,
        "backendPid": current_backend_instance().pid,
        "managedRuntime": backend_runtime.metadata(),
        "provider": settings.provider_api_name,
        "model_transport": settings.model_transport,
        "model": settings.model_api_name,
        "configured_model": settings.model_name,
        "base_url": settings.model_transmit_url if settings.model_transport == "transmit" else settings.model_base_url,
        "model_transmit": ({
            "url": settings.model_transmit_url,
            "transport": "ag-ui-sse",
            "modelInfoEndpoint": "/model-info/run",
            "inferenceEndpoint": "/model-inference/run",
        } if settings.model_transport == "transmit" else None),
        "builtin_skills": available_builtin_skills(),
        "observability": {
            "langsmith": {
                "enabled": settings.langsmith_tracing_enabled,
                "project": settings.langsmith_project,
                "endpoint": settings.langsmith_endpoint,
            }
        },
        "database_encryption": database_encryption_metadata(),
        "tools": {
            "workflow_run": workflow_capabilities(),
            "background_execution": {"submissionIdentity": "runId", "survivesFrontendDisconnect": True,
                "deduplication": "workspace-request-content", "outputRecovery": "application-lifecycle.get/recoverySnapshot.output",
                "confirmation": "original-business-gates", "backendRestart": "explicit-recovery"},
            "workflow_artifacts": artifact_capabilities(),
            "application_page_planning": application_page_planning_capabilities(),
            "application_lifecycle": application_lifecycle_capabilities(),
            "application_config": application_config_capabilities(),
            "application_development_planning": application_development_planning_capabilities(),
            "application_deletion": application_deletion_capabilities(),
            "user_skills": user_skills_capabilities(),
            "agent_files": agent_files_capabilities(),
            "builtin_authorization": builtin_authorization_capabilities(),
            "preview_runtime": preview_runtime_capabilities(),
            "backend_runtime": {"endpoint": "/backend-runtime/run", "transport": "ag-ui-sse",
                "actionField": "forwardedProps.backendRuntime", "customEventName": "backend-runtime",
                "stateSnapshotKey": "backendRuntime", "workflowIndependent": True,
                "actions": ["attach", "detach", "prepare_exit", "commit_exit", "cancel_exit"]},
            "data_sources": data_sources_capabilities(),
            "endpoint_designs": endpoint_designs_capabilities(),
            "entity_tables": entity_tables_capabilities(),
            "telemetry": telemetry_capabilities(),
            "app_update": app_update_capabilities(),
            "backend_logs": backend_logs_capabilities(),
            "ui_design_generation": ui_design_generation_capabilities(),
            "code_changes": code_changes_capabilities(),
            "revision_preview": revision_preview_capabilities(),
            "version_control": version_control_capabilities(),
            "version_publish": version_publish_capabilities(),
            "repository_branch": repository_branch_capabilities(),
            "iteration_service": iteration_service_capabilities(),
            "conversation": conversation_capabilities(),
            "temporary_chat": temporary_chat_capabilities(),
            "execution_recovery": execution_recovery_capabilities(),
            "build_task_retry": build_task_retry_capabilities(),
            "workspace": workspace_tools.capabilities(),
            "page_artifacts": page_artifacts_capabilities(),
        },
    }


@app.post("/app-update/run")
async def run_app_update(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """登录前通过 AG-UI 检测版本，模板服务地址仅从后端配置读取。"""

    return StreamingResponse(
        build_app_update_ag_ui_stream(payload=input_data, settings=settings, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/backend-runtime/run")
async def manage_backend_runtime(input_data: dict[str, Any] = Body(...),
                                 accept: Optional[str] = Header(default="text/event-stream")) -> StreamingResponse:
    """提供独立 AG-UI 后台接管及退出动作，不进入应用工作流。"""
    return StreamingResponse(build_backend_runtime_stream(input_data, accept), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/application-page-planning/run")
async def run_application_page_planning(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """后端持有规划写任务，前端连接只消费原 AG-UI 输出。"""
    return StreamingResponse(
        build_detached_execution_stream(payload=input_data, endpoint='/application-page-planning/run',
            source=lambda execution_payload: build_application_page_planning_ag_ui_stream(
            graph=application_planning_graph_for_request,
            payload=execution_payload,
            accept=accept,
        )),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/application-config/run")
async def run_application_config(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 动作流预览、提交或读取应用配置操作。"""

    return StreamingResponse(
        build_application_config_ag_ui_stream(payload=input_data, settings=settings, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/build-task-retry/run")
async def run_build_task_retry(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 动作重新调度指定失败任务。"""
    return StreamingResponse(build_task_retry_stream(payload=input_data, accept=accept),
        media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/execution-recovery/execute")
async def execute_execution_recovery(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 端点执行后端签发的当前恢复动作。"""

    return StreamingResponse(
        build_detached_execution_stream(payload=input_data, endpoint='/execution-recovery/execute',
            source=lambda execution_payload: build_execution_recovery_ag_ui_stream(
            payload=execution_payload,
            accept=accept,
        )),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/application-lifecycle/run")
async def run_application_lifecycle(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """运行独立应用生命周期 AG-UI 动作。"""

    return StreamingResponse(
        build_application_lifecycle_ag_ui_stream(
            payload=input_data,
            accept=accept,
            bootstrap_service=bootstrap_service,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/application-development-planning/run")
async def run_application_development_planning(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """运行独立于主工作流的工作台应用开发计划 AG-UI 动作。"""

    return StreamingResponse(
        build_application_development_planning_ag_ui_stream(
            payload=input_data,
            accept=accept,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/application-deletion/run")
async def run_application_deletion(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """在 Electron 移动目录前执行工作区级应用销毁准备。"""

    return StreamingResponse(
        build_application_deletion_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/application-deletion/prepare")
async def prepare_application_deletion_direct(
        request: ApplicationDeletionRequest,
) -> dict[str, Any]:
    """供 Electron 主进程在移动目录前直接复用应用销毁准备门禁。"""

    return await prepare_application_deletion(request)


@app.post("/application-deletion/complete")
async def complete_application_deletion_direct(
        request: ApplicationDeletionCompletionRequest,
) -> dict[str, Any]:
    """供 Electron 在目录移入回收站后释放该路径的删除栅栏。"""

    return complete_application_deletion(request)


@app.post("/skills/run")
async def run_user_skills(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    return StreamingResponse(
        build_user_skills_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/preview-runtime/run")
async def run_preview_runtime(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 流管理开发预览、日志和诊断修复。"""
    return StreamingResponse(
        build_preview_runtime_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/agent-files/run")
async def run_agent_files(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    return StreamingResponse(
        build_agent_files_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/code-changes/run")
async def run_code_changes(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 流执行代码变更撤销。"""

    return StreamingResponse(
        build_code_changes_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/revision-preview/run")
async def run_revision_preview(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 流物化并启动历史版本的只读预览。"""

    return StreamingResponse(
        build_revision_preview_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/version-control/run")
async def run_version_control(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 流复核并显式提交二次修改。"""

    return StreamingResponse(
        build_version_control_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/version-publish/run")
async def run_version_publish(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 流执行版本发布：提交、打 Tag、推送到远程仓库。"""

    return StreamingResponse(
        build_version_publish_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/repository-branch/run")
async def run_repository_branch(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 流执行远端分支动作：检查远端是否已存在该分支。"""

    return StreamingResponse(
        build_repository_branch_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/iteration-service/run")
async def run_iteration_service(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 流发起新迭代：清空规划产物，保留 AGENTS.md。"""

    return StreamingResponse(
        build_iteration_service_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/conversation/run")
async def run_conversation(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """运行可回答常规问题、读取工作区或执行局部修改的自由对话 Graph。"""

    return StreamingResponse(
        build_detached_execution_stream(payload=input_data, endpoint='/conversation/run',
            source=lambda execution_payload: build_conversation_ag_ui_stream(payload=execution_payload, accept=accept)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/temporary-chat/run")
async def run_temporary_chat(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """运行独立只读临时对话 AG-UI 流。"""

    return StreamingResponse(
        build_temporary_chat_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/ag-ui")
async def ag_ui(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """Compatibility alias while the frontend migrates to /workflow/run."""
    return StreamingResponse(
        build_workflow_ag_ui_stream(
            graph=workflow_graph_for_request,
            payload=input_data,
            accept=accept,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/tools/workspace/capabilities")
async def workspace_capabilities() -> dict[str, Any]:
    return workspace_tools.capabilities()


@app.post("/tools/workspace/info")
async def workspace_info(request: workspace_tools.WorkspaceRequest) -> dict[str, Any]:
    return workspace_tools.workspace_info(request)


@app.post("/tools/workspace/list-files")
async def workspace_list_files(
        request: workspace_tools.ListFilesRequest,
) -> dict[str, Any]:
    return workspace_tools.list_files(request)


@app.post("/tools/workspace/tree")
async def workspace_tree(request: workspace_tools.TreeRequest) -> dict[str, Any]:
    return workspace_tools.workspace_tree(request)


@app.post("/tools/file/read")
async def file_read(request: workspace_tools.ReadFileRequest) -> dict[str, Any]:
    return workspace_tools.read_file(request)


@app.post("/tools/file/write")
async def file_write(request: workspace_tools.WriteFileRequest) -> dict[str, Any]:
    return workspace_tools.write_file(request)


@app.post("/tools/file/patch")
async def file_patch(request: workspace_tools.PatchFileRequest) -> dict[str, Any]:
    return workspace_tools.patch_file(request)


@app.post("/tools/file/delete")
async def file_delete(request: workspace_tools.DeleteFileRequest) -> dict[str, Any]:
    return workspace_tools.delete_file(request)


@app.post("/tools/search/files")
async def search_files(request: workspace_tools.SearchFilesRequest) -> dict[str, Any]:
    return workspace_tools.search_files(request)


@app.post("/tools/search/text")
async def search_text(request: workspace_tools.SearchTextRequest) -> dict[str, Any]:
    return workspace_tools.search_text(request)


@app.post("/tools/database/tables")
async def database_tables(
        request: database_tools.DatabaseTablesRequest,
) -> dict[str, Any]:
    return database_tools.database_tables(request)


@app.post("/tools/database/table-columns")
async def database_table_columns(
        request: database_tools.DatabaseTableColumnsRequest,
) -> dict[str, Any]:
    return database_tools.database_table_columns(request)


@app.post("/tools/terminal/exec")
async def terminal_exec(request: workspace_tools.TerminalExecRequest) -> dict[str, Any]:
    return workspace_tools.terminal_exec(request)


@app.post("/tools/approvals/{approval_id}/approve")
async def approve_tool_request(
        approval_id: str,
        request: ApprovalActionRequest = Body(default_factory=ApprovalActionRequest),
) -> dict[str, Any]:
    return approval_store.approve(approval_id, scope=request.scope)


@app.post("/tools/approvals/{approval_id}/reject")
async def reject_tool_request(
        approval_id: str,
        request: ApprovalActionRequest = Body(default_factory=ApprovalActionRequest),
) -> dict[str, Any]:
    return approval_store.reject(approval_id, reason=request.reason)


@app.post("/tools/git/status")
async def git_status(request: workspace_tools.GitStatusRequest) -> dict[str, Any]:
    return workspace_tools.git_status(request)


@app.post("/tools/git/diff")
async def git_diff(request: workspace_tools.GitDiffRequest) -> dict[str, Any]:
    return workspace_tools.git_diff(request)


@app.post("/workflow/run")
async def run_workflow(
        input_data: dict[str, Any] = Body(...),
        accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过持久提交身份启动或重新接入同一后台工作流。"""
    return StreamingResponse(
        build_detached_execution_stream(payload=input_data, endpoint='/workflow/run',
            source=lambda execution_payload: build_workflow_ag_ui_stream(
            graph=workflow_graph_for_request,
            payload=execution_payload,
            accept=accept,
        )),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/ui-design-generation/run")
async def run_ui_design_generation(input_data: dict[str, Any] = Body(...),
        accept: str | None = Header(default="text/event-stream")) -> StreamingResponse:
    """使用独立 AG-UI 动作等待单页后台生成真实退出。"""
    return StreamingResponse(build_ui_design_generation_stream(payload=input_data, accept=accept),
        media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# CORS 必须包在整个 FastAPI 应用外层，确保路由构造或未处理异常生成的 500 响应也携带跨域头。
app = CORSMiddleware(
    app=app,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|null)$",
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
