# Structure And Ownership

## Nearby Ownership

Private code stays near the component or feature that owns it. Only move code upward when it has real reuse.

| Code Type | Single Component | Same Feature Module | Multiple Feature Modules |
| --- | --- | --- | --- |
| Component | component directory or current file | `routes/Feature/components/` | `src/components/` |
| Hook | component `hooks/` or current file | `routes/Feature/hooks/` | `src/hooks/` |
| API adapter | component nearby or module `apis.ts` | `routes/Feature/apis.ts` | `src/apis/` |
| Type | component file or `types.ts` | `routes/Feature/types.ts` | `src/typings/` |
| Constant | component file or `constants.ts` | `routes/Feature/constants.ts` | `src/constants/` |
| Style | component directory | feature style directory | `src/styles/` |
| Utility | component nearby if pure | `routes/Feature/utils.ts` | `src/utils/` |
| Provider | component or feature internal | `routes/Feature/providers/` | `src/providers/` |

## Promotion Rules

1. Do not promote code globally because it might be reused.
2. Promote only after at least a second real usage appears.
3. Before promoting globally, remove page-specific API, permission, copywriting, and workflow coupling.
4. Global directories contain only stable, generic, cross-feature code.
5. Private feature code should stay inside the feature even if there are several files.

## Feature Module Structure

```text
routes/Editor/
├── index.tsx
├── components/
│   └── ThemeForm/
│       ├── index.tsx
│       ├── hooks/
│       ├── types.ts
│       └── ThemeForm.module.less
├── hooks/
├── apis.ts
├── types.ts
└── constants.ts
```

## File Splitting

1. Split large files by feature responsibility, not by arbitrary line count alone.
2. A page entry should orchestrate data and layout, not contain all form, table, modal, and request logic.
3. A single file should have one main responsibility.
4. Do not combine page orchestration, complex form logic, table column definitions, request logic, and modal internals in one file.
5. Ordinary source files should target fewer than 300 lines.
6. Files above 400 lines must be split.
7. React component files should target fewer than 250 lines.
8. Hooks and utils should target fewer than 200 lines.
9. Type declarations, route tables, static config, and generated files may exceed limits, but must not contain business process logic.

## Comments

Write necessary comments, not noise.

Required comments:

1. Complex business rules, permission logic, state machines, data transformations, compatibility logic, race handling, and error fallback.
2. Temporary workarounds, legacy API compatibility, downgrade paths, and third-party component limitations.
3. Complex Hooks, table column configuration, form dependencies, important side effects, and cleanup boundaries.
4. Public functions, components, or Hooks whose parameter semantics are not obvious.

Comment style:

- Explain why the code exists and what business constraint it preserves.
- Do not repeat obvious code behavior.
- Avoid comments like `// set variable`, `// click handler`, or `// return result`.

Example:

```ts
// Legacy apps may not have terminal; default to PC assets to avoid blank previews.
const terminal = pageInfo?.terminal ?? 'pc';
```
