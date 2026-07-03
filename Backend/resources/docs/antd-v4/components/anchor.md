---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "anchor"
component: "Anchor"
component_code_name: "Anchor"
recommended_import: "import { Anchor } from 'antd';"
subtitle_zh: "锚点"
type_zh: "其他"
demo_count: 6
---

# Anchor 锚点

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Anchor`
- 推荐导入：`import { Anchor } from 'antd';`
- 组件分类：其他
- 简述：用于跳转到页面指定位置。

## 示例索引

- 基本（basic）
- 静态位置（static）
- 自定义 onClick 事件（onClick）
- 自定义锚点高亮（customizeHighlight）
- 设置锚点滚动偏移量（targetOffset）
- 监听锚点链接改变（onChange）

## 使用文档

用于跳转到页面指定位置。

### 何时使用

需要展现当前页面上可供跳转的锚点链接，以及快速在锚点之间跳转。

> 开发者注意事项：
>
> 自 `4.24.0` 起，由于组件从 class 重构成 FC，之前一些获取 `ref` 并调用内部实例方法的写法都会失效

### API

#### Anchor Props

| 成员 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| affix | 固定模式 | boolean | true |  |
| bounds | 锚点区域边界 | number | 5 |  |
| getContainer | 指定滚动的容器 | () => HTMLElement | () => window |  |
| getCurrentAnchor | 自定义高亮的锚点 | (activeLink: string) => string | - |  |
| offsetTop | 距离窗口顶部达到指定偏移量后触发 | number |  |  |
| showInkInFixed | `affix={false}` 时是否显示小圆点 | boolean | false |  |
| targetOffset | 锚点滚动偏移量，默认与 offsetTop 相同，例子 | number | - |  |
| onChange | 监听锚点链接改变 | (currentActiveLink: string) => void | - |  |
| onClick | `click` 事件的 handler | function(e: Event, link: Object) | - |  |

#### Link Props

| 成员   | 说明                             | 类型      | 默认值 | 版本 |
| ------ | -------------------------------- | --------- | ------ | ---- |
| href   | 锚点链接                         | string    | -      |      |
| target | 该属性指定在何处显示链接的资源。 | string    | -      |      |
| title  | 文字内容                         | ReactNode | -      |      |

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```tsx
import { Anchor } from 'antd';
import React from 'react';

const { Link } = Anchor;

const App: React.FC = () => (
  <Anchor>
    <Link href="#components-anchor-demo-basic" title="Basic demo" />
    <Link href="#components-anchor-demo-static" title="Static demo" />
    <Link href="#API" title="API">
      <Link href="#Anchor-Props" title="Anchor Props" />
      <Link href="#Link-Props" title="Link Props" />
    </Link>
  </Anchor>
);

export default App;
```

### 静态位置

- demo: `static`

不浮动，状态不随页面滚动变化。

```tsx
import { Anchor } from 'antd';
import React from 'react';

const { Link } = Anchor;

const App: React.FC = () => (
  <Anchor affix={false}>
    <Link href="#components-anchor-demo-basic" title="Basic demo" />
    <Link href="#components-anchor-demo-static" title="Static demo" />
    <Link href="#API" title="API">
      <Link href="#Anchor-Props" title="Anchor Props" />
      <Link href="#Link-Props" title="Link Props" />
    </Link>
  </Anchor>
);

export default App;
```

### 自定义 onClick 事件

- demo: `onClick`

点击锚点不记录历史。

```tsx
import { Anchor } from 'antd';
import React from 'react';

const { Link } = Anchor;

const handleClick = (
  e: React.MouseEvent<HTMLElement>,
  link: {
    title: React.ReactNode;
    href: string;
  },
) => {
  e.preventDefault();
  console.log(link);
};

const App: React.FC = () => (
  <Anchor affix={false} onClick={handleClick}>
    <Link href="#components-anchor-demo-basic" title="Basic demo" />
    <Link href="#components-anchor-demo-static" title="Static demo" />
    <Link href="#API" title="API">
      <Link href="#Anchor-Props" title="Anchor Props" />
      <Link href="#Link-Props" title="Link Props" />
    </Link>
  </Anchor>
);

export default App;
```

### 自定义锚点高亮

- demo: `customizeHighlight`

自定义锚点高亮。

```tsx
import { Anchor } from 'antd';
import React from 'react';

const { Link } = Anchor;

const getCurrentAnchor = () => '#components-anchor-demo-static';

const App: React.FC = () => (
  <Anchor affix={false} getCurrentAnchor={getCurrentAnchor}>
    <Link href="#components-anchor-demo-basic" title="Basic demo" />
    <Link href="#components-anchor-demo-static" title="Static demo" />
    <Link href="#API" title="API">
      <Link href="#Anchor-Props" title="Anchor Props" />
      <Link href="#Link-Props" title="Link Props" />
    </Link>
  </Anchor>
);

export default App;
```

### 设置锚点滚动偏移量

- demo: `targetOffset`

锚点目标滚动到屏幕正中间。

```tsx
import { Anchor } from 'antd';
import React, { useEffect, useState } from 'react';

const { Link } = Anchor;

const App: React.FC = () => {
  const [targetOffset, setTargetOffset] = useState<number | undefined>(undefined);

  useEffect(() => {
    setTargetOffset(window.innerHeight / 2);
  }, []);

  return (
    <Anchor targetOffset={targetOffset}>
      <Link href="#components-anchor-demo-basic" title="Basic demo" />
      <Link href="#components-anchor-demo-static" title="Static demo" />
      <Link href="#API" title="API">
        <Link href="#Anchor-Props" title="Anchor Props" />
        <Link href="#Link-Props" title="Link Props" />
      </Link>
    </Anchor>
  );
};

export default App;
```

### 监听锚点链接改变

- demo: `onChange`

监听锚点链接改变

```tsx
import { Anchor } from 'antd';
import React from 'react';

const { Link } = Anchor;

const onChange = (link: string) => {
  console.log('Anchor:OnChange', link);
};

const App: React.FC = () => (
  <Anchor affix={false} onChange={onChange}>
    <Link href="#components-anchor-demo-basic" title="Basic demo" />
    <Link href="#components-anchor-demo-static" title="Static demo" />
    <Link href="#API" title="API">
      <Link href="#Anchor-Props" title="Anchor Props" />
      <Link href="#Link-Props" title="Link Props" />
    </Link>
  </Anchor>
);

export default App;
```
