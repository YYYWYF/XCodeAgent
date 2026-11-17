"""显式组装 DataSource 所需能力，避免注入通用规划和子代理提示词。"""

from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.summarization import create_summarization_middleware
from langchain.agents import create_agent
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

from app.agents.execution_control import RunCancellationMiddleware


SKILLS_PROMPT = """## Available Skills
{skills_locations}
{skills_load_warnings}
{skills_list}
Use read_file(limit=1000) for Skill files."""

FILESYSTEM_PROMPT = """## Filesystem Tools
Use ls, read_file, write_file, edit_file, glob, and grep with virtual absolute paths.
Use offset/limit to read large files.
If write_file reports an existing target, use edit_file on that target.
Large tool outputs may be saved under /large_tool_results/; read the returned path."""


def create_data_source_runtime(
    *, model, system_prompt, tools, backend, permissions, skills, name,
):
    """保留编码必需的中间件和权限，不注册 Todo、子代理或默认通用提示词。"""

    return create_agent(
        model=model,
        name=name,
        system_prompt=system_prompt,
        tools=tools,
        middleware=[
            RunCancellationMiddleware(model),
            SkillsMiddleware(backend=backend, sources=skills, system_prompt=SKILLS_PROMPT),
            # 继续由官方文件工具执行原权限规则，不在提示词层模拟权限控制。
            FilesystemMiddleware(
                backend=backend,
                _permissions=permissions,
                system_prompt=FILESYSTEM_PROMPT,
            ),
            create_summarization_middleware(model, backend),
            PatchToolCallsMiddleware(),
            AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
        ],
    ).with_config({"recursion_limit": 9_999})
