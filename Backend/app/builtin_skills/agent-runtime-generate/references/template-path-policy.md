# Template path policy

The Build task carries the platform-owned path contract for the current Runtime template commit.
The generated application does not contain a platform Manifest or Definition Loader.

- The current module may read only its `engineering_context.read_paths` plus files explicitly
  supplied by the task.
- `modify_paths` are existing Runtime files that may be edited.
- `add_roots` and `test_roots` authorize minimal new files only when existing code cannot satisfy
  the Contract.
- The effective write scope is the intersection of these fields and task `allowed_paths`.
- Prefer modifying `src/app/agent/factory.py` and registering a Builder. Do not generate a
  `<agent_id>.py` file merely because an Agent exists.

Reject the task as `contract_mismatch` when the module, template commit, policy hash, or allowed
paths do not match. Do not infer permissions from repository contents or historical template files.
