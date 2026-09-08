---
name: agent-runtime-generate
description: >-
  Implement one confirmed business Agent in the generated Python 3.12 + DeepAgents
  runtime. Use only for owner=agent Build tasks with a complete Agent Contract; Java
  gateway implementation may remain deferred when no formal transport contract exists.
---

# agent-runtime-generate

Use this Skill only for a confirmed `agent:<agentId>` Build Unit. The execution prompt
provides one complete Agent Contract, any resolved Java API Contracts, and exact writable
paths. Those structures are authoritative for business semantics. A Java API Contract is
not by itself a complete Python-to-Java transport specification.

## Required workflow

1. Read the existing template files below before the first write:
   - `/agent-runtime/src/app/agent/factory.py`
   - `/agent-runtime/src/app/agent/context.py`
   - `/agent-runtime/src/app/models/factory.py`
   - `/agent-runtime/src/app/persistence/checkpointer.py`
2. Read the task's three writable target files if they already exist.
3. Read [runtime-extension-contract.md](references/runtime-extension-contract.md) and
   [business-agent-module.md](references/business-agent-module.md).
4. Read [java-tool-adapter.md](references/java-tool-adapter.md) only when
   `agentSettings.tools.bindings` is non-empty.
5. Read [focused-tests.md](references/focused-tests.md), then implement exactly the
   declared Agent module, Tool adapter, and focused test.
6. Read [task-result-contract.md](references/task-result-contract.md) before returning the
   task result. Do not edit template infrastructure, dependency files, environment files,
   Java source, or another Agent.

## Hard boundaries

- The generated Agent module exports exactly one template entry function named
  `create_agent(*, model, runtime_context, checkpointer)`.
- The template already resolves the project-default model through `init_chat_model` and
  injects it as `model`. Do not create another model, read model credentials, or allow a
  request to select Provider/Base URL/API Key.
- Call `create_deep_agent` with the injected model, generated tools, compiled System
  Prompt, declared checkpointer behavior, and `name=<agentId>`.
- Generate only tools listed in `agentSettings.tools.bindings`. Preserve their declared
  names, schemas, access modes, and Endpoint semantics. Never generate Java code or call a
  database, third-party API, browser, local command, or arbitrary URL directly.
- Reuse a gateway transport only when the current template or task supplies an explicit,
  supported boundary. Otherwise keep the Tool integration fail-closed and report that
  Java gateway wiring remains deferred. Missing gateway details must not block generation
  of the Agent module, contract-shaped Tool definitions, or focused tests.
- Do not invent a Java client, URL, route prefix, Header, Token name, response envelope,
  retry policy, or authentication mechanism from Endpoint metadata alone.
- Treat RuntimeContext identity and scopes as trusted only because the template created
  them after internal gateway authentication. Do not accept identity from tool arguments.
- Keep Skills, Knowledge, long-term/archive Memory, summary compression, Vision,
  structured final output, and per-Agent model overrides disabled when the Contract says
  they are disabled.
- Never place secrets, connection values, Prompt internals, full tool responses, or host
  paths in logs or task results.
- Return `failed` with a contract mismatch when the stable Runtime entry interface,
  business Tool schema, or task writable paths cannot implement the Contract. A deferred
  Java transport is not a contract mismatch when no formal transport contract was supplied;
  it must instead remain explicit and fail closed.

The outer Workflow owns dependency installation, compilation, tests, service startup,
health checks, and integration verification. Do not run them from this Build task.
