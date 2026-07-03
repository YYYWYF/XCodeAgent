---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "back-top"
component: "BackTop"
component_code_name: "BackTop"
recommended_import: "import { BackTop } from 'antd';"
subtitle_zh: "回到顶部"
type_zh: "其他"
demo_count: 2
---

# BackTop 回到顶部

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`BackTop`
- 推荐导入：`import { BackTop } from 'antd';`
- 组件分类：其他
- 简述：返回页面顶部的操作按钮。

## 示例索引

- 基本（basic）
- 自定义样式（custom）

## 使用文档

返回页面顶部的操作按钮。

### 何时使用

- 当页面内容区域比较长时；
- 当用户需要频繁返回顶部查看相关内容时。

### API

> 有默认样式，距离底部 `50px`，可覆盖。
>
> 自定义样式宽高不大于 40px \* 40px。

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| duration | 回到顶部所需时间（ms） | number | 450 | 4.4.0 |
| target | 设置需要监听其滚动事件的元素，值为一个返回对应 DOM 元素的函数 | () => HTMLElement | () => window |  |
| visibilityHeight | 滚动高度达到此参数值才出现 BackTop | number | 400 |  |
| onClick | 点击按钮的回调函数 | function | - |  |

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```tsx
import { BackTop } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <BackTop />
    Scroll down to see the bottom-right
    <strong className="site-back-top-basic"> gray </strong>
    button.
  </>
);

export default App;
```

```css
.site-back-top-basic {
  color: rgba(64, 64, 64, 0.6);
}
```

### 自定义样式

- demo: `custom`

可以自定义回到顶部按钮的样式，限制宽高：`40px * 40px`。

> 注意：`BackTop` 需要一个可接受 `onClick` 事件的元素作为 `children`。 如果您直接将文本作为子项放置，则该组件将无法正常运行。

```tsx
import { BackTop } from 'antd';
import React from 'react';

const style: React.CSSProperties = {
  height: 40,
  width: 40,
  lineHeight: '40px',
  borderRadius: 4,
  backgroundColor: '#1088e9',
  color: '#fff',
  textAlign: 'center',
  fontSize: 14,
};

const App: React.FC = () => (
  <div style={{ height: '600vh', padding: 8 }}>
    <div>Scroll to bottom</div>
    <div>Scroll to bottom</div>
    <div>Scroll to bottom</div>
    <div>Scroll to bottom</div>
    <div>Scroll to bottom</div>
    <div>Scroll to bottom</div>
    <div>Scroll to bottom</div>
    <BackTop>
      <div style={style}>UP</div>
    </BackTop>
  </div>
);

export default App;
```
