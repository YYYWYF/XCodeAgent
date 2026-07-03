---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "alert"
component: "Alert"
component_code_name: "Alert"
recommended_import: "import { Alert } from 'antd';"
subtitle_zh: "警告提示"
type_zh: "反馈"
demo_count: 11
---

# Alert 警告提示

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Alert`
- 推荐导入：`import { Alert } from 'antd';`
- 组件分类：反馈
- 简述：警告提示，展现需要关注的信息。

## 示例索引

- 基本（basic）
- 四种样式（style）
- 可关闭的警告提示（closable）
- 含有辅助性文字介绍（description）
- 图标（icon）
- 自定义关闭（close-text）
- 顶部公告（banner）
- 轮播的公告（loop-banner）
- 平滑地卸载（smooth-closed）
- React 错误处理（error-boundary）
- 操作（action）

## 使用文档

警告提示，展现需要关注的信息。

### 何时使用

- 当某个页面需要向用户显示警告的信息时。
- 非浮层的静态展现形式，始终展现，不会自动消失，用户可以点击关闭。

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| action | 自定义操作项 | ReactNode | - | 4.9.0 |
| afterClose | 关闭动画结束后触发的回调函数 | () => void | - |  |
| banner | 是否用作顶部公告 | boolean | false |  |
| closable | 默认不显示关闭按钮 | boolean | - |  |
| closeText | 自定义关闭按钮 | ReactNode | - |  |
| closeIcon | 自定义关闭 Icon | ReactNode | `` | 4.18.0 |
| description | 警告提示的辅助性文字介绍 | ReactNode | - |  |
| icon | 自定义图标，`showIcon` 为 true 时有效 | ReactNode | - |  |
| message | 警告提示内容 | ReactNode | - |  |
| showIcon | 是否显示辅助图标 | boolean | false，`banner` 模式下默认值为 true |  |
| type | 指定警告提示的样式，有四种选择 `success`、`info`、`warning`、`error` | string | `info`，`banner` 模式下默认值为 `warning` |  |
| onClose | 关闭时触发的回调函数 | (e: MouseEvent) => void | - |  |

#### Alert.ErrorBoundary

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| description | 自定义错误内容，如果未指定会展示报错堆栈 | ReactNode | {{ error stack }} |  |
| message | 自定义错误标题，如果未指定会展示原生报错信息 | ReactNode | {{ error }} |  |

## 示例代码

### 基本

- demo: `basic`

最简单的用法，适用于简短的警告提示。

```tsx
import { Alert } from 'antd';
import React from 'react';

const App: React.FC = () => <Alert message="Success Text" type="success" />;

export default App;
```

### 四种样式

- demo: `style`

共有四种样式 `success`、`info`、`warning`、`error`。

```tsx
import { Alert } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Alert message="Success Text" type="success" />
    <Alert message="Info Text" type="info" />
    <Alert message="Warning Text" type="warning" />
    <Alert message="Error Text" type="error" />
  </>
);

export default App;
```

### 可关闭的警告提示

- demo: `closable`

显示关闭按钮，点击可关闭警告提示。

```tsx
import { Alert } from 'antd';
import React from 'react';

const onClose = (e: React.MouseEvent<HTMLButtonElement, MouseEvent>) => {
  console.log(e, 'I was closed.');
};

const App: React.FC = () => (
  <>
    <Alert
      message="Warning Text Warning Text Warning TextW arning Text Warning Text Warning TextWarning Text"
      type="warning"
      closable
      onClose={onClose}
    />
    <Alert
      message="Error Text"
      description="Error Description Error Description Error Description Error Description Error Description Error Description"
      type="error"
      closable
      onClose={onClose}
    />
  </>
);

export default App;
```

### 含有辅助性文字介绍

- demo: `description`

含有辅助性文字介绍的警告提示。

```tsx
import { Alert } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Alert
      message="Success Text"
      description="Success Description Success Description Success Description"
      type="success"
    />
    <Alert
      message="Info Text"
      description="Info Description Info Description Info Description Info Description"
      type="info"
    />
    <Alert
      message="Warning Text"
      description="Warning Description Warning Description Warning Description Warning Description"
      type="warning"
    />
    <Alert
      message="Error Text"
      description="Error Description Error Description Error Description Error Description"
      type="error"
    />
  </>
);

export default App;
```

### 图标

- demo: `icon`

可口的图标让信息类型更加醒目。

```tsx
import { Alert } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Alert message="Success Tips" type="success" showIcon />
    <Alert message="Informational Notes" type="info" showIcon />
    <Alert message="Warning" type="warning" showIcon closable />
    <Alert message="Error" type="error" showIcon />
    <Alert
      message="Success Tips"
      description="Detailed description and advice about successful copywriting."
      type="success"
      showIcon
    />
    <Alert
      message="Informational Notes"
      description="Additional description and information about copywriting."
      type="info"
      showIcon
    />
    <Alert
      message="Warning"
      description="This is a warning notice about copywriting."
      type="warning"
      showIcon
      closable
    />
    <Alert
      message="Error"
      description="This is an error message about copywriting."
      type="error"
      showIcon
    />
  </>
);

export default App;
```

### 自定义关闭

- demo: `close-text`

可以自定义关闭，自定义的文字会替换原先的关闭 `Icon`。

```tsx
import { Alert } from 'antd';
import React from 'react';

const App: React.FC = () => <Alert message="Info Text" type="info" closeText="Close Now" />;

export default App;
```

### 顶部公告

- demo: `banner`

页面顶部通告形式，默认有图标且 `type` 为 'warning'。

```tsx
import { Alert } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Alert message="Warning text" banner />
    <br />
    <Alert
      message="Very long warning text warning text text text text text text text"
      banner
      closable
    />
    <br />
    <Alert showIcon={false} message="Warning text without icon" banner />
    <br />
    <Alert type="error" message="Error text" banner />
  </>
);

export default App;
```

### 轮播的公告

- demo: `loop-banner`

配合 react-text-loop-next 或 react-fast-marquee 实现消息轮播通知栏。

```tsx
import { Alert } from 'antd';
import React from 'react';
import Marquee from 'react-fast-marquee';

const App: React.FC = () => (
  <Alert
    banner
    message={
      <Marquee pauseOnHover gradient={false}>
        I can be a React component, multiple React components, or just some text.
      </Marquee>
    }
  />
);

export default App;
```

### 平滑地卸载

- demo: `smooth-closed`

平滑、自然的卸载提示。

```tsx
import { Alert } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [visible, setVisible] = useState(true);

  const handleClose = () => {
    setVisible(false);
  };

  return (
    <div>
      {visible ? (
        <Alert message="Alert Message Text" type="success" closable afterClose={handleClose} />
      ) : null}
      <p>placeholder text here</p>
    </div>
  );
};

export default App;
```

### React 错误处理

- demo: `error-boundary`

友好的 React 错误处理 包裹组件。

```tsx
import { Alert, Button } from 'antd';
import React, { useState } from 'react';

const { ErrorBoundary } = Alert;
const ThrowError: React.FC = () => {
  const [error, setError] = useState<Error>();
  const onClick = () => {
    setError(new Error('An Uncaught Error'));
  };

  if (error) {
    throw error;
  }
  return (
    <Button danger onClick={onClick}>
      Click me to throw a error
    </Button>
  );
};

const App: React.FC = () => (
  <ErrorBoundary>
    <ThrowError />
  </ErrorBoundary>
);

export default App;
```

### 操作

- demo: `action`

可以在右上角自定义操作项。

```tsx
import { Alert, Button, Space } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Alert
      message="Success Tips"
      type="success"
      showIcon
      action={
        <Button size="small" type="text">
          UNDO
        </Button>
      }
      closable
    />
    <Alert
      message="Error Text"
      showIcon
      description="Error Description Error Description Error Description Error Description"
      type="error"
      action={
        <Button size="small" danger>
          Detail
        </Button>
      }
    />
    <Alert
      message="Warning Text"
      type="warning"
      action={
        <Space>
          <Button size="small" type="ghost">
            Done
          </Button>
        </Space>
      }
      closable
    />
    <Alert
      message="Info Text"
      description="Info Description Info Description Info Description Info Description"
      type="info"
      action={
        <Space direction="vertical">
          <Button size="small" type="primary">
            Accept
          </Button>
          <Button size="small" danger type="ghost">
            Decline
          </Button>
        </Space>
      }
      closable
    />
  </>
);

export default App;
```
