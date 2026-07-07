from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from ag_ui.core import RunAgentInput
from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.graph import graph
from app.protocols.ag_ui import build_ag_ui_stream
from app.protocols.workflow_visualization import (
    build_workflow_ag_ui_stream,
    workflow_capabilities,
)
from app.graph.agent import AgentRuntime
from app.config import Settings
from app.graph.orchestrator import (
    DevelopmentOrchestratorRuntime,
    orchestrator_capabilities,
)
from app.services.requirement_intake import intake_capabilities
from app.tools import antd_v4_docs
from app.workspace import workspace as workspace_tools
from app.middleware.approvals import approval_store
from app.agents.requirement_planner import (
    RequirementPlannerRuntime,
    planner_capabilities,
)

settings = Settings.from_env()
agent = AgentRuntime(settings)
planner = RequirementPlannerRuntime(settings)
orchestrator = DevelopmentOrchestratorRuntime(settings, planner)

app = FastAPI(
    title="Local LangGraph Agent",
    description="A minimal local FastAPI backend powered by LangGraph and an Anthropic-compatible model.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|null)$",
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: Annotated[
        str, Field(min_length=1, description="User input for the agent.")
    ]
    session_id: Optional[str] = Field(
        default=None,
        description="Optional conversation id. Reuse it to keep in-memory chat history.",
    )
    system_prompt: Optional[str] = Field(
        default=None, description="Optional prompt override."
    )
    workspace_root: Optional[str] = Field(
        default=None, description="Optional workspace root for local tools."
    )
    temperature: Optional[float] = Field(default=None, ge=0, le=1)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=8192)


class ChatResponse(BaseModel):
    session_id: str
    model: str
    answer: str
    messages: list[dict[str, str]]


class RequirementPlannerRequest(BaseModel):
    message: Annotated[
        str, Field(min_length=1, description="User requirement or answer.")
    ]
    action: str = Field(default="answer", description="start, answer, or finalize.")
    planner_state: Optional[dict[str, Any]] = Field(default=None)
    application: Optional[dict[str, Any]] = Field(default=None)


class DevelopmentOrchestratorRequest(BaseModel):
    message: Annotated[
        str,
        Field(
            min_length=1,
            description="User requirement, answer, or verification request.",
        ),
    ]
    action: str = Field(
        default="answer", description="start, answer, finalize, dispatch, or verify."
    )
    orchestrator_state: Optional[dict[str, Any]] = Field(default=None)
    planner_state: Optional[dict[str, Any]] = Field(default=None)
    application: Optional[dict[str, Any]] = Field(default=None)
    workspace_root: Optional[str] = Field(default=None)


class ApprovalActionRequest(BaseModel):
    scope: Literal["once", "operation"] = Field(default="once")
    reason: Optional[str] = Field(default=None)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "provider": settings.model_provider,
        "model": settings.model_api_name,
        "configured_model": settings.anthropic_model,
        "base_url": settings.anthropic_base_url,
        "builtin_skills": ["react-antd-v4-codegen"],
        "tools": {
            "antd_v4_docs": {
                "available": antd_v4_docs.is_available(),
                "docs_dir": str(antd_v4_docs.docs_root()),
            },
            "requirement_intake": intake_capabilities(),
            "requirement_planner": planner_capabilities(),
            "development_orchestrator": orchestrator_capabilities(),
            "workflow_run": workflow_capabilities(),
            "workspace": workspace_tools.capabilities(),
        },
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        result = await agent.chat(
            request.message,
            session_id=request.session_id,
            system_prompt=request.system_prompt,
            workspace_root=request.workspace_root,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Model call failed: {exc}"
        ) from exc
    return ChatResponse(**result)


@app.post("/ag-ui")
async def ag_ui(
    run_input: RunAgentInput,
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    return StreamingResponse(
        build_ag_ui_stream(run_input, agent, planner, orchestrator, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/tools/requirement-planner")
async def run_requirement_planner(request: RequirementPlannerRequest) -> dict[str, Any]:
    try:
        return await planner.run(
            request.message,
            planner_state=request.planner_state,
            application=request.application,
            action=request.action,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Requirement planner failed: {exc}"
        ) from exc


@app.post("/tools/development-orchestrator")
async def run_development_orchestrator(
    request: DevelopmentOrchestratorRequest,
) -> dict[str, Any]:
    try:
        return await orchestrator.run(
            request.message,
            orchestrator_state=request.orchestrator_state,
            planner_state=request.planner_state,
            application=request.application,
            workspace_root=request.workspace_root,
            action=request.action,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Development orchestrator failed: {exc}"
        ) from exc


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
        build_workflow_ag_ui_stream(graph=graph, payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
