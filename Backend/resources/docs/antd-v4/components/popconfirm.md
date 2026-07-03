---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "popconfirm"
component: "Popconfirm"
component_code_name: "Popconfirm"
recommended_import: "import { Popconfirm } from 'antd';"
subtitle_zh: "气泡确认框"
type_zh: "反馈"
demo_count: 7
---

# Popconfirm 气泡确认框

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Popconfirm`
- 推荐导入：`import { Popconfirm } from 'antd';`
- 组件分类：反馈
- 简述：点击元素，弹出气泡式的确认框。

## 示例索引

- 基本（basic）
- 国际化（locale）
- 位置（placement）
- 条件触发（dynamic-trigger）
- 自定义 Icon 图标（icon）
- 异步关闭（async）
- 基于 Promise 的异步关闭（promise）

## 使用文档

点击元素，弹出气泡式的确认框。

### 何时使用

目标元素的操作需要用户进一步的确认时，在目标元素附近弹出浮层提示，询问用户。

和 `confirm` 弹出的全屏居中模态对话框相比，交互形式更轻量。

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| cancelButtonProps | cancel 按钮 props | ButtonProps | - |  |
| cancelText | 取消按钮文字 | string | `取消` |  |
| disabled | 阻止点击 Popconfirm 子元素时弹出确认框 | boolean | false |  |
| icon | 自定义弹出气泡 Icon 图标 | ReactNode | &lt;ExclamationCircle /> |  |
| okButtonProps | ok 按钮 props | ButtonProps | - |  |
| okText | 确认按钮文字 | string | `确定` |  |
| okType | 确认按钮类型 | string | `primary` |  |
| showCancel | 是否显示取消按钮 | boolean | true | 4.18.0 |
| title | 确认框的描述 | ReactNode \| () => ReactNode | - |  |
| onCancel | 点击取消的回调 | function(e) | - |  |
| onConfirm | 点击确认的回调 | function(e) | - |  |

更多属性请参考 Tooltip。

### 注意

请确保 `Popconfirm` 的子元素能接受 `onMouseEnter`、`onMouseLeave`、`onFocus`、`onClick` 事件。

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```tsx
import { message, Popconfirm } from 'antd';
import React from 'react';

const confirm = (e: React.MouseEvent<HTMLElement>) => {
  console.log(e);
  message.success('Click on Yes');
};

const cancel = (e: React.MouseEvent<HTMLElement>) => {
  console.log(e);
  message.error('Click on No');
};

const App: React.FC = () => (
  <Popconfirm
    title="Are you sure to delete this task?"
    onConfirm={confirm}
    onCancel={cancel}
    okText="Yes"
    cancelText="No"
  >
    <a href="#">Delete</a>
  </Popconfirm>
);

export default App;
```

### 国际化

- demo: `locale`

使用 `okText` 和 `cancelText` 自定义按钮文字。

```tsx
import { Popconfirm } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Popconfirm title="Are you sure？" okText="Yes" cancelText="No">
    <a href="#">Delete</a>
  </Popconfirm>
);

export default App;
```

### 位置

- demo: `placement`

位置有十二个方向。如需箭头指向目标元素中心，可以设置 `arrowPointAtCenter`。

```tsx
import { Button, message, Popconfirm } from 'antd';
import React from 'react';

const text = 'Are you sure to delete this task?';

const confirm = () => {
  message.info('Clicked on Yes.');
};

const App: React.FC = () => (
  <div className="demo">
    <div style={{ marginLeft: 70, whiteSpace: 'nowrap' }}>
      <Popconfirm placement="topLeft" title={text} onConfirm={confirm} okText="Yes" cancelText="No">
        <Button>TL</Button>
      </Popconfirm>
      <Popconfirm placement="top" title={text} onConfirm={confirm} okText="Yes" cancelText="No">
        <Button>Top</Button>
      </Popconfirm>
      <Popconfirm
        placement="topRight"
        title={text}
        onConfirm={confirm}
        okText="Yes"
        cancelText="No"
      >
        <Button>TR</Button>
      </Popconfirm>
    </div>
    <div style={{ width: 70, float: 'left' }}>
      <Popconfirm placement="leftTop" title={text} onConfirm={confirm} okText="Yes" cancelText="No">
        <Button>LT</Button>
      </Popconfirm>
      <Popconfirm placement="left" title={text} onConfirm={confirm} okText="Yes" cancelText="No">
        <Button>Left</Button>
      </Popconfirm>
      <Popconfirm
        placement="leftBottom"
        title={text}
        onConfirm={confirm}
        okText="Yes"
        cancelText="No"
      >
        <Button>LB</Button>
      </Popconfirm>
    </div>
    <div style={{ width: 70, marginLeft: 304 }}>
      <Popconfirm
        placement="rightTop"
        title={text}
        onConfirm={confirm}
        okText="Yes"
        cancelText="No"
      >
        <Button>RT</Button>
      </Popconfirm>
      <Popconfirm placement="right" title={text} onConfirm={confirm} okText="Yes" cancelText="No">
        <Button>Right</Button>
      </Popconfirm>
      <Popconfirm
        placement="rightBottom"
        title={text}
        onConfirm={confirm}
        okText="Yes"
        cancelText="No"
      >
        <Button>RB</Button>
      </Popconfirm>
    </div>
    <div style={{ marginLeft: 70, clear: 'both', whiteSpace: 'nowrap' }}>
      <Popconfirm
        placement="bottomLeft"
        title={text}
        onConfirm={confirm}
        okText="Yes"
        cancelText="No"
      >
        <Button>BL</Button>
      </Popconfirm>
      <Popconfirm placement="bottom" title={text} onConfirm={confirm} okText="Yes" cancelText="No">
        <Button>Bottom</Button>
      </Popconfirm>
      <Popconfirm
        placement="bottomRight"
        title={text}
        onConfirm={confirm}
        okText="Yes"
        cancelText="No"
      >
        <Button>BR</Button>
      </Popconfirm>
    </div>
  </div>
);

export default App;
```

### 条件触发

- demo: `dynamic-trigger`

可以判断是否需要弹出。

```tsx
import { message, Popconfirm, Switch } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [condition, setCondition] = useState(true);

  const changeCondition = (checked: boolean) => {
    setCondition(checked);
  };

  const confirm = () => {
    setOpen(false);
    message.success('Next step.');
  };

  const cancel = () => {
    setOpen(false);
    message.error('Click on cancel.');
  };

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setOpen(newOpen);
      return;
    }
    // Determining condition before show the popconfirm.
    console.log(condition);
    if (condition) {
      confirm(); // next step
    } else {
      setOpen(newOpen);
    }
  };

  return (
    <div>
      <Popconfirm
        title="Are you sure delete this task?"
        open={open}
        onOpenChange={handleOpenChange}
        onConfirm={confirm}
        onCancel={cancel}
        okText="Yes"
        cancelText="No"
      >
        <a href="#">Delete a task</a>
      </Popconfirm>
      <br />
      <br />
      Whether directly execute：
      <Switch defaultChecked onChange={changeCondition} />
    </div>
  );
};

export default App;
```

### 自定义 Icon 图标

- demo: `icon`

自定义提示 `icon`。

```tsx
import { QuestionCircleOutlined } from '@ant-design/icons';
import { Popconfirm } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Popconfirm title="Are you sure？" icon={<QuestionCircleOutlined style={{ color: 'red' }} />}>
    <a href="#">Delete</a>
  </Popconfirm>
);

export default App;
```

### 异步关闭

- demo: `async`

点击确定后异步关闭气泡确认框，例如提交表单。

```tsx
import { Button, Popconfirm } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);

  const showPopconfirm = () => {
    setOpen(true);
  };

  const handleOk = () => {
    setConfirmLoading(true);

    setTimeout(() => {
      setOpen(false);
      setConfirmLoading(false);
    }, 2000);
  };

  const handleCancel = () => {
    console.log('Clicked cancel button');
    setOpen(false);
  };

  return (
    <Popconfirm
      title="Title"
      open={open}
      onConfirm={handleOk}
      okButtonProps={{ loading: confirmLoading }}
      onCancel={handleCancel}
    >
      <Button type="primary" onClick={showPopconfirm}>
        Open Popconfirm with async logic
      </Button>
    </Popconfirm>
  );
};

export default App;
```

### 基于 Promise 的异步关闭

- demo: `promise`

点击确定后异步关闭 Popconfirm，例如提交表单。

```tsx
import { Button, Popconfirm } from 'antd';
import React from 'react';

const App: React.FC = () => {
  const confirm = () =>
    new Promise(resolve => {
      setTimeout(() => resolve(null), 3000);
    });

  return (
    <Popconfirm title="Title" onConfirm={confirm} onOpenChange={() => console.log('open change')}>
      <Button type="primary">Open Popconfirm with Promise</Button>
    </Popconfirm>
  );
};

export default App;
```
