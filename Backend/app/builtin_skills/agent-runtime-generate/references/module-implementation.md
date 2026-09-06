# Seven-module implementation

Choose the action from current disk evidence:

- `skip`: only when the Contract explicitly disables an optional module.
- `reuse`: existing template behavior and wiring fully satisfy the current module config hash.
- `modify`: an existing declared extension point needs the smallest change.
- `add`: the template cannot carry required behavior and the task declares an add/test root.

Module rules:

- Prompt: preserve the locked platform prefix, identity, boundaries, persona, System Prompt, and
  constraints. Request text and credentials never enter the constant Prompt.
- Model: reuse the injected project model; bind only confirmed generation parameters and never call
  another model initializer.
- Memory: reuse SQLite short-term checkpoint support when selected. Do not invent MySQL, OSS,
  archive, or long-term adapters.
- Tools: preserve Tool and Endpoint identities and schemas; use the template fail-closed transport
  until a real Gateway contract exists.
- Skills and Knowledge: skip when disabled. Do not generate a fake loader or retriever when Runtime
  support is absent.
- Context: preserve trusted RuntimeContext identity, budgets, interaction behavior, and supported
  compression settings. Reject unsupported enabled policies rather than silently ignoring them.

One module result must not claim completion for any other module.
