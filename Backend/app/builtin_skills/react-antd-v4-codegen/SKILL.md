---
name: react-antd-v4-codegen
description: 在生成、修改或审查必须遵循项目约定、Ant Design 4.24.16、package.json 依赖声明、按功能拆分文件、就近归属、React Hook 规则和必要注释的 React + TypeScript 代码时使用。
---

# React Antd v4 代码生成

在 React + TypeScript 代码生成、修改和审查时使用此技能。

## 必选工作流

0. 将 `REACT_BEST_PRACTICES_GUIDE.md` 和 `AGENTS.md` 作为强制入口指令。
1. 在引入第三方库之前检查相关 `package.json`。
2. 确认 antd 使用目标为 Ant Design `4.24.16`。
3. 遵循就近归属和按功能拆分文件。
4. 保持 React 代码纯净、有类型，并明确处理请求和错误状态。
5. 为复杂或非显而易见的逻辑添加必要注释。
6. 完成前使用审查检查清单。

## 硬门槛

以下情况应停止或要求确认：

- `antd` 缺失或与 `4.24.16` 不兼容，但任务需要 antd 代码。
- 需要的第三方包未在 `package.json` 中声明。
- 请求的实现需要未经批准的新依赖。
- 现有项目模式与这些规则冲突，且影响行为。

## 参考文件

按需读取：

- `REACT_BEST_PRACTICES_GUIDE.md`：项目指南入口和规则映射。
- `AGENTS.md`：React + TypeScript 工作区中编码代理的硬性要求。
- `references/dependencies-and-antd.md`：package.json 依赖规则和 Ant Design `4.24.16` 规则。
- `references/structure-and-ownership.md`：功能模块、就近归属、文件大小限制和注释规则。
- `references/react-rules.md`：React 状态、Hook、Effect、API、类型、性能、路由和错误规则。
- `references/review-checklist.md`：最终代码审查检查清单和优先级顺序。

对于大多数 React 代码生成任务，请阅读 `dependencies-and-antd.md`、`structure-and-ownership.md` 和 `react-rules.md`。对于审查或最终验证，请阅读 `review-checklist.md`。
