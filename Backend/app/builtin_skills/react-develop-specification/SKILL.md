---
name: react-develop-specification
description: 一套完整的 React 项目开发规范，覆盖框架介绍、语言规范、命名规范、代码规范、安全规范、工程规范和工程示例。当用户提到编写前端代码、编写 React 代码、评审 React 代码、React 项目规范、命名约定、代码风格、组件设计、hooks 使用、setState/useState、JSX 书写、生命周期、Fragment、Ref、样式命名、a11y、dangerouslySetInnerHTML、XSS 防护、TypeScript with React、npm 包选型、构建工具、ESLint/Prettier/Stylelint、单元测试、项目目录结构等相关需求时使用此技能。
---

# React 开发规范

本技能封装了一套完整的 React 项目开发规范，条目分为 **【强制】** 和 **【推荐】** 两类。当用户询问 React 相关的规范、约束、最佳实践，或让你写 / 评审 React 代码时，按本规范给出建议或直接产出符合规范的代码。

## 使用原则

- **【强制】** 项必须遵守，涉及正确性、安全性或关键一致性；**【推荐】** 项在合理情境下遵守。
- 用户明确询问某一方面（如命名、hooks、安全）时，聚焦回答，不要把整份规范都倒出来。
- 用户让你直接写代码时，按规范产出可运行代码，简短说明命中的关键规范条目即可。
- 用户已有项目风格且与本规范冲突时，尊重用户既有约定；仅在明显影响安全或质量时提出建议。
- 规范与用户明确要求冲突时，以用户要求为准，并简短告知本规范建议。

## 规范总览

- **语言规范**：TypeScript 作为项目开发语言。
- **命名规范（6 条）**：文件、组件、类型统一 PascalCase；函数与属性 camelCase；jsx 文件扩展名为 tsx；样式采用 CSS Module + BEM。
- **代码规范（45 条）**：导入顺序、any 使用、JSX 写法、children/属性传递、解构、默认值、Fragment、层级、废弃 API、setState/useState、key、生命周期、事件、Hook 规则、ref、DOM 属性、a11y 属性等。
- **安全规范（4 条）**：过滤 dangerouslySetInnerHTML、禁止直接注入 DOM HTML、校验用户输入的 href/src。
- **工程规范**：版本、组件定义（函数组件 / 类组件）、npm 包选型（路由、状态、组件库、其他库）、前端工程化（格式化、代码/样式校验、提交校验、测试、调试）。
- **工程示例**：推荐的项目目录结构。

## 参考文档索引

规范细则按章节拆分到 `references/`。用户询问对应领域时，读取对应文件后再作答；写代码前若不确定某规范细节，先查阅对应文件。

| 场景                                                                           | 阅读文件                                 |
| ------------------------------------------------------------------------------ | ---------------------------------------- |
| React 简介、术语解释（props/state/生命周期/context/Effect/CSR/SSR）            | `references/00-framework-intro.md`       |
| 文件、组件、函数、属性、类型、样式的命名                                       | `references/01-naming-standards.md`      |
| JSX 写法、Hook 规则、生命周期、事件、ref、DOM 属性、a11y 等代码规范（45 条）   | `references/02-coding-standards.md`      |
| 富文本渲染、DOM 注入、用户输入用于 href/src 的 XSS 防护                        | `references/03-security-standards.md`    |
| React 版本、组件定义方式、npm 包选型/ESLint/Prettier/Stylelint/husky/jest 配置 | `references/04-engineering-standards.md` |
| 推荐的项目目录结构与工程示例                                                   | `references/05-project-example.md`       |
| React 版本差异、tsconfig 建议、过时 API、SSR 使用场景                          | `references/06-appendix.md`              |

## 常见回答方式

**用户问：文件应该怎么命名？**
→ 读取 `references/01-naming-standards.md`，说明单文件 tsx 用 `PascalCase.tsx`，目录形式使用 `TestComponent/index.tsx`；含 jsx 的文件扩展名必须为 tsx；组件/类型 PascalCase，函数/属性 camelCase。

**用户问：能不能在 componentDidMount 里 setState？**
→ 读取 `references/02-coding-standards.md` 第 3.3.18 条：**【强制】** 禁止在 componentDidMount 中同步调用 setState；通过异步请求获取数据在 componentDidMount 中更新状态不受此限制。

**用户让你写一个用户信息卡片组件**
→ 直接按规范产出：函数组件 + `PascalCase.tsx` 文件名 + TypeScript Props 接口（PascalCase） + 属性解构 + 自闭合标签 + 事件用箭头函数 + 图片带 alt，末尾简短点出遵循的核心规范条目。

**用户问：`dangerouslySetInnerHTML` 能用吗？**
→ 读取 `references/03-security-standards.md`：可以用，但**必须**先用 DOMPurify 等库对数据做过滤（3.4.1），并且元素内不能再添加 children（对应 3.3.10）。

## 强制条目速查（写 / 评审代码时优先自检）

- 文件、组件、类型：PascalCase；函数、属性：camelCase。
- 含 JSX 的文件扩展名一律 `.tsx`。
- 导入语句写在其他语句之前。
- JSX 属性值使用单引号或模板字符串；`style` 必须为对象；不能有重复属性；组件必须先声明再使用。
- 不将 `children` 作为组件属性传递；`dangerouslySetInnerHTML` 元素内不能有 children。
- 组件的 render 必须有 return；render 中不能修改状态；无状态组件不能使用 `this`。
- 使用 `setState` / `useState` 更新 state，禁止直接改 state。
- 使用含特定意义的唯一 id 作为 key，不用 index。
- 禁止在 `componentDidMount` / `componentDidUpdate` / `componentWillUpdate` 中**同步**调用 setState。
- Hook 必须在函数组件或自定义 Hook 顶层调用，不能在循环、条件、嵌套函数中调用；自定义 hook 以 `use` 开头。
- ref 不使用字符串，采用回调函数、`useRef` 或 `React.createRef`。
- 不使用未知 / 已废弃 DOM 属性；`<html>` 有 lang、`iframe` 有 title、`img` 有 alt。
- 不再使用已废弃 API（见附录 6.3）。
- JSX 层级不超过 15 层（高：6，中：10，低：15）。
- 涉及富文本或用户输入 URL 时必须做过滤和校验（安全规范 3.4）。

产出代码时，如果不确定某条规范的正例反例，读取 `references/02-coding-standards.md` 或对应文件确认，不要凭印象输出。
