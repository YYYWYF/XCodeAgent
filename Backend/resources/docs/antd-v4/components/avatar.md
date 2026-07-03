---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "avatar"
component: "Avatar"
component_code_name: "Avatar"
recommended_import: "import { Avatar } from 'antd';"
subtitle_zh: "头像"
type_zh: "数据展示"
demo_count: 6
---

# Avatar 头像

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Avatar`
- 推荐导入：`import { Avatar } from 'antd';`
- 组件分类：数据展示
- 简述：用来代表用户或事物，支持图片、图标或字符展示。

## 示例索引

- 基本（basic）
- 类型（type）
- 自动调整字符大小（dynamic）
- 带徽标的头像（badge）
- Avatar.Group（group）
- 响应式尺寸（responsive）

## 使用文档

用来代表用户或事物，支持图片、图标或字符展示。

### 设计师专属

安装 Kitchen Sketch 插件 💎，一键填充高逼格头像和文本。

### API

#### Avatar

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| alt | 图像无法显示时的替代文本 | string | - |  |
| gap | 字符类型距离左右两侧边界单位像素 | number | 4 | 4.3.0 |
| icon | 设置头像的自定义图标 | ReactNode | - |  |
| shape | 指定头像的形状 | `circle` \| `square` | `circle` |  |
| size | 设置头像的大小 | number \| `large` \| `small` \| `default` \| { xs: number, sm: number, ...} | `default` | 4.7.0 |
| src | 图片类头像的资源地址或者图片元素 | string \| ReactNode | - | ReactNode: 4.8.0 |
| srcSet | 设置图片类头像响应式资源地址 | string | - |  |
| draggable | 图片是否允许拖动 | boolean \| `'true'` \| `'false'` | - |  |
| crossOrigin | CORS 属性设置 | `'anonymous'` \| `'use-credentials'` \| `''` | - | 4.17.0 |
| onError | 图片加载失败的事件，返回 false 会关闭组件默认的 fallback 行为 | () => boolean | - |  |

> Tip：你可以设置 `icon` 或 `children` 作为图片加载失败的默认 fallback 行为，优先级为 `icon` > `children`

#### Avatar.Group (4.5.0+)

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| maxCount | 显示的最大头像个数 | number | - |  |
| maxPopoverPlacement | 多余头像气泡弹出位置 | `top` \| `bottom` | `top` |  |
| maxPopoverTrigger | 设置多余头像 Popover 的触发方式 | `hover` \| `focus` \| `click` | `hover` | 4.17.0 |
| maxStyle | 多余头像样式 | CSSProperties | - |  |
| size | 设置头像的大小 | number \| `large` \| `small` \| `default` \| { xs: number, sm: number, ...} | `default` | 4.8.0 |

## 示例代码

### 基本

- demo: `basic`

头像有三种尺寸，两种形状可选。

```tsx
import { UserOutlined } from '@ant-design/icons';
import { Avatar } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <div>
      <Avatar size={64} icon={<UserOutlined />} />
      <Avatar size="large" icon={<UserOutlined />} />
      <Avatar icon={<UserOutlined />} />
      <Avatar size="small" icon={<UserOutlined />} />
    </div>
    <div>
      <Avatar shape="square" size={64} icon={<UserOutlined />} />
      <Avatar shape="square" size="large" icon={<UserOutlined />} />
      <Avatar shape="square" icon={<UserOutlined />} />
      <Avatar shape="square" size="small" icon={<UserOutlined />} />
    </div>
  </>
);

export default App;
```

### 类型

- demo: `type`

支持三种类型：图片、Icon 以及字符，其中 Icon 和字符型可以自定义图标颜色及背景色。

```tsx
import { UserOutlined } from '@ant-design/icons';
import { Avatar, Image } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Avatar icon={<UserOutlined />} />
    <Avatar>U</Avatar>
    <Avatar size={40}>USER</Avatar>
    <Avatar src="https://joeschmoe.io/api/v1/random" />
    <Avatar src={<Image src="https://joeschmoe.io/api/v1/random" style={{ width: 32 }} />} />
    <Avatar style={{ color: '#f56a00', backgroundColor: '#fde3cf' }}>U</Avatar>
    <Avatar style={{ backgroundColor: '#87d068' }} icon={<UserOutlined />} />
  </>
);

export default App;
```

### 自动调整字符大小

- demo: `dynamic`

对于字符型的头像，当字符串较长时，字体大小可以根据头像宽度自动调整。也可使用 `gap` 来设置字符距离左右两侧边界单位像素。

```tsx
import { Avatar, Button } from 'antd';
import React, { useState } from 'react';

const UserList = ['U', 'Lucy', 'Tom', 'Edward'];
const ColorList = ['#f56a00', '#7265e6', '#ffbf00', '#00a2ae'];
const GapList = [4, 3, 2, 1];

const App: React.FC = () => {
  const [user, setUser] = useState(UserList[0]);
  const [color, setColor] = useState(ColorList[0]);
  const [gap, setGap] = useState(GapList[0]);

  const changeUser = () => {
    const index = UserList.indexOf(user);
    setUser(index < UserList.length - 1 ? UserList[index + 1] : UserList[0]);
    setColor(index < ColorList.length - 1 ? ColorList[index + 1] : ColorList[0]);
  };

  const changeGap = () => {
    const index = GapList.indexOf(gap);
    setGap(index < GapList.length - 1 ? GapList[index + 1] : GapList[0]);
  };

  return (
    <>
      <Avatar style={{ backgroundColor: color, verticalAlign: 'middle' }} size="large" gap={gap}>
        {user}
      </Avatar>
      <Button
        size="small"
        style={{ margin: '0 16px', verticalAlign: 'middle' }}
        onClick={changeUser}
      >
        ChangeUser
      </Button>
      <Button size="small" style={{ verticalAlign: 'middle' }} onClick={changeGap}>
        changeGap
      </Button>
    </>
  );
};

export default App;
```

### 带徽标的头像

- demo: `badge`

通常用于消息提示。

```tsx
import { UserOutlined } from '@ant-design/icons';
import { Avatar, Badge } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <span className="avatar-item">
      <Badge count={1}>
        <Avatar shape="square" icon={<UserOutlined />} />
      </Badge>
    </span>
    <span>
      <Badge dot>
        <Avatar shape="square" icon={<UserOutlined />} />
      </Badge>
    </span>
  </>
);

export default App;
```

```css
/* tile uploaded pictures */
.avatar-item {
  margin-right: 24px;
}

[class*='-col-rtl'] .avatar-item {
  margin-right: 0;
  margin-left: 24px;
}
```

### Avatar.Group

- demo: `group`

头像组合展现。

```tsx
import { AntDesignOutlined, UserOutlined } from '@ant-design/icons';
import { Avatar, Divider, Tooltip } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Avatar.Group>
      <Avatar src="https://joeschmoe.io/api/v1/random" />
      <Avatar style={{ backgroundColor: '#f56a00' }}>K</Avatar>
      <Tooltip title="Ant User" placement="top">
        <Avatar style={{ backgroundColor: '#87d068' }} icon={<UserOutlined />} />
      </Tooltip>
      <Avatar style={{ backgroundColor: '#1890ff' }} icon={<AntDesignOutlined />} />
    </Avatar.Group>
    <Divider />
    <Avatar.Group maxCount={2} maxStyle={{ color: '#f56a00', backgroundColor: '#fde3cf' }}>
      <Avatar src="https://joeschmoe.io/api/v1/random" />
      <Avatar style={{ backgroundColor: '#f56a00' }}>K</Avatar>
      <Tooltip title="Ant User" placement="top">
        <Avatar style={{ backgroundColor: '#87d068' }} icon={<UserOutlined />} />
      </Tooltip>
      <Avatar style={{ backgroundColor: '#1890ff' }} icon={<AntDesignOutlined />} />
    </Avatar.Group>
    <Divider />
    <Avatar.Group
      maxCount={2}
      size="large"
      maxStyle={{ color: '#f56a00', backgroundColor: '#fde3cf' }}
    >
      <Avatar src="https://joeschmoe.io/api/v1/random" />
      <Avatar style={{ backgroundColor: '#f56a00' }}>K</Avatar>
      <Tooltip title="Ant User" placement="top">
        <Avatar style={{ backgroundColor: '#87d068' }} icon={<UserOutlined />} />
      </Tooltip>
      <Avatar style={{ backgroundColor: '#1890ff' }} icon={<AntDesignOutlined />} />
    </Avatar.Group>
    <Divider />
    <Avatar.Group
      maxCount={2}
      maxPopoverTrigger="click"
      size="large"
      maxStyle={{ color: '#f56a00', backgroundColor: '#fde3cf', cursor: 'pointer' }}
    >
      <Avatar src="https://zos.alipayobjects.com/rmsportal/ODTLcjxAfvqbxHnVXCYX.png" />
      <Avatar style={{ backgroundColor: '#f56a00' }}>K</Avatar>
      <Tooltip title="Ant User" placement="top">
        <Avatar style={{ backgroundColor: '#87d068' }} icon={<UserOutlined />} />
      </Tooltip>
      <Avatar style={{ backgroundColor: '#1890ff' }} icon={<AntDesignOutlined />} />
    </Avatar.Group>
  </>
);

export default App;
```

### 响应式尺寸

- demo: `responsive`

头像大小可以根据屏幕大小自动调整。

```tsx
import { AntDesignOutlined } from '@ant-design/icons';
import { Avatar } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Avatar
    size={{ xs: 24, sm: 32, md: 40, lg: 64, xl: 80, xxl: 100 }}
    icon={<AntDesignOutlined />}
  />
);

export default App;
```
