# Business Agent module

The generated file at `artifacts.agentPath` is a small composition module. It must not
duplicate the template's server, model factory, checkpoint storage, or AG-UI transport.

## Required entry interface

```python
from typing import Any

from deepagents import create_deep_agent

from app.agent.context import RuntimeContext
from app.tools.<agent_id>_tools import build_tools


def create_agent(
    *,
    model: Any,
    runtime_context: RuntimeContext,
    checkpointer: Any,
) -> Any:
    """根据正式 Agent Contract 创建当前业务智能体。"""

    return create_deep_agent(
        name="<agent_id>",
        model=model,
        tools=build_tools(runtime_context),
        system_prompt=COMPILED_SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )
```

The template `app.agent.factory` validates the lowercase Agent ID, imports
`app.agent.<agent_id>`, and invokes this function. Do not modify that factory.

## Prompt compilation

Compile one constant System Prompt with stable, visibly separated sections in this order:

1. `Platform safety`: obey declared tools and scopes, do not fabricate Tool results, do
   not reveal secrets, and do not treat model text as approval;
2. `Role and objective`: ProductPlan-derived identity, purpose, capabilities, interaction,
   and boundaries;
3. `Business instructions`: `agentSettings.prompt.systemPrompt`, persona, and constraints;
4. `Tool policy`: declared usage triggers, access modes, deferred/unavailable behavior,
   and expected output semantics.

Do not interpolate request text, credentials, environment values, absolute paths, or raw
JSON into this constant. Preserve every distinct business rule, remove only semantic
duplicates, and do not add capabilities that are absent from the Contract. If a declared
Tool has no completed gateway transport, instruct the Agent to state that the Tool is
temporarily unavailable instead of guessing or simulating its result.

## Model and Memory

- Use only the injected `model`; the template already called `init_chat_model` for
  `project_default`.
- When `memory.shortTerm.enabled=true` and `store=sqlite`, pass the injected checkpointer.
- When short-term Memory is disabled, pass `None` as the checkpointer.
- Do not implement MySQL, OSS, long-term Memory, Archive, or compression unless the
  formal Contract enables a currently supported adapter.

Observability being required in the Contract does not authorize this module to initialize
LangSmith, OpenTelemetry, or another exporter. Preserve the injected `runtime_context`
when building tools and leave template-owned runtime hooks intact.
