"""集中维护并组装 DataSource Agent 的角色、阶段职责和任务执行提示词。"""

from __future__ import annotations

import json
from typing import Any

from app.agents.data_source.prompt_context import execution_task_packet
from app.agents.data_source.workspace_context import backend_workspace_context
from app.observability.build_instrumentation import record_prompt
from app.services.builtin_skills import (
    SPRINGBOOT_BACKEND_BOOTSTRAP_SKILL_NAME,
    SPRINGBOOT_BACKEND_BUSINESS_SKILL_NAME,
    SPRINGBOOT_BACKEND_CONTROLLER_SKILL_NAME,
    SPRINGBOOT_BACKEND_FOUNDATION_SKILL_NAME,
)


DATA_SOURCE_SYSTEM_PROMPT = """You are a Java Backend Development Agent using Java 8, Spring Boot, and Maven.
Implement the provided tasks and implementation contracts by reading relevant code
and following the project's existing structure and coding conventions."""


_STAGE_PROMPTS = {
    SPRINGBOOT_BACKEND_BOOTSTRAP_SKILL_NAME: (
        "The current stage is Bootstrap. Check and complete the project's shared Maven "
        "dependencies, annotation processor configuration, and runtime configuration required "
        "for database access or external API integration."
    ),
    SPRINGBOOT_BACKEND_FOUNDATION_SKILL_NAME: (
        "The current stage is Foundation. Establish or complete the module's data access "
        "and integration foundations, including database mappings, Mapper interfaces, "
        "repository skeletons, external API client structures, and client connection "
        "configuration, as required by the task. These structures support business "
        "implementation in subsequent stages."
    ),
    SPRINGBOOT_BACKEND_BUSINESS_SKILL_NAME: (
        "The current stage is Business. Implement the approved endpoint's business behavior "
        "through the Service and required supporting types. Complete database operations "
        "or external API calls, field mappings, response composition, and transaction "
        "and error handling according to the implementation contract."
    ),
    SPRINGBOOT_BACKEND_CONTROLLER_SKILL_NAME: (
        "The current stage is Controller. Connect the approved HTTP endpoints to the "
        "completed Services. Implement routing, request binding and validation, response "
        "wrapping, and the authorization annotations required by the implementation contract, "
        "using the project's existing web infrastructure."
    ),
}


def data_source_system_prompt(
    *,
    builtin_skill_names: tuple[str, ...],
    required_user_skills_prompt: str = "",
) -> str:
    """按已挂载的阶段 Skill 组装通用角色、阶段职责和用户技能指令。"""

    # 沿用调用链已有的阶段白名单，保持提示词与缓存 Agent 的 Skill 范围一致。
    stage_prompts = [
        prompt
        for skill_name, prompt in _STAGE_PROMPTS.items()
        if skill_name in builtin_skill_names
    ]
    return "\n\n".join(
        part
        for part in (DATA_SOURCE_SYSTEM_PROMPT, *stage_prompts, required_user_skills_prompt)
        if part
    )


def data_source_generation_prompt(
    *,
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> str:
    """集中生成任务执行规则，并保留完整工作区上下文、任务包和结果协议。"""

    task_packets = [
        execution_task_packet(project_plan, task)
        for task in tasks
    ]
    workspace_context = backend_workspace_context(workspace_snapshot)
    task_text = json.dumps(task_packets, ensure_ascii=False, indent=2)
    workspace_text = json.dumps(workspace_context, ensure_ascii=False, indent=2)
    prompt = (
        "Execute the approved task provided below.\n\n"
        "## Execution Requirements\n\n"
        "- Use implementation_contract as the implementation authority. Make only "
        "the necessary changes within allowed_paths and change_scope.\n"
        "- Before implementation, read every Skill entry and reference listed in "
        "instruction_paths. Read repeated files only once and apply them only to "
        "tasks that list them.\n"
        "- Use Backend Workspace Context to locate project files. "
        "backend_working_directory is the Java project root. "
        "Read target files and relevant existing implementations before editing.\n"
        "- stage defines the task's responsibility; execute execution_steps in array order.\n"
        "- Preserve content that satisfies the contract; add or correct only missing behavior.\n"
        "- Check live targets before writing: read and evaluate an existing add target; "
        "create a missing modify target only if the contract still requires it. "
        "If approved_add_path already exists, report failure_category=repair_scope_stale.\n"
        "- Use virtual absolute paths for filesystem tools. Task paths are relative "
        "to the virtual workspace root: backend/pom.xml maps to /backend/pom.xml. "
        "Do not prepend the host workspace path or the backend working directory again.\n"
        "- If the contract is insufficient or the task cannot be completed within "
        "its approved scope, report failure using the result protocol.\n\n"
        "## Verification Policy\n\n"
        "verification_policy is outer_integration_test_only. The platform performs "
        "integration verification; do not install dependencies, run verification "
        "commands, start services, or create verification scripts. "
        "Report only verification actually performed.\n\n"
        "## Result Format\n\n"
        "Return only one JSON object whose only top-level field is task_results. "
        "Return exactly one result per task, with no unknown or duplicate task_id. "
        "Each result contains task_id, status, and a concise, non-empty summary.\n"
        "- completed: any write was performed; omit satisfaction_evidence.\n"
        "- already_satisfied: all targets fully satisfy the contract and no writes "
        "were performed. Provide a non-empty satisfaction_evidence object containing "
        "inspected target_files and concise, concrete findings covering the complete contract "
        "without repeating the summary.\n"
        "- failed: provide failure_category and failure_reason. Only contract_mismatch "
        "or plan_mismatch may include a non-empty change_request.\n"
        "Do not return changed_files or commands, or wrap the JSON in Markdown fences.\n\n"
        "Backend Workspace Context:\n"
        f"{workspace_text}\n\n"
        f"Task packets:\n{task_text}\n"
    )
    # 提示词迁移到独立模块后，继续记录完整输入及上下文观测数据。
    record_prompt(prompt, task_context=task_text, workspace_context=workspace_text)
    return prompt
