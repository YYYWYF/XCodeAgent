"""创建只读二次修改调查 Agent。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallLimitMiddleware,
)

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


class RevisionInvestigatorMiddleware(AgentMiddleware):
    """把二次修改调查 Agent 的模型工具硬限制为只读文件导航能力。"""

    _ALLOWED_TOOLS = frozenset({"ls", "read_file", "glob", "grep"})

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步调用模型前移除写入、执行、待办和委派工具。"""

        return handler(request.override(tools=_read_only_tools(request.tools)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步调用模型前应用相同的只读工具白名单。"""

        return await handler(request.override(tools=_read_only_tools(request.tools)))


def create_revision_investigator_agent(model: Any, workspace_root: str | None = None):
    """创建只在快速分类证据不足时读取当前工作区的 Deep Agent。"""

    system_prompt = (
        "You are XCodeAgent's read-only Revision Investigator. You run only after the fast "
        "router could not safely classify a workbench message. Progressively inspect the smallest "
        "set of current workspace files needed to distinguish a confirmed-semantic change from a "
        "localized implementation fix. Never write, edit, delete, execute commands, plan todos, or "
        "delegate work. Use at most six read/search/list tool calls. Treat current confirmed JSON "
        "artifacts under .xcodeagent as product and technical contract evidence; source code is "
        "implementation evidence. Missing contract evidence is unknown, not proof that a request is "
        "an implementation fix. A clear business request must still enter the matching formal "
        "workflow even when source paths or design details are absent. Existing-page visual, layout, "
        "spacing, copy, responsive, and interaction polish remains implementation_fix/frontend when "
        "it does not add or change business behavior. Do not choose or output Graph node names. "
        "Return exactly one JSON object matching the requested routing contract and no Markdown. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    return create_deep_agent(
        name="revision-investigator-agent",
        model=model,
        system_prompt=system_prompt,
        middleware=[
            RevisionInvestigatorMiddleware(),
            ToolCallLimitMiddleware(run_limit=6, exit_behavior="end"),
        ],
        backend=create_workspace_backend(workspace_root),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="revision_investigator",
        ),
    )


def revision_investigator_prompt(
    *,
    request: str,
    conversation_summary: str,
    fast_decision: dict[str, Any],
    current_target: dict[str, Any] | None,
) -> str:
    """构造包含快速分类失败证据和当前业务目标的只读调查输入。"""

    return (
        "Investigate only enough workspace evidence to produce a safer routing candidate.\n\n"
        "Routing rules:\n"
        "- formal_revision: confirmed product or technical semantics change. Return formalBranch, "
        "revisionType, earliestArtifact, affectedArtifactKeys, and affectedResourceKeys.\n"
        "- implementation_fix: confirmed semantics stay unchanged and the request is a localized "
        "frontend, backend, fullstack, or precise workspace-file correction.\n"
        "- workspace_question: the user requests read-only inspection or explanation.\n"
        "- casual_chat: no workspace evidence is needed.\n"
        "- clarification: use only when the business object/action and expected result remain "
        "materially ambiguous after bounded investigation.\n\n"
        "Formal mapping:\n"
        "- requirement_scope_change -> design_stage_revision / requirement-spec\n"
        "- product_behavior_change -> design_stage_revision / product-plan\n"
        "- technical_contract_change, endpoint_implementation_change, or data_source_change "
        "-> workbench_plan_revision / technical-plan\n\n"
        "Return exactly this JSON shape:\n"
        '{"response":"","route":"casual_chat|workspace_question|clarification|implementation_fix|formal_revision",'
        '"formalBranch":"design_stage_revision|workbench_plan_revision|null",'
        '"revisionType":"requirement_scope_change|product_behavior_change|technical_contract_change|endpoint_implementation_change|data_source_change|null",'
        '"earliestArtifact":"requirement-spec|product-plan|technical-plan|null",'
        '"owner":"frontend|backend|fullstack|workspace|none|unknown",'
        '"confidence":0.0,"reason":"简体中文依据",'
        '"clarificationQuestion":"仅 clarification 时填写",'
        '"candidatePaths":[],"affectedArtifactKeys":[],"affectedResourceKeys":[],"questions":[]}\n\n'
        f"Fast router result:\n{json.dumps(fast_decision, ensure_ascii=False, indent=2)}\n\n"
        f"Current target:\n{json.dumps(current_target or {}, ensure_ascii=False, indent=2)}\n\n"
        f"Bounded conversation summary:\n{conversation_summary[-4_000:] or '(empty)'}\n\n"
        f"Current user request:\n{request.strip()}"
    )


def _read_only_tools(tools: list[Any]) -> list[Any]:
    """按稳定工具名只保留目录、读取、匹配和搜索工具。"""

    return [
        tool
        for tool in tools
        if str(
            (tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", ""))
            or ""
        )
        in RevisionInvestigatorMiddleware._ALLOWED_TOOLS
    ]
