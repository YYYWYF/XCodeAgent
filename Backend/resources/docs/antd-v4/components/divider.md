---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "divider"
component: "Divider"
component_code_name: "Divider"
recommended_import: "import { Divider } from 'antd';"
subtitle_zh: "分割线"
type_zh: "布局"
demo_count: 4
---

# Divider 分割线

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Divider`
- 推荐导入：`import { Divider } from 'antd';`
- 组件分类：布局
- 简述：区隔内容的分割线。

## 示例索引

- 水平分割线（horizontal）
- 带文字的分割线（with-text）
- 分割文字使用正文样式（plain）
- 垂直分割线（vertical）

## 使用文档

区隔内容的分割线。

### 何时使用

- 对不同章节的文本段落进行分割。
- 对行内文字/链接进行分割，例如表格的操作列。

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| children | 嵌套的标题 | ReactNode | - |  |
| className | 分割线样式类 | string | - |  |
| dashed | 是否虚线 | boolean | false |  |
| orientation | 分割线标题的位置 | `left` \| `right` \| `center` | `center` |  |
| orientationMargin | 标题和最近 left/right 边框之间的距离，去除了分割线，同时 `orientation` 必须为 `left` 或 `right` | string \| number | - |  |
| plain | 文字是否显示为普通正文样式 | boolean | false | 4.2.0 |
| style | 分割线样式对象 | CSSProperties | - |  |
| type | 水平还是垂直类型 | `horizontal` \| `vertical` | `horizontal` |  |

## 示例代码

### 水平分割线

- demo: `horizontal`

默认为水平分割线，可在中间加入文字。

```tsx
import { Divider } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider />
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider dashed />
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
  </>
);

export default App;
```

### 带文字的分割线

- demo: `with-text`

分割线中带有文字，可以用 `orientation` 指定文字位置。

```tsx
import { Divider } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider>Text</Divider>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider orientation="left">Left Text</Divider>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider orientation="right">Right Text</Divider>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider orientation="left" orientationMargin="0">
      Left Text with 0 orientationMargin
    </Divider>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider orientation="right" orientationMargin={50}>
      Right Text with 50px orientationMargin
    </Divider>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
  </>
);

export default App;
```

### 分割文字使用正文样式

- demo: `plain`

使用 `plain` 可以设置为更轻量的分割文字样式。

```tsx
import { Divider } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider plain>Text</Divider>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider orientation="left" plain>
      Left Text
    </Divider>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
    <Divider orientation="right" plain>
      Right Text
    </Divider>
    <p>
      Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed nonne merninisti licere mihi ista
      probare, quae sunt a te dicta? Refert tamen, quo modo.
    </p>
  </>
);

export default App;
```

### 垂直分割线

- demo: `vertical`

使用 `type="vertical"` 设置为行内的垂直分割线。

```tsx
import { Divider } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    Text
    <Divider type="vertical" />
    <a href="#">Link</a>
    <Divider type="vertical" />
    <a href="#">Link</a>
  </>
);

export default App;
```
