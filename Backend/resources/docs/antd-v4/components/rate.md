---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "rate"
component: "Rate"
component_code_name: "Rate"
recommended_import: "import { Rate } from 'antd';"
subtitle_zh: "评分"
type_zh: "数据录入"
demo_count: 7
---

# Rate 评分

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Rate`
- 推荐导入：`import { Rate } from 'antd';`
- 组件分类：数据录入
- 简述：评分组件。

## 示例索引

- 基本（basic）
- 半星（half）
- 文案展现（text）
- 只读（disabled）
- 清除（clear）
- 其他字符（character）
- 自定义字符（character-function）

## 使用文档

评分组件。

### 何时使用

- 对评价进行展示。
- 对事物进行快速的评级操作。

### API

| 属性 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| allowClear | 是否允许再次点击后清除 | boolean | true |  |
| allowHalf | 是否允许半选 | boolean | false |  |
| autoFocus | 自动获取焦点 | boolean | false |  |
| character | 自定义字符 | ReactNode \| (RateProps) => ReactNode | &lt;StarFilled /> | function(): 4.4.0 |
| className | 自定义样式类名 | string | - |  |
| count | star 总数 | number | 5 |  |
| defaultValue | 默认值 | number | 0 |  |
| disabled | 只读，无法进行交互 | boolean | false |  |
| style | 自定义样式对象 | CSSProperties | - |  |
| tooltips | 自定义每项的提示信息 | string\[] | - |  |
| value | 当前数，受控值 | number | - |  |
| onBlur | 失去焦点时的回调 | function() | - |  |
| onChange | 选择时的回调 | function(value: number) | - |  |
| onFocus | 获取焦点时的回调 | function() | - |  |
| onHoverChange | 鼠标经过时数值变化的回调 | function(value: number) | - |  |
| onKeyDown | 按键回调 | function(event) | - |  |

### 方法

| 名称 | 描述 |
| --- | --- |
| blur() | 移除焦点 |
| focus() | 获取焦点 |

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```tsx
import { Rate } from 'antd';
import React from 'react';

const App: React.FC = () => <Rate />;

export default App;
```

### 半星

- demo: `half`

支持选中半星。

```tsx
import { Rate } from 'antd';
import React from 'react';

const App: React.FC = () => <Rate allowHalf defaultValue={2.5} />;

export default App;
```

### 文案展现

- demo: `text`

给评分组件加上文案展示。

```tsx
import { Rate } from 'antd';
import React, { useState } from 'react';

const desc = ['terrible', 'bad', 'normal', 'good', 'wonderful'];

const App: React.FC = () => {
  const [value, setValue] = useState(3);

  return (
    <span>
      <Rate tooltips={desc} onChange={setValue} value={value} />
      {value ? <span className="ant-rate-text">{desc[value - 1]}</span> : ''}
    </span>
  );
};

export default App;
```

### 只读

- demo: `disabled`

只读，无法进行鼠标交互。

```tsx
import { Rate } from 'antd';
import React from 'react';

const App: React.FC = () => <Rate disabled defaultValue={2} />;

export default App;
```

### 清除

- demo: `clear`

支持允许或者禁用清除。

```tsx
import { Rate } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Rate defaultValue={3} />
    <span className="ant-rate-text">allowClear: true</span>
    <br />
    <Rate allowClear={false} defaultValue={3} />
    <span className="ant-rate-text">allowClear: false</span>
  </>
);

export default App;
```

### 其他字符

- demo: `character`

可以将星星替换为其他字符，比如字母，数字，字体图标甚至中文。

```tsx
import { HeartOutlined } from '@ant-design/icons';
import { Rate } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Rate character={<HeartOutlined />} allowHalf />
    <br />
    <Rate character="A" allowHalf style={{ fontSize: 36 }} />
    <br />
    <Rate character="好" allowHalf />
  </>
);

export default App;
```

### 自定义字符

- demo: `character-function`

可以使用 `(RateProps) => ReactNode` 的方式自定义每一个字符。

```tsx
import { FrownOutlined, MehOutlined, SmileOutlined } from '@ant-design/icons';
import { Rate } from 'antd';
import React from 'react';

const customIcons: Record<number, React.ReactNode> = {
  1: <FrownOutlined />,
  2: <FrownOutlined />,
  3: <MehOutlined />,
  4: <SmileOutlined />,
  5: <SmileOutlined />,
};

const App: React.FC = () => (
  <>
    <Rate defaultValue={2} character={({ index }: { index: number }) => index + 1} />
    <br />
    <Rate defaultValue={3} character={({ index }: { index: number }) => customIcons[index + 1]} />
  </>
);

export default App;
```
