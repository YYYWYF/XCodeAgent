# React Rules

## General React Rules

1. Keep render pure. Do not request data, subscribe, mutate external state, log analytics, or touch the DOM during render.
2. Prefer existing project patterns, wrappers, request hooks, styles, and naming.
3. Do not perform unrelated refactors.
4. Always handle loading, empty, error, forbidden, and notFound states where applicable.

## State

Use the smallest suitable state owner:

| State Type | Preferred Owner |
| --- | --- |
| Single-component UI state | `useState` |
| Complex local interaction state | `useReducer` |
| Shareable/restorable page state | URL path or query params |
| Low-frequency cross-tree state | Context |
| High-frequency global collaboration state | existing external store |
| Server data | existing project request Hook or request cache |
| Form data | form library or local controlled state |

Avoid duplicate state:

```tsx
const canEdit = appInfo?.appStatus === AppStatusType.NORMAL && hasPermission;
```

Do not store that derived value in state and sync it with `useEffect`.

## Context

1. Context is for low-frequency shared state.
2. Do not put huge API responses, large lists, or fast-changing editor state into one Context.
3. Memoize Context values with `useMemo`.
4. Keep Provider functions stable when needed.
5. Split Context by update frequency, such as state context and actions context.

## Hooks

1. Hooks only run in React function components or custom Hooks.
2. Hooks must be called at top level in the same order on every render.
3. Never call Hooks inside conditions, loops, event handlers, ordinary functions, or `try/catch`.
4. Never call Hooks after an early return.
5. Do not remove dependencies to silence `exhaustive-deps`; restructure instead.
6. Custom Hooks must start with `use` and express business meaning.
7. Component-private Hooks stay in the component directory; feature-shared Hooks stay in the feature `hooks/`; cross-feature Hooks go to `src/hooks/`.

Correct:

```tsx
function Editor() {
  const { data, loading, error } = usePageInfo();
  const contextValue = useMemo(() => ({ data }), [data]);

  if (error) return <ErrorView />;
  if (loading) return <Loading />;

  return (
    <EditorContext.Provider value={contextValue}>
      <Content />
    </EditorContext.Provider>
  );
}
```

Incorrect:

```tsx
function Editor() {
  const { data, loading } = usePageInfo();
  if (loading) return <Loading />;

  const contextValue = useMemo(() => ({ data }), [data]);
  return <Content />;
}
```

## Effect

Use `useEffect` only to synchronize with external systems:

1. Event subscriptions.
2. Browser APIs, DOM APIs, and third-party SDKs.
3. External connections and cleanup.
4. Manual request/cancellation flows.
5. Analytics, logs, and page title side effects.

Do not use Effect for derived state:

```tsx
const pageTitle = `${pageInfo?.name ?? ''}-${appInfo?.appName ?? ''}`;
```

Effects that subscribe, create timers, start async flows, or allocate external resources must cleanup and handle races.

## API

1. `service/` owns request instances, interceptors, login behavior, encryption, and error normalization.
2. `apis/` owns business API functions only; it should not contain component state logic.
3. Components and business Hooks use existing project request Hooks.
4. Do not introduce a new request library unless declared and approved.
5. API inputs and outputs must have explicit types.
6. Independent requests should run in parallel.
7. Dependent requests use `ready` or the project equivalent.
8. Search, autocomplete, filters, and autosave must debounce/throttle and handle race conditions.
9. Write operations must consider retry, idempotency, versions, or locks.

## Components

1. Prefer ordinary function components over `React.FC`.
2. Props must have explicit types.
3. Declare `children?: React.ReactNode` explicitly when needed.
4. Page components orchestrate data and layout; UI components display and handle local interaction.
5. Large pages, editors, previewers, and low-frequency modules may use `React.lazy`.
6. Lazy-loaded code must have `Suspense`; chunk load failures need Error Boundary coverage.
7. Complex forms, tables, filters, modals, detail panels, and toolbars should be split by feature.

## Types

1. Object shapes prefer `interface`.
2. Union, utility, and mapped types use `type`.
3. Stable backend numeric codes may use `enum`.
4. Frontend option lists prefer `as const` plus a union type.
5. API params, API responses, component props, and UI state must be modeled separately.
6. Avoid `any`; use `unknown` and narrow. If an external library lacks types, isolate `any` locally and explain why.
7. Do not pass raw backend response objects deep through the component tree.

## Performance

1. Use parallel async work when operations are independent.
2. Use lazy loading for large modules.
3. Use pagination, virtual scroll, or chunk rendering for long lists.
4. List keys must be stable and unique; avoid index keys for mutable lists.
5. Context value references must be stable.
6. Do not wrap every function in `useCallback` or every object in `useMemo`.
7. Stabilize references only for Context, Hook dependencies, `React.memo` children, or expensive computation.

## Routing And Errors

1. Route constants should be centralized.
2. Use configuration-style routing such as `useRoutes` when that matches the project.
3. Page-level Providers may live on route nodes.
4. Dynamic routes must validate backend config and handle forbidden/notFound.
5. Route fallback must render NotFound.
6. Route pages, lazy modules, and editor core areas need Error Boundary coverage.
7. Prefer existing project Error Boundary; do not add an undeclared library for this.
