# Dependencies And Ant Design

## Dependency Rules

1. Every third-party `import` must be declared in the current package or workspace `package.json`.
2. Runtime libraries belong in `dependencies` or the project-approved runtime dependency location.
3. Build, test, lint, and type-only tools belong in `devDependencies`.
4. Type packages such as `@types/*` must also be declared.
5. Do not assume undeclared transitive dependencies, browser globals, bundler internals, or parent-project dependencies are available.
6. Before adding a new package, confirm existing dependencies cannot satisfy the need.
7. If a new package is truly required, update `package.json` and explain the purpose.
8. Prefer existing project dependencies and wrappers over new libraries.

## Ant Design 4.24.16 Rules

1. Only generate Ant Design `4.24.16` code.
2. If `package.json` does not declare antd compatible with `4.24.16`, stop generating antd-related code and ask the user to confirm or fix the dependency.
3. Do not use antd v5/v6-only APIs or patterns.
4. Do not use v5/v6 theme token APIs, `App`/`useApp`, `theme.algorithm`, or dayjs-by-default assumptions.
5. For new `Modal` and `Drawer` code in antd `4.24.16`, prefer `open`; when maintaining old code, keep existing `visible` usage locally if the project already uses it.
6. `DatePicker`, `TimePicker`, and `RangePicker` default to the antd v4 moment ecosystem unless the project has an explicit replacement.
7. `Form`, `Table`, `Upload`, `Select`, `Tree`, `Modal`, `Drawer`, `message`, `notification`, and `ConfigProvider` are complex components. Follow existing project usage and wrappers.
8. Prefer existing project-level component wrappers. Do not bypass wrappers unless the surrounding code already uses raw antd components.

## Common Red Flags

- Importing `antd/es/theme`, using `theme.useToken`, or writing v5 token logic.
- Using `App.useApp()`.
- Assuming date values are `dayjs` in an antd v4 project.
- Adding `lodash`, `ahooks`, `react-query`, `zustand`, or similar packages without checking `package.json`.
- Importing a utility from a package only because it is common knowledge, not because the project declares it.
