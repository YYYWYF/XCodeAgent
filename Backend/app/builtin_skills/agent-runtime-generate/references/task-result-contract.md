# Task result contract

Return exactly one JSON object with a single top-level `task_results` array and exactly one
result for each approved task. Do not return Markdown or free text.

Each result contains:

- `task_id`: the unchanged approved task ID;
- `status`: `completed`, `already_satisfied`, or `failed`;
- `summary`: a non-empty factual description of the in-scope file result.

Use `completed` when the authorized Python files now satisfy the stable Runtime and
business Agent contract. If Java transport was not formally supplied, a fail-closed Tool
boundary may still be completed; state clearly in `summary` that live gateway integration
is deferred and do not claim end-to-end Tool readiness.

Use `already_satisfied` only when the existing authorized files already meet the task.
Use `failed` with non-empty `failure_category` and `failure_reason` when the stable Runtime
interface, business schemas, or writable paths make the task impossible in scope.

Never claim that dependencies were installed, tests were executed, services were started,
the Java gateway was implemented, or outer required checks passed. Those facts belong to
the outer Workflow or the Java owner.
