"""七模块只扩展现有 Unit Worker 规则，保持当前候选协议与 Local/Global 校验。"""

import json

from app.services.runtime_agent_dag import agent_task_manifest
from app.services.unit_generation_contracts import UnitGenerationContext


def resolve_agent_unit_task_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """平台固定七模块和依赖，模型只在模块策略内选择精确文件及实现步骤。"""

    policy = context.constraints.get("agent_template_policy")
    if not policy or not policy.get("modulePaths"):
        raise ValueError("Agent Unit 缺少已验证并冻结的模板路径策略。")
    return (
        "Use exactly the incremental module manifest below: one Task per record, exact task_id "
        "and dependencies, owner=agent, task_type=agent.code, status=pending. Each Task has one "
        "agent.runtime deliverable with target_id equal to the formal agent_id and provides "
        "containing its exact requirement_id. Never combine modules or create another planning "
        "workflow. Keep can_run_in_parallel=false because modules share the existing factory.\n"
        + json.dumps(agent_task_manifest(context), ensure_ascii=False, indent=2),
        "Read the authorized TechnicalPlan Agent Contract and real Runtime template context. "
        "Plan minimal Python source/resource changes for each module using this platform policy. "
        "All target_files, change_scope, allowed_paths and deliverable paths must be identical "
        "non-empty sets of exact files. modifyPaths permits its existing exact files; addRoots "
        "and testRoots permit module-owned .py/.md/.txt/.json/.yaml/.yml files only. Never emit "
        "globs, hidden files, SQL, scripts, dependency/configuration changes or mechanical Agent wrappers.\n"
        + json.dumps(dict(policy), ensure_ascii=False, indent=2, default=dict),
        "Keep all seven configured modules including explicitly disabled tools/skills/knowledge. "
        "A disabled module still needs an inspection/removal-of-wiring Task, status=pending; "
        "never mark it completed without execution. Prompt implements persona and constraints; "
        "Model reuses the project-injected model without a new credential or model instance; "
        "Memory reuses the template checkpointer with trusted Principal/thread isolation; Tools "
        "import the declared Endpoint ApplicationService locally, never call loopback REST; "
        "Skills and Knowledge honor only configured resources; Context propagates trusted "
        "request identity and confirmed interaction inputs. Use Chinese numbered execution steps. "
        "Do not plan build, startup, database schema changes, permissions changes or acceptance tasks.",
    )
