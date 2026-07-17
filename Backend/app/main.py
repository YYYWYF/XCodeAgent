from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.graph import clear_workflow_graph_cache, workflow_graph_for_request
from app.graph.application_planning_workflow import (
    application_planning_graph_for_request,
    clear_application_planning_graph_cache,
)
from app.protocols.workflow import (
    build_workflow_ag_ui_stream,
    workflow_capabilities,
)
from app.protocols.application_page_planning import (
    application_page_planning_capabilities,
    build_application_page_planning_ag_ui_stream,
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
from app.config import Settings
from app.services.agent_file_documents import ensure_agents_document
from app.services.builtin_skills import available_builtin_skills
from app.tools import antd_v4_docs
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
            "antd_v4_docs": {
                "available": antd_v4_docs.is_available(),
                "docs_dir": str(antd_v4_docs.docs_root()),
            },
            "workflow_run": workflow_capabilities(),
            "application_page_planning": application_page_planning_capabilities(),
            "application_development_planning": application_development_planning_capabilities(),
            "user_skills": user_skills_capabilities(),
            "agent_files": agent_files_capabilities(),
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


@app.get("/tools/antd-v4/components")
async def list_antd_v4_components() -> dict[str, object]:
    return {
        "version": "4.24.16",
        "components": antd_v4_docs.list_components(),
    }


@app.get("/tools/antd-v4/components/{slug}")
async def get_antd_v4_component(slug: str) -> dict[str, object]:
    try:
        return antd_v4_docs.get_component_doc(slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/tools/antd-v4/search")
async def search_antd_v4_docs(
    q: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=10),
    max_text_chars: int = Query(default=1200, ge=200, le=6000),
) -> dict[str, object]:
    results = antd_v4_docs.search(q, limit=limit)
    return {
        "version": "4.24.16",
        "query": q,
        "results": [
            antd_v4_docs.search_result_to_dict(result, max_text_chars=max_text_chars)
            for result in results
        ],
    }


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
