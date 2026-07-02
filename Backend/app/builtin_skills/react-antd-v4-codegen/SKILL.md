---
name: react-antd-v4-codegen
description: Use when generating, modifying, or reviewing React + TypeScript code that must follow project conventions, Ant Design 4.24.16, package.json dependency declarations, feature-based file splitting, nearby ownership, React Hook rules, and necessary comments.
---

# React Antd v4 Codegen

Use this skill for React + TypeScript code generation, modification, and review.

## Mandatory Workflow

0. Treat `REACT_BEST_PRACTICES_GUIDE.md` and `AGENTS.md` as mandatory entry instructions.
1. Check the relevant `package.json` before importing third-party libraries.
2. Confirm antd usage targets Ant Design `4.24.16`.
3. Follow nearby ownership and feature-based file splitting.
4. Keep React code pure, typed, and explicit about request and error states.
5. Add necessary comments for complex or non-obvious logic.
6. Before finishing, use the review checklist.

## Hard Gates

Stop or ask for confirmation when:

- `antd` is missing or not compatible with `4.24.16` and the task requires antd code.
- A desired third-party package is not declared in `package.json`.
- The requested implementation requires an unapproved new dependency.
- The existing project pattern conflicts with these rules in a way that affects behavior.

## Reference Files

Read only what is needed:

- `REACT_BEST_PRACTICES_GUIDE.md`: project guide entry and rule map.
- `AGENTS.md`: hard requirements for coding agents in React + TypeScript workspaces.
- `references/dependencies-and-antd.md`: package.json dependency rules and Ant Design `4.24.16` rules.
- `references/structure-and-ownership.md`: feature modules, nearby ownership, file-size limits, and comment rules.
- `references/react-rules.md`: React state, Hooks, Effect, API, typing, performance, routing, and error rules.
- `references/review-checklist.md`: final code review checklist and priority order.

For most React code generation tasks, read `dependencies-and-antd.md`, `structure-and-ownership.md`, and `react-rules.md`. For review or final validation, read `review-checklist.md`.
