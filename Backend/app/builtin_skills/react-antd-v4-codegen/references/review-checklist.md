# Review Checklist

Run this checklist before finishing React code generation or review.

## Hard Gates

1. Is antd usage compatible with `antd@4.24.16`?
2. Are all third-party imports declared in `package.json`?
3. Did the implementation avoid antd v5/v6 APIs?
4. Are Hooks called only at top level and before any conditional return?
5. Are loading, empty, error, forbidden, and notFound states handled where applicable?

## Structure

1. Is code split by feature module?
2. Can the user review the change by feature from the directory and file names?
3. Are private components, Hooks, types, constants, styles, providers, APIs, and utils kept near their owner?
4. Has any module-private code been promoted too early to global directories?
5. Are ordinary source files under the target size?
6. Are files over 400 lines split, unless they are allowed exceptions?

## React Quality

1. Is render pure?
2. Is there unnecessary derived state?
3. Is there unnecessary `useEffect`?
4. Do Effects cleanup subscriptions, timers, async races, and external resources?
5. Are Context values memoized and not too broad?
6. Are request dependencies and race conditions handled?
7. Are list keys stable and unique?

## Types And Comments

1. Are API params, responses, props, and UI state typed separately?
2. Is `any` avoided or locally isolated with a reason?
3. Do complex business rules, compatibility logic, race handling, and side-effect boundaries have necessary comments?
4. Are comments explaining why, not repeating what?

## Priority Order

When rules conflict, choose in this order:

1. Correctness: Hooks, types, request states, error fallback.
2. Dependency legality: declared packages only; antd `4.24.16` only.
3. Feature reviewability: split by feature and avoid large files.
4. Project consistency: existing directories, names, wrappers, request APIs, styles.
5. Maintainability: clear state ownership and minimal side effects.
6. Understandability: necessary comments for complex logic.
7. Performance: optimize only where useful.
8. Simplicity: avoid unnecessary abstraction and unrelated refactors.
