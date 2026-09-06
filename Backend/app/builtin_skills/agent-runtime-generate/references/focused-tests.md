# Focused tests

Add or modify tests only under the current module's task-declared `testRoots`. Tests describe the actual
module change; the outer Workflow decides when to execute them.

## Agent composition

- Assert only the changed module behavior and that injected model/tools/
  RuntimeContext/checkpointer are preserved.
- Use a minimal `RuntimeContext` fixture. Do not read real credentials or environment
  configuration.
- Do not repeat unaffected template-owned Factory, AG-UI, model, Tool, server, or
  persistence tests.

## Tool boundary

- For a special Tool transform, assert only the transformation that the generic Tool
  builder cannot express.
- When a declared transport exists, replace that boundary and verify Contract-derived
  request/response mapping without real network access.
- When transport is deferred, assert invocation fails closed with a bounded unavailable
  error and never reaches a network client.
- Do not assert speculative Java URLs, Headers, Tokens, response envelopes, or Java client
  types that are not supplied by the formal task.

Keep the test deterministic and limited to the current module.
Do not install dependencies, start services, or run integration checks from the Build
task.
