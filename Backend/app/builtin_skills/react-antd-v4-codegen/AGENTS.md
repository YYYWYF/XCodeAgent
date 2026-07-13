# 编码代理指令

在本工作区生成或修改 React + TypeScript 代码时使用这些规则。

主要规则来源：

- `react-antd-v4-codegen/SKILL.md`

硬性要求：

1. 只编写 Ant Design `4.24.16` 代码。不得生成 antd v5/v6 API。
2. 每个第三方引入必须已在当前包或工作区的 `package.json` 中声明。
3. 按功能拆分文件。避免大文件，保持按功能易于审查。
4. 使用就近归属：私有代码保留在组件或功能模块内部；只有跨模块可复用的代码才移到全局目录。
5. 遵循 React Hook 规则。切勿在条件语句中或提前 return 之后调用 Hook。
6. 避免不必要的 `useEffect`；不要存储派生状态。
7. 显式处理加载中、空数据、错误、无权限和未找到状态。
8. 为复杂业务规则、兼容逻辑、竞态处理和副作用边界编写必要注释。
9. 优先使用已有的项目模式、包装器、请求 Hook、组件、样式和命名。
10. 不要引入无关的重构。

完成 React 工作前，过一遍 `react-antd-v4-codegen/references/review-checklist.md`。
