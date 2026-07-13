# 依赖与 Ant Design

## 依赖规则

1. 每个第三方 `import` 必须已在当前包或工作区的 `package.json` 中声明。
2. 运行时库应放在 `dependencies` 或项目批准的运行时依赖位置。
3. 构建、测试、lint 和仅类型工具应放在 `devDependencies` 中。
4. `@types/*` 等类型包也必须声明。
5. 不要假设未声明的传递依赖、浏览器全局变量、打包器内部模块或父项目依赖是可用的。
6. 在添加新包之前，先确认现有依赖能否满足需求。
7. 如果确实需要新包，请更新 `package.json` 并说明用途。
8. 优先使用已有的项目依赖和包装器，而非引入新库。

## Ant Design 4.24.16 规则

1. 只生成 Ant Design `4.24.16` 代码。
2. 如果 `package.json` 未声明与 `4.24.16` 兼容的 antd，请停止生成 antd 相关代码，并要求用户确认或修复依赖。
3. 不要使用 antd v5/v6 专属 API 或模式。
4. 不要使用 v5/v6 主题 token API、`App`/`useApp`、`theme.algorithm` 或默认 dayjs 的假设。
5. 优先使用已有的项目级组件包装器。不要绕过包装器，除非周边代码已经在使用原生 antd 组件。

## 常见红线

- 引入 `antd/es/theme`、使用 `theme.useToken` 或编写 v5 token 逻辑。
- 使用 `App.useApp()`。
- 在 antd v4 项目中假设日期值是 `dayjs`。
- 未检查 `package.json` 就添加 `lodash`、`ahooks`、`react-query`、`zustand` 或类似包。
- 仅凭常识就引入某个工具库，而不是因为项目已声明该库。
