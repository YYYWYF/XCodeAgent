# Coding Agent Instructions

Use these rules whenever generating or modifying React + TypeScript code in this workspace.

Primary rule source:

- `react-antd-v4-codegen/SKILL.md`

Hard requirements:

1. Only write Ant Design `4.24.16` code. Do not generate antd v5/v6 APIs.
2. Every third-party import must be declared in the current package or workspace `package.json`.
3. Use feature-based file splitting. Avoid large files and keep code easy to review by feature.
4. Use nearby ownership: private code stays inside the component or feature module; only cross-module reusable code moves to global directories.
5. Follow React Hook rules. Never call Hooks conditionally or after an early return.
6. Avoid unnecessary `useEffect`; do not store derived state.
7. Handle loading, empty, error, forbidden, and notFound states explicitly.
8. Write necessary comments for complex business rules, compatibility logic, race handling, and side-effect boundaries.
9. Prefer existing project patterns, wrappers, request hooks, components, styles, and naming.
10. Do not introduce unrelated refactors.

Before finishing React work, run through `react-antd-v4-codegen/references/review-checklist.md`.
