CODE_GRAPH_TASK_EXECUTION_GUIDANCE = """
## Code graph navigation contract
Process dispatched tasks one by one by `task_id`, even when several tasks for the same owner
arrive in one batch. Before a broad `list_files`, text search, or directory read for each task,
call `code_graph_context` using that task's `target_files`, `change_scope`, `allowed_paths`, and
the narrowest known business symbol or endpoint name. Prefer `file_summary` for an existing
target file. Use `search_symbols` for a new file or a task that only identifies a business
concept. Call `references`, `impact`, or `related_tests` only after a concrete symbol match.

Treat a code graph result as usable only when `status` is `ready` and at least one of
`matches`, `relations`, `relatedTests`, or `impactedFiles` is non-empty. If the response is
malformed, has status `skipped`, `unavailable`, or `failed`, or is `ready` with all four result
collections empty, do not fail the task and do not repeat the same graph query. Immediately
fall back to the existing task-scoped file listing, search, and `read_file` flow, constrained by
`target_files`, `allowed_paths`, and `change_scope`. A graph failure never expands the task's
authorized paths. Code graph data is navigation metadata only: always read the current source
file with a workspace file tool before editing or claiming acceptance.
""".strip()
