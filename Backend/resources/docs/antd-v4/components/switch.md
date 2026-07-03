---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "switch"
component: "Switch"
component_code_name: "Switch"
recommended_import: "import { Switch } from 'antd';"
subtitle_zh: "开关"
type_zh: "数据录入"
demo_count: 5
---

# Switch 开关

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Switch`
- 推荐导入：`import { Switch } from 'antd';`
- 组件分类：数据录入
- 简述：开关选择器。

## 示例索引

- 基本（basic）
- 不可用（disabled）
- 文字和图标（text）
- 两种大小（size）
- 加载中（loading）

## 使用文档

开关选择器。

### 何时使用

- 需要表示开关状态/两种状态之间的切换时；
- 和 `checkbox` 的区别是，切换 `switch` 会直接触发状态改变，而 `checkbox` 一般用于状态标记，需要和提交操作配合。

### API

| 参数 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| autoFocus | 组件自动获取焦点 | boolean | false |
| checked | 指定当前是否选中 | boolean | false |
| checkedChildren | 选中时的内容 | ReactNode | - |
| className | Switch 器类名 | string | - |
| defaultChecked | 初始是否选中 | boolean | false |
| disabled | 是否禁用 | boolean | false |
| loading | 加载中的开关 | boolean | false |
| size | 开关大小，可选值：`default` `small` | string | `default` |
| unCheckedChildren | 非选中时的内容 | ReactNode | - |
| onChange | 变化时的回调函数 | function(checked: boolean, event: Event) | - |
| onClick | 点击时的回调函数 | function(checked: boolean, event: Event) | - |

### 方法

| 名称    | 描述     |
| ------- | -------- |
| blur()  | 移除焦点 |
| focus() | 获取焦点 |

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```tsx
import { Switch } from 'antd';
import React from 'react';

const onChange = (checked: boolean) => {
  console.log(`switch to ${checked}`);
};

const App: React.FC = () => <Switch defaultChecked onChange={onChange} />;

export default App;
```

### 不可用

- demo: `disabled`

Switch 失效状态。

```tsx
import { Button, Switch } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [disabled, setDisabled] = useState(true);

  const toggle = () => {
    setDisabled(!disabled);
  };

  return (
    <>
      <Switch disabled={disabled} defaultChecked />
      <br />
      <Button type="primary" onClick={toggle}>
        Toggle disabled
      </Button>
    </>
  );
};

export default App;
```

### 文字和图标

- demo: `text`

带有文字和图标。

```tsx
import { CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { Switch } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Switch checkedChildren="开启" unCheckedChildren="关闭" defaultChecked />
    <br />
    <Switch checkedChildren="1" unCheckedChildren="0" />
    <br />
    <Switch
      checkedChildren={<CheckOutlined />}
      unCheckedChildren={<CloseOutlined />}
      defaultChecked
    />
  </>
);

export default App;
```

### 两种大小

- demo: `size`

`size="small"` 表示小号开关。

```tsx
import { Switch } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Switch defaultChecked />
    <br />
    <Switch size="small" defaultChecked />
  </>
);

export default App;
```

### 加载中

- demo: `loading`

标识开关操作仍在执行中。

```tsx
import { Switch } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Switch loading defaultChecked />
    <br />
    <Switch size="small" loading />
  </>
);

export default App;
```
