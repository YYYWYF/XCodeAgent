---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "affix"
component: "Affix"
component_code_name: "Affix"
recommended_import: "import { Affix } from 'antd';"
subtitle_zh: "固钉"
type_zh: "导航"
demo_count: 3
---

# Affix 固钉

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Affix`
- 推荐导入：`import { Affix } from 'antd';`
- 组件分类：导航
- 简述：将页面元素钉在可视范围。

## 示例索引

- 基本（basic）
- 固定状态改变的回调（on-change）
- 滚动容器（target）

## 使用文档

将页面元素钉在可视范围。

### 何时使用

当内容区域比较长，需要滚动页面时，这部分内容对应的操作或者导航需要在滚动范围内始终展现。常用于侧边菜单和按钮组合。

页面可视范围过小时，慎用此功能以免遮挡页面内容。

### API

| 成员 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| offsetBottom | 距离窗口底部达到指定偏移量后触发 | number | - |
| offsetTop | 距离窗口顶部达到指定偏移量后触发 | number | 0 |
| target | 设置 `Affix` 需要监听其滚动事件的元素，值为一个返回对应 DOM 元素的函数 | () => HTMLElement | () => window |
| onChange | 固定状态改变时触发的回调函数 | (affixed?: boolean) => void | - |

**注意：**`Affix` 内的元素不要使用绝对定位，如需要绝对定位的效果，可以直接设置 `Affix` 为绝对定位：

```jsx
<Affix style={{ position: 'absolute', top: y, left: x }}>...</Affix>
```

### FAQ

#### Affix 使用 `target` 绑定容器时，元素会跑到容器外。

从性能角度考虑，我们只监听容器滚动事件。如果希望任意滚动，你可以在窗体添加滚动监听：

相关 issue：#3938 #5642 #16120

#### Affix 在水平滚动容器中使用时， 元素 `left` 位置不正确。

Affix 一般只适用于单向滚动的区域，只支持在垂直滚动容器中使用。如果希望在水平容器中使用，你可以考虑使用 原生 `position: sticky` 实现。

相关 issue: #29108

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```tsx
import { Affix, Button } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [top, setTop] = useState(10);
  const [bottom, setBottom] = useState(10);

  return (
    <>
      <Affix offsetTop={top}>
        <Button type="primary" onClick={() => setTop(top + 10)}>
          Affix top
        </Button>
      </Affix>
      <br />
      <Affix offsetBottom={bottom}>
        <Button type="primary" onClick={() => setBottom(bottom + 10)}>
          Affix bottom
        </Button>
      </Affix>
    </>
  );
};

export default App;
```

### 固定状态改变的回调

- demo: `on-change`

可以获得是否固定的状态。

```tsx
import { Affix, Button } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Affix offsetTop={120} onChange={affixed => console.log(affixed)}>
    <Button>120px to affix top</Button>
  </Affix>
);

export default App;
```

### 滚动容器

- demo: `target`

用 `target` 设置 `Affix` 需要监听其滚动事件的元素，默认为 `window`。

```tsx
import { Affix, Button } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);

  return (
    <div className="scrollable-container" ref={setContainer}>
      <div className="background">
        <Affix target={() => container}>
          <Button type="primary">Fixed at the top of container</Button>
        </Affix>
      </div>
    </div>
  );
};

export default App;
```
