# Runtime extension contract

The Agent Runtime template owns the server, AG-UI transport, model construction,
authentication, interaction lifecycle, and checkpoint storage. Generated business files
extend only its documented composition points.

## Stable Python interface

- `app.agent.factory` imports `app.agent.<agent_id>` and calls
  `create_agent(*, model, runtime_context, checkpointer)`.
- `model` is the project-default chat model already created by the template.
- `runtime_context` is the template-created `RuntimeContext`; current stable fields are
  `user_id`, `tenant_id`, `scopes`, and `traceparent`.
- `checkpointer` is the template-owned short-term checkpoint implementation.
- The generated module may import `create_deep_agent`, `RuntimeContext`, and its own
  generated Tool builder. It must not recreate template factories or lifecycle code.

Inspect the current template before relying on any additional function, setting, package,
or field. Transitive dependencies are not a supported extension interface.

## Capabilities outside this Build task

The Java gateway transport is not part of the stable Python extension interface until an
explicit client/transport contract is supplied. Do not infer it from template settings or
Java Endpoint metadata.

`model.requiredCapabilities.observability=true` means the generated Agent must preserve
the template's trace context and hooks. It does not require this task to add exporters,
telemetry dependencies, logging backends, or Java tracing code. Never log Prompt content,
credentials, full Tool responses, or model-visible private context.
