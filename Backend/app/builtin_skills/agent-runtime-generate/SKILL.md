---
name: agent-runtime-generate
description: >-
  Inspect and implement one approved Prompt, Model, Memory, Tools, Skills, Knowledge,
  or Context module in a generated Python DeepAgents Runtime. Use only for platform-compiled
  owner=agent, task_type=agent.code Build tasks with a validated platform template path policy.
---

# agent-runtime-generate

Implement exactly one `source_refs.agent_module` from the confirmed Agent Contract.

## Workflow

1. Read the current task and its Contract slice.
2. Read [template-path-policy.md](references/template-path-policy.md), then only
   the current module's declared `readPaths` and existing authorized target files.
3. Read [module-implementation.md](references/module-implementation.md) and decide exactly one
   action: `skip`, `reuse`, `modify`, or `add`.
4. For Tools, also read [java-tool-adapter.md](references/java-tool-adapter.md). For added or
   modified tests, read [focused-tests.md](references/focused-tests.md).
5. Make the smallest authorized change. Read [task-result-contract.md](references/task-result-contract.md)
   before returning the result.

## Boundaries

- Treat the Contract as the complete business source. Do not invent identity, Prompt rules,
  capabilities, Tools, schemas, URLs, credentials, approval policy, or Java behavior.
- Do not modify paths outside the task policy, `.xcodeagent/`, frontend, Java backend, planning artifacts,
  the Build DAG, dependency files, environment files, or another Agent.
- Reuse a template capability when it already satisfies the current module. Do not create a file
  merely to produce a Diff, and do not create a per-Agent wrapper by convention.
- Add Python only below the current module's task-declared `addRoots` or `testRoots`. Do not initialize
  a second model or duplicate the Runtime server, AG-UI, authentication, interaction, or settings.
- Keep missing Java Gateway transport fail-closed. Do not block Python structure generation or
  simulate successful Tool results.
- Never expose secrets, complete System Prompts, unbounded Tool output, host paths, or hidden model
  reasoning in task results.

The outer Workflow owns dependency installation, commands, tests, service startup, health checks,
review, and acceptance. Do not run project-level verification from this Build task.
