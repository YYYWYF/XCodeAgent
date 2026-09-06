---
name: agent-runtime-generate
description: >-
  Implement one confirmed business Agent in the generated Python 3.12 + DeepAgents
  runtime. Use only for owner=agent Build tasks with a complete Agent Contract.
---

# agent-runtime-generate

Use this Skill only for a confirmed `agent:<agentId>` Build Unit. The execution prompt
provides one complete Agent Contract, its resolved Java API Contracts, and exact writable
paths. Those structures are authoritative; do not redesign the Agent, Runtime, Endpoint,
security policy, or Build task.

## Required workflow

1. Read the existing template files below before the first write:
   - `/agent-runtime/src/app/agent/factory.py`
   - `/agent-runtime/src/app/agent/context.py`
   - `/agent-runtime/src/app/models/factory.py`
   - `/agent-runtime/src/app/persistence/checkpointer.py`
2. Read the task's three writable target files if they already exist.
3. Read [business-agent-module.md](references/business-agent-module.md).
4. Read [java-tool-adapter.md](references/java-tool-adapter.md) only when
   `agentSettings.tools.bindings` is non-empty.
5. Implement exactly the declared Agent module, Tool adapter, and focused test. Do not
   edit template infrastructure, dependency files, environment files, or another Agent.

## Hard boundaries

- The generated Agent module exports exactly one template entry function named
  `create_agent(*, model, runtime_context, checkpointer)`.
- The template already resolves the project-default model through `init_chat_model` and
  injects it as `model`. Do not create another model, read model credentials, or allow a
  request to select Provider/Base URL/API Key.
- Call `create_deep_agent` with the injected model, generated tools, compiled System
  Prompt, declared checkpointer behavior, and `name=<agentId>`.
- Generate only tools listed in `agentSettings.tools.bindings`. A Tool may call only its
  resolved Java Endpoint; it must never call a database, third-party API, browser, local
  command, or arbitrary URL directly.
- Treat RuntimeContext identity and scopes as trusted only because the template created
  them after internal gateway authentication. Do not accept identity from tool arguments.
- Keep Skills, Knowledge, long-term/archive Memory, summary compression, Vision,
  structured final output, and per-Agent model overrides disabled when the Contract says
  they are disabled.
- Never place secrets, connection values, Prompt internals, full tool responses, or host
  paths in logs or task results.
- Return `failed` with a contract mismatch when the template entry interface, resolved
  Endpoint schemas, or task writable paths cannot implement the Contract exactly. Do not
  broaden scope or silently degrade behavior.

The outer Workflow owns dependency installation, compilation, tests, service startup,
health checks, and integration verification. Do not run them from this Build task.
