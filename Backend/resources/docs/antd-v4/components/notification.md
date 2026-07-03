---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "notification"
component: "Notification"
component_code_name: "notification"
recommended_import: "import { notification } from 'antd';"
subtitle_zh: "通知提醒框"
type_zh: "反馈"
demo_count: 9
---

# Notification 通知提醒框

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`notification`
- 推荐导入：`import { notification } from 'antd';`
- 组件分类：反馈
- 简述：全局展示通知提醒信息。
- 重要说明：notification 是全局通知 API，按小写对象使用，例如 notification.open(...)。

## 示例索引

- 基本（basic）
- 自动关闭的延时（duration）
- 带有图标的通知提醒框（with-icon）
- 自定义按钮（with-btn）
- 自定义图标（custom-icon）
- 位置（placement）
- 自定义样式（custom-style）
- 更新消息内容（update）
- 通过 Hooks 获取上下文（hooks）

## 使用文档

全局展示通知提醒信息。

### 何时使用

在系统四个角显示通知提醒信息。经常用于以下情况：

- 较为复杂的通知内容。
- 带有交互的通知，给出用户下一步的行动点。
- 系统主动推送。

### API

- `notification.success(config)`
- `notification.error(config)`
- `notification.info(config)`
- `notification.warning(config)`
- `notification.warn(config)`
- `notification.open(config)`
- `notification.close(key: String)`
- `notification.destroy()`

config 参数如下：

| 参数 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| bottom | 消息从底部弹出时，距离底部的位置，单位像素 | number | 24 |
| btn | 自定义关闭按钮 | ReactNode | - |
| className | 自定义 CSS class | string | - |
| closeIcon | 自定义关闭图标 | ReactNode | - |
| description | 通知提醒内容，必选 | ReactNode | - |
| duration | 默认 4.5 秒后自动关闭，配置为 null 则不自动关闭 | number | 4.5 |
| getContainer | 配置渲染节点的输出位置 | () => HTMLNode | () => document.body |
| icon | 自定义图标 | ReactNode | - |
| key | 当前通知唯一标志 | string | - |
| message | 通知提醒标题，必选 | ReactNode | - |
| placement | 弹出位置，可选 `topLeft` `topRight` `bottomLeft` `bottomRight` | string | `topRight` |
| style | 自定义内联样式 | CSSProperties | - |
| top | 消息从顶部弹出时，距离顶部的位置，单位像素 | number | 24 |
| onClick | 点击通知时触发的回调函数 | function | - |
| onClose | 当通知关闭时触发 | function | - |

还提供了一个全局配置方法，在调用前提前配置，全局一次生效。

- `notification.config(options)`

  > 当你使用 `ConfigProvider` 进行全局化配置时，系统会默认自动开启 RTL 模式。(4.3.0+)
  >
  > 当你想单独使用，可通过如下设置开启 RTL 模式。

```js
notification.config({
  placement: 'bottomRight',
  bottom: 50,
  duration: 3,
  rtl: true,
});
```

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| bottom | 消息从底部弹出时，距离底部的位置，单位像素 | number | 24 |  |
| closeIcon | 自定义关闭图标 | ReactNode | - |  |
| duration | 默认自动关闭延时，单位秒 | number | 4.5 |  |
| getContainer | 配置渲染节点的输出位置 | () => HTMLNode | () => document.body |  |
| placement | 弹出位置，可选 `top` `topLeft` `topRight` `bottom` `bottomLeft` `bottomRight` | string | `topRight` |  |
| rtl | 是否开启 RTL 模式 | boolean | false |  |
| top | 消息从顶部弹出时，距离顶部的位置，单位像素 | number | 24 |  |
| maxCount | 最大显示数, 超过限制时，最早的消息会被自动关闭 | number | - | 4.17.0 |

### FAQ

#### 为什么 notification 不能获取 context、redux 的内容和 ConfigProvider 的 `locale/prefixCls` 配置？

直接调用 notification 方法，antd 会通过 `ReactDOM.render` 动态创建新的 React 实体。其 context 与当前代码所在 context 并不相同，因而无法获取 context 信息。

当你需要 context 信息（例如 ConfigProvider 配置的内容）时，可以通过 `notification.useNotification` 方法会返回 `api` 实体以及 `contextHolder` 节点。将其插入到你需要获取 context 位置即可：

```tsx
const [api, contextHolder] = notification.useNotification();

return (
  <Context1.Provider value="Ant">
    {/* contextHolder 在 Context1 内，它可以获得 Context1 的 context */}
    {contextHolder}
    <Context2.Provider value="Design">
      {/* contextHolder 在 Context2 外，因而不会获得 Context2 的 context */}
    </Context2.Provider>
  </Context1.Provider>
);
```

**异同：**通过 hooks 创建的 `contextHolder` 必须插入到子元素节点中才会生效，当你不需要上下文信息时请直接调用。

#### 静态方法如何设置 prefixCls ？

你可以通过 `ConfigProvider.config`-4.13.0+>) 进行设置。

## 示例代码

### 基本

- demo: `basic`

最简单的用法，4.5 秒后自动关闭。

```tsx
import { Button, notification } from 'antd';
import React from 'react';

const openNotification = () => {
  notification.open({
    message: 'Notification Title',
    description:
      'This is the content of the notification. This is the content of the notification. This is the content of the notification.',
    onClick: () => {
      console.log('Notification Clicked!');
    },
  });
};
const App: React.FC = () => (
  <Button type="primary" onClick={openNotification}>
    Open the notification box
  </Button>
);

export default App;
```

### 自动关闭的延时

- demo: `duration`

自定义通知框自动关闭的延时，默认 `4.5s`，取消自动关闭只要将该值设为 `0` 即可。

```tsx
import { Button, notification } from 'antd';
import React from 'react';

const openNotification = () => {
  const args = {
    message: 'Notification Title',
    description:
      'I will never close automatically. This is a purposely very very long description that has many many characters and words.',
    duration: 0,
  };
  notification.open(args);
};

const App: React.FC = () => (
  <Button type="primary" onClick={openNotification}>
    Open the notification box
  </Button>
);

export default App;
```

### 带有图标的通知提醒框

- demo: `with-icon`

通知提醒框左侧有图标。

```tsx
import { Button, notification, Space } from 'antd';
import React from 'react';

type NotificationType = 'success' | 'info' | 'warning' | 'error';

const openNotificationWithIcon = (type: NotificationType) => {
  notification[type]({
    message: 'Notification Title',
    description:
      'This is the content of the notification. This is the content of the notification. This is the content of the notification.',
  });
};

const App: React.FC = () => (
  <Space>
    <Button onClick={() => openNotificationWithIcon('success')}>Success</Button>
    <Button onClick={() => openNotificationWithIcon('info')}>Info</Button>
    <Button onClick={() => openNotificationWithIcon('warning')}>Warning</Button>
    <Button onClick={() => openNotificationWithIcon('error')}>Error</Button>
  </Space>
);

export default App;
```

### 自定义按钮

- demo: `with-btn`

自定义关闭按钮的样式和文字。

```tsx
import { Button, notification } from 'antd';
import React from 'react';

const close = () => {
  console.log(
    'Notification was closed. Either the close button was clicked or duration time elapsed.',
  );
};

const openNotification = () => {
  const key = `open${Date.now()}`;
  const btn = (
    <Button type="primary" size="small" onClick={() => notification.close(key)}>
      Confirm
    </Button>
  );
  notification.open({
    message: 'Notification Title',
    description:
      'A function will be be called after the notification is closed (automatically after the "duration" time of manually).',
    btn,
    key,
    onClose: close,
  });
};

const App: React.FC = () => (
  <Button type="primary" onClick={openNotification}>
    Open the notification box
  </Button>
);

export default App;
```

### 自定义图标

- demo: `custom-icon`

图标可以被自定义。

```tsx
import { SmileOutlined } from '@ant-design/icons';
import { Button, notification } from 'antd';
import React from 'react';

const openNotification = () => {
  notification.open({
    message: 'Notification Title',
    description:
      'This is the content of the notification. This is the content of the notification. This is the content of the notification.',
    icon: <SmileOutlined style={{ color: '#108ee9' }} />,
  });
};

const App: React.FC = () => (
  <Button type="primary" onClick={openNotification}>
    Open the notification box
  </Button>
);

export default App;
```

### 位置

- demo: `placement`

使用 `placement` 可以配置通知从右上角、右下角、左下角、左上角弹出。

```tsx
import {
  BorderBottomOutlined,
  BorderTopOutlined,
  RadiusBottomleftOutlined,
  RadiusBottomrightOutlined,
  RadiusUpleftOutlined,
  RadiusUprightOutlined,
} from '@ant-design/icons';
import { Button, Divider, notification, Space } from 'antd';
import type { NotificationPlacement } from 'antd/es/notification';
import React from 'react';

const openNotification = (placement: NotificationPlacement) => {
  notification.info({
    message: `Notification ${placement}`,
    description:
      'This is the content of the notification. This is the content of the notification. This is the content of the notification.',
    placement,
  });
};

const App: React.FC = () => (
  <>
    <Space>
      <Button type="primary" onClick={() => openNotification('top')} icon={<BorderTopOutlined />}>
        top
      </Button>
      <Button
        type="primary"
        onClick={() => openNotification('bottom')}
        icon={<BorderBottomOutlined />}
      >
        bottom
      </Button>
    </Space>
    <Divider />
    <Space>
      <Button
        type="primary"
        onClick={() => openNotification('topLeft')}
        icon={<RadiusUpleftOutlined />}
      >
        topLeft
      </Button>
      <Button
        type="primary"
        onClick={() => openNotification('topRight')}
        icon={<RadiusUprightOutlined />}
      >
        topRight
      </Button>
    </Space>
    <Divider />
    <Space>
      <Button
        type="primary"
        onClick={() => openNotification('bottomLeft')}
        icon={<RadiusBottomleftOutlined />}
      >
        bottomLeft
      </Button>
      <Button
        type="primary"
        onClick={() => openNotification('bottomRight')}
        icon={<RadiusBottomrightOutlined />}
      >
        bottomRight
      </Button>
    </Space>
  </>
);

export default App;
```

### 自定义样式

- demo: `custom-style`

使用 style 和 className 来定义样式。

```tsx
import { Button, notification } from 'antd';
import React from 'react';

const openNotification = () => {
  notification.open({
    message: 'Notification Title',
    description:
      'This is the content of the notification. This is the content of the notification. This is the content of the notification.',
    className: 'custom-class',
    style: {
      width: 600,
    },
  });
};

const App: React.FC = () => (
  <Button type="primary" onClick={openNotification}>
    Open the notification box
  </Button>
);

export default App;
```

### 更新消息内容

- demo: `update`

可以通过唯一的 key 来更新内容。

```tsx
import { Button, notification } from 'antd';
import React from 'react';

const key = 'updatable';

const openNotification = () => {
  notification.open({
    key,
    message: 'Notification Title',
    description: 'description.',
  });

  setTimeout(() => {
    notification.open({
      key,
      message: 'New Title',
      description: 'New description.',
    });
  }, 1000);
};

const App: React.FC = () => (
  <Button type="primary" onClick={openNotification}>
    Open the notification box
  </Button>
);

export default App;
```

### 通过 Hooks 获取上下文

- demo: `hooks`

通过 `notification.useNotification` 创建支持读取 context 的 `contextHolder`。

```tsx
import {
  RadiusBottomleftOutlined,
  RadiusBottomrightOutlined,
  RadiusUpleftOutlined,
  RadiusUprightOutlined,
} from '@ant-design/icons';
import { Button, Divider, notification, Space } from 'antd';
import type { NotificationPlacement } from 'antd/es/notification';
import React from 'react';

const Context = React.createContext({ name: 'Default' });

const App: React.FC = () => {
  const [api, contextHolder] = notification.useNotification();

  const openNotification = (placement: NotificationPlacement) => {
    api.info({
      message: `Notification ${placement}`,
      description: <Context.Consumer>{({ name }) => `Hello, ${name}!`}</Context.Consumer>,
      placement,
    });
  };

  return (
    <Context.Provider value={{ name: 'Ant Design' }}>
      {contextHolder}
      <Space>
        <Button type="primary" onClick={() => openNotification('topLeft')}>
          <RadiusUpleftOutlined />
          topLeft
        </Button>
        <Button type="primary" onClick={() => openNotification('topRight')}>
          <RadiusUprightOutlined />
          topRight
        </Button>
      </Space>
      <Divider />
      <Space>
        <Button type="primary" onClick={() => openNotification('bottomLeft')}>
          <RadiusBottomleftOutlined />
          bottomLeft
        </Button>
        <Button type="primary" onClick={() => openNotification('bottomRight')}>
          <RadiusBottomrightOutlined />
          bottomRight
        </Button>
      </Space>
    </Context.Provider>
  );
};

export default App;
```
