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

Compile one constant System Prompt in this stable order:

1. platform safety rules: obey declared tools and scopes, do not fabricate tool results,
   do not reveal secrets, and do not bypass approval;
2. ProductPlan-derived identity, purpose, capabilities, interaction and boundaries;
3. `agentSettings.prompt.systemPrompt`, persona and constraints;
4. declared Tool usage rules and expected output semantics.

Do not interpolate request text, credentials, environment values, absolute paths, or raw
JSON into this constant. Preserve the business meaning, but remove duplicate sentences.

## Model and Memory

- Use only the injected `model`; the template already called `init_chat_model` for
  `project_default`.
- When `memory.shortTerm.enabled=true` and `store=sqlite`, pass the injected checkpointer.
- When short-term Memory is disabled, pass `None` as the checkpointer.
- Do not implement MySQL, OSS, long-term Memory, Archive, or compression unless the
  formal Contract enables a currently supported adapter.

The focused test must patch `create_deep_agent` and assert the exact name, injected model,
tool list, System Prompt safety clauses, and checkpointer behavior.
