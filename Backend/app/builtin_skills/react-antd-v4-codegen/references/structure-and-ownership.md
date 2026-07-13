# 结构与归属

## 就近归属

私有代码保留在拥有它的组件或功能附近。只有在真正复用时才向上移动。

| 代码类型 | 单个组件 | 同功能模块 | 多个功能模块 |
| --- | --- | --- | --- |
| 组件 | 组件目录或当前文件 | `routes/Feature/components/` | `src/components/` |
| Hook | 组件 `hooks/` 或当前文件 | `routes/Feature/hooks/` | `src/hooks/` |
| API 适配器 | 组件附近或模块 `apis.ts` | `routes/Feature/apis.ts` | `src/apis/` |
| 类型 | 组件文件或 `types.ts` | `routes/Feature/types.ts` | `src/typings/` |
| 常量 | 组件文件或 `constants.ts` | `routes/Feature/constants.ts` | `src/constants/` |
| 样式 | 组件目录 | 功能样式目录 | `src/styles/` |
| 工具函数 | 纯函数放组件附近 | `routes/Feature/utils.ts` | `src/utils/` |
| Provider | 组件或功能内部 | `routes/Feature/providers/` | `src/providers/` |

## 提升规则

1. 不要因为代码"可能被复用"就将其提升到全局。
2. 只有在至少出现第二个真实使用场景后才提升。
3. 提升到全局前，移除页面专属的 API、权限、文案和工作流耦合。
4. 全局目录只包含稳定、通用、跨功能的代码。
5. 功能私有代码即使文件较多，也应保留在功能模块内部。

## 功能模块结构

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

## 文件拆分

1. 按功能职责拆分大文件，而非仅按任意行数拆分。
2. 页面入口应编排数据与布局，不应包含全部表单、表格、弹窗和请求逻辑。
3. 单个文件应只有一项主要职责。
4. 不要将页面编排、复杂表单逻辑、表格列定义、请求逻辑和弹窗内部细节混在一个文件里。
5. 普通源文件应控制在 300 行以内。
6. 超过 400 行的文件必须拆分。
7. React 组件文件应控制在 250 行以内。
8. Hook 和工具文件应控制在 200 行以内。
9. 类型声明、路由表、静态配置和生成文件可以超出限制，但不得包含业务流程逻辑。

## 注释

编写必要注释，而非噪音。

必须注释的情况：

1. 复杂业务规则、权限逻辑、状态机、数据转换、兼容逻辑、竞态处理和错误兜底。
2. 临时变通方案、旧版 API 兼容、降级路径和第三方组件限制。
3. 复杂 Hook、表格列配置、表单依赖、重要副作用和清理边界。
4. 参数语义不明显公开函数、组件或 Hook。

注释风格：

- 说明代码为什么存在、保护了什么业务约束。
- 不要重复显而易见的代码行为。
- 避免写 `// 设置变量`、`// 点击处理` 或 `// 返回结果` 这类注释。

示例：

```ts
// 旧版应用可能没有终端信息；默认使用 PC 资源以避免预览白屏。
const terminal = pageInfo?.terminal ?? 'pc';
```
