# React 应用开发规范入口

这份规范已拆分为项目入口文件和可复用 skill 文件，避免大模型每次加载过长上下文。

## 当前项目入口

- `AGENTS.md`

`AGENTS.md` 是给 coding agent 的默认项目指令。模型在本工作区生成或修改 React + TypeScript 代码时，应先遵守这里的硬规则。

## 可复用 Skill

- `react-antd-v4-codegen/SKILL.md`

这个目录可作为后续跨项目复用的 skill 雏形。`SKILL.md` 保持短，只写触发场景、硬门槛和加载顺序。

## 详细规则引用

- `react-antd-v4-codegen/references/dependencies-and-antd.md`
  - `package.json` 依赖声明规则
  - Ant Design `4.24.16` 规则
  - 禁止 antd v5/v6 写法

- `react-antd-v4-codegen/references/structure-and-ownership.md`
  - 功能模块拆分
  - 就近归属
  - 文件行数限制
  - 必要注释规则

- `react-antd-v4-codegen/references/react-rules.md`
  - React 状态管理
  - Hooks 规则
  - Effect 规则
  - API、类型、性能、路由、错误边界规则

- `react-antd-v4-codegen/references/review-checklist.md`
  - 代码生成完成前的检查清单
  - 规则冲突时的优先级

## 推荐使用方式

1. 当前项目：保留 `AGENTS.md` 和 `react-antd-v4-codegen/`。
2. 其他项目复用：复制 `react-antd-v4-codegen/`，并在目标项目的 `AGENTS.md` 中引用它。
3. 如果未来正式安装为 Codex skill，将 `react-antd-v4-codegen/` 放入 Codex skills 目录即可。
