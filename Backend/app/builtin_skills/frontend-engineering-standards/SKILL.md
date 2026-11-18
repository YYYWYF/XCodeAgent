---
name: frontend-engineering-standards
description: 前端 Build 的 API、查询状态、组件、类型和代码分层规范。
version: "1.0"
---

# Frontend Engineering Standards

以当前模板依赖、模板现有导出和已确认实施契约为依据。生成应用使用 React、TypeScript、Ant Design、ProComponents、ahooks、React Router 与 Rsbuild。框架受管文件由模板边界 Skill 保护。

| 规则 ID | 适用任务 | 要求 |
| --- | --- | --- |
| FE-API-BOUNDARY | API 模块、页面 | 业务请求封装在 `src/apis/`，API 模块复用模板默认导出的 `service`。保持正式方法、路径、参数和字段；成功直接返回响应业务值。保留原有符合契约的导出。静态模块遵循静态数据 Skill。 |
| FE-API-USE-REQUEST | 页面 | 查询、列表和详情通过 ahooks `useRequest` 管理。简单查询直接在页面编写。新增、修改和删除可在事件处理器或 `onFinish` 直接调用业务 API。 |
| FE-API-UI-DECOUPLING | 页面 | ProTable 通过 `dataSource`、`loading`、受控分页消费状态；ProFormSelect 用 `options`。业务请求独立于组件的 `request` 属性。 |
| FE-CONSTANTS | 页面 | 将有业务含义的共享字典、枚举映射和固定选项放在授权的 `src/constants/` 业务文件。一次性局部值留在使用处。 |
| FE-TYPES | API 模块、页面 | 复用已有 API 类型；跨页面共用的类型放在授权的 `src/typings/`，局部 UI 类型就近定义。避免重复类型和无必要的 `any`。 |
| FE-PRO-COMPONENTS | 页面 | 按已确认设计选择 ProTable、ProList、ProCard、ProForm、ModalForm、DrawerForm 或 StepsForm。基础交互使用 Ant Design 组件。 |
| FE-HOOKS | 页面 | 遵守 Hooks 调用顺序；复杂编排、联动和可复用请求逻辑可抽取到已授权 Hook 文件。订阅等外部同步使用 Effect，请求状态由 useRequest 管理。 |
| FE-CODE-LAYERING | API 模块、页面 | 页面组合、交互和状态放 pages；HTTP 和静态访问放 apis；可复用 UI 放 components；常量、类型、Hook、纯工具分别按职责组织。新增文件须由任务明确授权。 |

工程验收规则

- `FE-NO-DIRECT-HTTP` 禁止页面、业务组件及任务 Hook 直接调用 fetch、axios、service。
- `FE-NO-UI-REQUEST-PROP` 禁止 ProTable、ProFormSelect 等通过 JSX `request` 绑定业务 API。
- `FE-QUERY-USE-REQUEST` 对可确定的契约查询，检查其实际 API 调用由 ahooks useRequest 服务函数管理。

按需参考

- [业务 API 与 useRequest](references/api-request-patterns.md)
- [表格、表单和选项](references/component-patterns.md)
- [复杂请求与页面分层](references/page-structure-examples.md)
