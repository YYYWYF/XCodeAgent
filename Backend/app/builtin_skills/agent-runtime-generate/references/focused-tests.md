# Focused tests

Generate the test at `artifacts.testPath`. Tests describe the generated Python extension;
the outer Workflow decides when to execute them.

## Agent composition

- Patch `create_deep_agent` and assert the exact Agent name, injected model, compiled
  System Prompt safety rules, declared Tool list, and checkpointer behavior.
- Use a minimal `RuntimeContext` fixture. Do not read real credentials or environment
  configuration.
- Verify disabled Contract capabilities remain disabled without testing template-owned
  AG-UI, model factory, server, or persistence internals again.

## Tool boundary

- Assert one generated Tool per declared binding, stable Tool names, model-visible argument
  schemas, and correct read/write semantics.
- When a declared transport exists, replace that boundary and verify Contract-derived
  request/response mapping without real network access.
- When transport is deferred, assert invocation fails closed with a bounded unavailable
  error and never reaches a network client.
- Do not assert speculative Java URLs, Headers, Tokens, response envelopes, or Java client
  types that are not supplied by the formal task.

Keep the test deterministic and limited to the generated Agent module and Tool adapter.
Do not install dependencies, start services, or run integration checks from the Build
task.
