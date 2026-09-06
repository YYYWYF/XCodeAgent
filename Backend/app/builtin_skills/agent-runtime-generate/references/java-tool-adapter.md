# Java gateway Tool boundary

The Runtime template may already expose standard Contract Tools. Reuse it when the current task
policy and disk evidence prove the binding is complete; otherwise modify an authorized existing
Tool entry point or add the minimum adapter below the task's Tools `addRoots`.

This reference constrains the Python boundary only. Java gateway implementation and Java
source generation belong to another owner and are not part of this Skill.

## Stable Tool contract

- Do not regenerate a standard Tool binding or transport wrapper that already satisfies the Contract.
- Derive Tool arguments only from the resolved request/path/query schema. Do not add
  identity, tenant, scope, credential, URL, or Header parameters to the model-visible Tool.
- Preserve `toolId`, description/usage trigger, `accessMode`, Endpoint identity, method,
  path, request schema, and response schema as business-interface metadata.
- `accessMode=read` remains read-only. `accessMode=write` never treats user or model text as
  platform approval.

## Gateway integration modes

Choose the mode from evidence already present in the task and template:

1. **Declared transport available**: reuse the supplied Python gateway client or transport
   abstraction and its configuration contract. Map the declared Tool request and response
   without changing their shapes. Tests replace that boundary; they do not call a real
   service.
2. **Transport not yet declared**: keep the Tool and its typed arguments available, but
   route invocation to one small fail-closed boundary that returns or raises a bounded
   `gateway_not_configured`-style error. Do not issue an HTTP request and do not simulate a
   successful business response.

Endpoint `method` and `path` describe the future business call; they do not establish a
base URL, Java client class, route prefix, Header set, Token name, response envelope,
timeout, redirect, or retry policy. Never invent those details. `RuntimeContext` remains
trusted input from the Python Runtime, but how it is forwarded is owned by the future
gateway contract.

The absence of a completed Java gateway does not block generation of the Agent module,
Tool declarations, or focused tests. The task summary must state that live Tool transport
remains deferred whenever mode 2 is used.
