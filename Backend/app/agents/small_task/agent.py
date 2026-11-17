"""创建工作台和自由对话共同使用的局部代码修改 Agent。"""

from collections.abc import Callable

from app.agents.execution_control import RunCancellationMiddleware, create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, ModelRequest, ModelResponse

from app.agents.small_task.scope import ScopedSmallTaskBackend, is_small_task_path_allowed
from app.agents.frontend_test_instructions import FRONTEND_TEST_INSTRUCTIONS
from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.middleware.direct_modification import DirectModificationMiddleware
from app.middleware.small_task_cancellation import SmallTaskCancellationMiddleware
from app.observability.build_instrumentation import record_prompt
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.delete_file import create_delete_file_tool
from app.tools.execute import create_execute_tool
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


class IntegrationRepairPromptMetrics(AgentMiddleware):
    """首轮记录进入模型的有效内容大小，正文留在现有诊断体系。"""

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """在技能和记忆处理后统计完整系统提示及首轮任务文本。"""
        if request.state.get("run_model_call_count", 0) == 0 and request.messages:
            task = request.messages[0].text
            system = request.system_message.text if request.system_message else ""
            record_prompt(f"{system}\n{task}", task_context=task)
        return handler(request)


def create_integration_repair_agent(
    model, workspace_root: str | None, *, user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol, required_user_skills_prompt: str, unit_test: bool = False,
):
    """为集成修复及单测代码修复组装有界文件工具。"""
    backend = ScopedSmallTaskBackend(create_workspace_backend(
        workspace_root, include_builtin_skills=True,
        user_skills_backend=user_skills_backend, agent_memory_backend=agent_memory_backend,
    ), limit_context=True, unit_test_code_only=unit_test)
    filesystem = FilesystemMiddleware(
        backend=backend, permissions=create_workspace_permissions(
            workspace_root, mode="small_task", include_builtin_skills=True,
            include_user_skills=True, include_agent_memory=True,
        ),
    )
    file_tools = {"ls", "read_file", "write_file", "edit_file", "glob", "grep"}
    filesystem.tools = [tool for tool in filesystem.tools if tool.name in file_tools]
    unit_instructions = (
        "Repair one unit-test code failure. Start with the exact diagnosed location and make the "
        "smallest authorized edit. Preserve all valid tests, assertions, and formal contracts. "
        "Do not modify test scripts or dependency manifests. The platform reruns the original tests. "
        "Return completed with a real edit, or report an exact scope or formal escalation before editing. "
        "Return one SmallTask JSON object with status, summary, changedFiles, verification, failureReason "
        "and escalation. Leave verification empty for platform retest."
    )
    integration_instructions = (
        "Repair one integration failure using the task packet. Start with diagnosed source "
        "before configuration hints. Edit only allowedPaths and preserve acceptance checks and formal "
        "contracts. The platform runs installation and build checks after edits. Never claim a check "
        "passed. Keep exploration narrow and make the smallest verified-on-disk change. If more scope "
        "is needed, return exact requestedPaths with requires_user_confirmation; for formal changes "
        "return requires_workflow before editing. Finish with one JSON object containing status and summary."
    )
    return create_agent(
        name="unit-test-code-repair" if unit_test else "integration-repair-small-task", model=model,
        system_prompt="\n\n".join(filter(None, [
            unit_instructions if unit_test else integration_instructions,
            VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS, required_user_skills_prompt,
        ])),
        tools=[] if unit_test else [create_delete_file_tool(workspace_root, path_guard=is_small_task_path_allowed)],
        middleware=[
            RunCancellationMiddleware(model), SmallTaskCancellationMiddleware(),
            ModelCallLimitMiddleware(run_limit=12, exit_behavior="end"),
            filesystem,
            SkillsMiddleware(backend=backend, sources=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT]),
            MemoryMiddleware(backend=backend, sources=[AGENT_MEMORY_VIRTUAL_PATH]),
            *([] if unit_test else [IntegrationRepairPromptMetrics()]),
        ],
    ).with_config({"recursion_limit": 128})


def create_small_task_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只处理局部工作区改动、禁止任务编排和正式工件变更的 Deep Agent。"""

    base_system_prompt = (
        "You are the SmallTask Coding Agent shared by the workbench repair flow and free chat. "
        "The current user message contains one bounded task packet. Treat that packet as the "
        "complete execution contract: inspect only the relevant workspace context, make the "
        "smallest authorized change, and preserve the supplied engineering and business checks. You may edit files "
        "only inside packet.allowedPaths. Start inspection from packet.candidateFiles and application "
        "source directories. Never inspect installed dependencies, caches, or generated build output "
        "such as node_modules, dist, build, target, .next, or .turbo; package manifests and lockfiles "
        "are sufficient dependency evidence. "
        "For backendStartupFailure, use its rootCause and its explicitly supplied virtual log "
        "paths as read-only evidence. Fix the authorized dependency/configuration/source cause; "
        "never disable startup verification, suppress required initialization or fake readiness. "
        "Do not create or modify RequirementSpec, ProjectPlan, "
        "BuildTaskPlan, API contract documents, workflow artifacts, environment files, or database "
        "schema/DDL. Ordinary documentation, tests, scripts, and repository configuration may be "
        "edited only when the packet explicitly identifies them as allowed paths. Do not add a page, "
        "endpoint, data source, migration, or product behavior that "
        "is not already described by the packet. If the task needs any of those things, stop before "
        "editing and return requires_workflow with a reasonCode and workflowIntent. If an existing "
        "implementation is correct, verify it and return already_satisfied. If the implementation "
        "needs an additional code path with the same semantics, return requires_user_confirmation "
        "with the exact requestedPaths before writing outside the current scope. Never broaden a "
        "path glob on your own. Do not use task, write_todos, or subagents. Return exactly one JSON "
        "object and no Markdown. The JSON status must be completed, already_satisfied, "
        "requires_user_confirmation, requires_workflow, or failed. Include summary, changedFiles, "
        "verification, failureReason, and escalation when applicable. A single transient tool error "
        "does not decide the task result: when the authorized change is actually written and the "
        "requested outcome is verified, return completed and keep the tool error only as a warning. "
        "Return failed only for a missing or incorrect outcome, an unauthorized change, or a genuine "
        "verification blocker. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    execute_tool = create_execute_tool(workspace_root)
    return create_deep_agent(
        name="small-task-coding-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, FRONTEND_TEST_INSTRUCTIONS, required_user_skills_prompt) if part
        ),
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=[
            create_delete_file_tool(workspace_root, path_guard=is_small_task_path_allowed),
            execute_tool,
        ],
        middleware=[
            # 与前端 Agent 一样，在每次模型调用前补回可能被中间层遗漏的命令工具。
            DirectModificationMiddleware(required_tools=[execute_tool]),
            SmallTaskCancellationMiddleware(),
        ],
        backend=ScopedSmallTaskBackend(
            create_workspace_backend(
                workspace_root,
                include_builtin_skills=True,
                user_skills_backend=user_skills_backend,
                agent_memory_backend=agent_memory_backend,
            )
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="small_task",
            include_builtin_skills=True,
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
