# Ant Design v4 JSON 组件描述

本目录定义一套面向低代码编辑器的 Ant Design v4 组件元数据协议。当前先提供
`Button` 和 `Input` 两个样例，用于确认描述粒度和运行时约定，再按相同结构扩展到
全部组件。

## 目录结构

```text
docs/antd-v4/
├── catalog.json
├── examples/
│   ├── button-instance.json
│   └── button-instance.generated.tsx
├── schema/
│   └── component.schema.json
└── components/
    ├── button.json
    └── input.json
```

## 最终组件 JSON 示例

如果页面中有一个“提交订单”按钮，编辑器最终保存的是
[`examples/button-instance.json`](./examples/button-instance.json) 这样的组件实例。
它可以直接生成
[`examples/button-instance.generated.tsx`](./examples/button-instance.generated.tsx)
所展示的 JSX。

`components/button.json` 则是提供给编辑器的元数据，用于告诉属性面板 Button
支持配置哪些内容。两者用途不同：

- `examples/button-instance.json`：页面数据，交给渲染器生成真实 Button。
- `components/button.json`：组件能力说明，交给编辑器生成属性和事件配置面板。

## 可视化搭建实例

组件实例保存用户在属性面板和事件面板中的配置：

```json
{
  "id": "submitOrderButton",
  "component": "Button",
  "props": {
    "text": "提交订单",
    "type": "primary",
    "icon": "CheckOutlined",
    "loading": {
      "binding": "order.submitting"
    },
    "disabled": {
      "condition": {
        "field": "order.items.length",
        "operator": "equal",
        "value": 0
      }
    }
  },
  "events": {
    "click": [
      {
        "action": "submitForm",
        "formId": "orderForm"
      }
    ]
  }
}
```

对应 JSX：

```tsx
<Button
  type="primary"
  icon={<CheckOutlined />}
  loading={order.submitting}
  disabled={order.items.length === 0}
  onClick={() => orderForm.submit()}
>
  提交订单
</Button>
```

未被用户修改的默认属性不写入 JSON。例如 Button 默认的 `size="middle"`、
`htmlType="button"`、`danger={false}` 都应由 Ant Design 自己处理。

配置项对应可视化编辑器控件：

- `text`：文本输入框。
- `type`：下拉选择器。
- `icon`：图标选择器。
- `binding`：数据源选择器。
- `condition`：字段、运算符和值组成的条件编辑器。
- `events.click`：点击事件动作编排器。

组件版本、import、TypeScript 类型和函数实现属于源码生成器，不写入页面实例。
生成器根据组件元数据将 `text` 转为 `children`，将 `click` 转为 `onClick`，并将
绑定、条件和动作编译为 React 表达式及事件函数。

## 核心设计

- `props`：可在属性面板配置的组件属性。
- `events`：组件能发出的事件，以及事件参数、可提取字段和动作绑定方式。
- `slots`：`children`、`icon`、`prefix` 等 ReactNode 属性的可序列化表示。
- `methods`：通过组件 ref 暴露的命令式方法。
- `extends`：继承的原生 HTML 属性或共享属性集，避免在每个组件重复数百项 DOM 属性。
- `examples`：可以直接交给低代码渲染器消费的实例 JSON。

每项属性都区分：

- `valueType`：运行时值类型。
- `editor`：编辑器应使用的控件。
- `defaultValue`：Ant Design 默认值。
- `required`：是否必填。
- `bindable`：是否允许表达式或状态绑定。
- `since`：该能力首次出现的 Ant Design v4 版本。

## 可序列化值

普通值直接写入 JSON：

```json
{
  "type": "primary",
  "disabled": false
}
```

动态值使用绑定表达式：

```json
{
  "$bind": "state.form.submitting",
  "fallback": false
}
```

ReactNode 使用节点描述：

```json
{
  "$node": "icon",
  "name": "SearchOutlined"
}
```

事件不保存 JavaScript 函数，而是保存动作列表：

```json
{
  "onClick": [
    {
      "action": "state.set",
      "args": {
        "path": "dialog.visible",
        "value": true
      }
    }
  ]
}
```

事件参数可通过 `$event` 引用：

```json
{
  "onChange": [
    {
      "action": "state.set",
      "args": {
        "path": "form.username",
        "value": {
          "$event": "target.value"
        }
      }
    }
  ]
}
```

## 运行时约定

渲染器负责完成以下转换：

1. 将普通 JSON 值直接映射到组件 props。
2. 解析 `$bind`，订阅对应状态并生成 prop 值。
3. 将 `$node` 转换为文本、图标或嵌套组件。
4. 将事件动作数组编译为 React 事件处理函数。
5. 仅允许执行动作注册表中已注册的动作，禁止从 JSON 执行任意脚本。
6. 根据 `extends` 白名单透传合法的 DOM、ARIA 和 `data-*` 属性。

## 当前依据

样例基于项目安装的 `antd@4.24.16` 类型声明设计。`source.typeFile` 指向本地
TypeScript 声明文件，便于后续自动生成和人工校验。
