from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Body, FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.graph import clear_workflow_graph_cache, workflow_graph_for_request
from app.graph.application_planning_workflow import (
    application_planning_graph_for_request,
    clear_application_planning_graph_cache,
)
from app.graph.direct_modification_workflow import clear_direct_modification_graph_cache
from app.protocols.workflow import (
    build_workflow_ag_ui_stream,
    workflow_capabilities,
)
from app.protocols.application_page_planning import (
    application_page_planning_capabilities,
    build_application_page_planning_ag_ui_stream,
)
from app.protocols.application_lifecycle import (
    application_lifecycle_capabilities,
    build_application_lifecycle_ag_ui_stream,
)
from app.protocols.application_development_planning import (
    application_development_planning_capabilities,
    build_application_development_planning_ag_ui_stream,
)
from app.protocols.user_skills import (
    build_user_skills_ag_ui_stream,
    user_skills_capabilities,
)
from app.protocols.agent_files import (
    agent_files_capabilities,
    build_agent_files_ag_ui_stream,
)
from app.protocols.code_changes import (
    build_code_changes_ag_ui_stream,
    code_changes_capabilities,
)
from app.protocols.direct_modification import (
    build_direct_modification_ag_ui_stream,
    direct_modification_capabilities,
)
from app.config import Settings
from app.services.agent_file_documents import ensure_agents_document
from app.services.builtin_skills import available_builtin_skills
from app.services.project_launcher import (
    launch_backend_project,
    launch_frontend_project,
    stop_project_preview,
    stop_backend_project,
)
from app.workspace import workspace as workspace_tools
from app.middleware.approvals import approval_store
from app.persistence.checkpoints import close_workflow_checkpointer

settings = Settings.from_env()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_agents_document()
    try:
        yield
    finally:
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

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|null)$",
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApprovalActionRequest(BaseModel):
    scope: Literal["once", "operation"] = Field(default="once")
    reason: Optional[str] = Field(default=None)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "provider": settings.model_provider,
        "model": settings.model_api_name,
        "configured_model": settings.model_name,
        "base_url": settings.model_base_url,
        "builtin_skills": available_builtin_skills(),
        "observability": {
            "langsmith": {
                "enabled": settings.langsmith_tracing_enabled,
                "project": settings.langsmith_project,
                "endpoint": settings.langsmith_endpoint,
            }
        },
        "tools": {
            "workflow_run": workflow_capabilities(),
            "application_page_planning": application_page_planning_capabilities(),
            "application_lifecycle": application_lifecycle_capabilities(),
            "application_development_planning": application_development_planning_capabilities(),
            "user_skills": user_skills_capabilities(),
            "agent_files": agent_files_capabilities(),
            "code_changes": code_changes_capabilities(),
            "direct_modification": direct_modification_capabilities(),
            "workspace": workspace_tools.capabilities(),
        },
    }


@app.post("/application-page-planning/run")
async def run_application_page_planning(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    return StreamingResponse(
        build_application_page_planning_ag_ui_stream(
            graph=application_planning_graph_for_request,
            payload=input_data,
            accept=accept,
        ),
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


@app.post("/direct-modification/run")
async def run_direct_modification(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """运行独立于正式规划工作流的快速代码修改 Graph。"""

    return StreamingResponse(
        build_direct_modification_ag_ui_stream(payload=input_data, accept=accept),
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
    return StreamingResponse(
        build_workflow_ag_ui_stream(
            graph=workflow_graph_for_request,
            payload=input_data,
            accept=accept,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ProjectLaunchRequest(BaseModel):
    workspace: str = Field(min_length=1, max_length=4096)


@app.post("/api/projects/launch")
def api_launch_project(request: ProjectLaunchRequest) -> dict[str, Any]:
    """启动模板项目的后端与前端预览。

    用于在模板下载完成、进入工作区后自动启动开发服务器。
    执行顺序：先启后端（如果存在 backend/pom.xml），再启前端。
    FastAPI 自动将同步函数放在线程池中执行，不会阻塞事件循环。
    """

    root = Path(request.workspace).expanduser().resolve()
    backend_root = root / "backend"
    pom_path = backend_root / "pom.xml"
    has_backend_project = pom_path.is_file()

    backend: dict[str, Any] = {"status": "skipped", "message": "未找到后端项目，跳过。"}
    backend_process = None

    if has_backend_project:
        backend = launch_backend_project(root)
        backend_process = backend.pop("_process", None)
        if backend.get("status") == "failed":
            return {
                "status": "failed",
                "message": f"后端启动失败：{backend.get('message')}",
                "backend": backend,
                "frontend": None,
                "failed_stage": backend.get("failed_stage"),
            }

    frontend = launch_frontend_project(root)
    if frontend.get("status") == "failed":
        if backend_process is not None and has_backend_project:
            stop_backend_project(backend, backend_process)
        return {
            "status": "failed",
            "message": f"前端启动失败：{frontend.get('message')}",
            "backend": backend,
            "frontend": frontend,
            "failed_stage": "frontend_start",
        }

    return {
        "status": "running",
        "message": "后端与前端项目均已启动并就绪。",
        "preview_url": frontend.get("preview_url"),
        "backend": backend,
        "frontend": frontend,
    }


@app.post("/api/projects/stop")
def api_stop_project(request: ProjectLaunchRequest) -> dict[str, Any]:
    """停止指定工作区生成应用的前后端预览服务。"""

    return stop_project_preview(request.workspace)
