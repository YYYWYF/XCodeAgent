---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "segmented"
component: "Segmented"
component_code_name: "Segmented"
recommended_import: "import { Segmented } from 'antd';"
subtitle_zh: "分段控制器"
type_zh: "数据展示"
demo_count: 9
---

# Segmented 分段控制器

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Segmented`
- 推荐导入：`import { Segmented } from 'antd';`
- 组件分类：数据展示
- 简述：分段控制器。自 `antd@4.20.0` 版本开始提供该组件。

## 示例索引

- 基本（basic）
- Block 分段选择器（block）
- 不可用（disabled）
- 受控模式（controlled）
- 自定义渲染（custom）
- 动态数据（dynamic）
- 三种大小（size）
- 设置图标（with-icon）
- 只设置图标（icon-only）

## 使用文档

分段控制器。自 `antd@4.20.0` 版本开始提供该组件。

### 何时使用

- 用于展示多个选项并允许用户选择其中单个选项；
- 当切换选中选项时，关联区域的内容会发生变化。

### API

> 自 `antd@4.20.0` 版本开始提供该组件。

#### Segmented

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| block | 将宽度调整为父元素宽度的选项 | boolean | false |  |
| defaultValue | 默认选中的值 | string \| number |  |  |
| disabled | 是否禁用 | boolean | false |  |
| onChange | 选项变化时的回调函数 | function(value: string \| number) |  |  |
| options | 数据化配置选项内容 | string\[] \| number\[] \| Array | [] |  |
| size | 控件尺寸 | `large` \| `middle` \| `small` | - |  |
| value | 当前选中的值 | string \| number |  |  |

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```jsx
import { Segmented } from 'antd';

const Demo = () => <Segmented options={['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Yearly']} />;

export default Demo;
```

```css
.code-box-demo {
  overflow-x: auto;
}

.code-box-demo .ant-segmented {
  margin-bottom: 10px;
}
```

### Block 分段选择器

- demo: `block`

`block` 属性使其适合父元素宽度。

```jsx
import { Segmented } from 'antd';

const Demo = () => <Segmented block options={[123, 456, 'longtext-longtext-longtext-longtext']} />;

export default Demo;
```

### 不可用

- demo: `disabled`

Segmented 不可用。

```jsx
import { Segmented } from 'antd';

const Demo = () => (
  <>
    <Segmented options={['Map', 'Transit', 'Satellite']} disabled />
    <br />
    <Segmented
      options={[
        'Daily',
        { label: 'Weekly', value: 'Weekly', disabled: true },
        'Monthly',
        { label: 'Quarterly', value: 'Quarterly', disabled: true },
        'Yearly',
      ]}
    />
  </>
);

export default Demo;
```

### 受控模式

- demo: `controlled`

受控的 Segmented。

```tsx
import React, { useState } from 'react';
import { Segmented } from 'antd';

const Demo: React.FC = () => {
  const [value, setValue] = useState<string | number>('Map');

  return <Segmented options={['Map', 'Transit', 'Satellite']} value={value} onChange={setValue} />;
};

export default Demo;
```

### 自定义渲染

- demo: `custom`

使用 ReactNode 自定义渲染每一个 Segmented Item。

```jsx
import { Avatar, Segmented } from 'antd';
import { UserOutlined } from '@ant-design/icons';

const Demo = () => (
  <>
    <Segmented
      options={[
        {
          label: (
            <div style={{ padding: 4 }}>
              <Avatar src="https://joeschmoe.io/api/v1/random" />
              <div>User 1</div>
            </div>
          ),
          value: 'user1',
        },
        {
          label: (
            <div style={{ padding: 4 }}>
              <Avatar style={{ backgroundColor: '#f56a00' }}>K</Avatar>
              <div>User 2</div>
            </div>
          ),
          value: 'user2',
        },
        {
          label: (
            <div style={{ padding: 4 }}>
              <Avatar style={{ backgroundColor: '#87d068' }} icon={<UserOutlined />} />
              <div>User 3</div>
            </div>
          ),
          value: 'user3',
        },
      ]}
    />
    <br />
    <Segmented
      options={[
        {
          label: (
            <div style={{ padding: 4 }}>
              <div>Spring</div>
              <div>Jan-Mar</div>
            </div>
          ),
          value: 'spring',
        },
        {
          label: (
            <div style={{ padding: 4 }}>
              <div>Summer</div>
              <div>Apr-Jun</div>
            </div>
          ),
          value: 'summer',
        },
        {
          label: (
            <div style={{ padding: 4 }}>
              <div>Autumn</div>
              <div>Jul-Sept</div>
            </div>
          ),
          value: 'autumn',
        },
        {
          label: (
            <div style={{ padding: 4 }}>
              <div>Winter</div>
              <div>Oct-Dec</div>
            </div>
          ),
          value: 'winter',
        },
      ]}
    />
  </>
);

export default Demo;
```

### 动态数据

- demo: `dynamic`

动态加载数据。

```tsx
import React, { useState } from 'react';
import { Segmented, Button } from 'antd';

const defaultOptions = ['Daily', 'Weekly', 'Monthly'];

const Demo: React.FC = () => {
  const [options, setOptions] = useState(defaultOptions);
  const [moreLoaded, setMoreLoaded] = useState(false);

  const handleLoadOptions = () => {
    setOptions([...defaultOptions, 'Quarterly', 'Yearly']);
    setMoreLoaded(true);
  };

  return (
    <>
      <Segmented options={options} />
      <br />
      <Button type="primary" disabled={moreLoaded} onClick={handleLoadOptions}>
        Load more options
      </Button>
    </>
  );
};

export default Demo;
```

### 三种大小

- demo: `size`

我们为 `` 组件定义了三种尺寸（大、默认、小），高度分别为 `40px`、`32px` 和 `24px`。

```jsx
import { Segmented } from 'antd';

const Demo = () => (
  <>
    <Segmented size="large" options={['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Yearly']} />
    <br />
    <Segmented options={['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Yearly']} />
    <br />
    <Segmented size="small" options={['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Yearly']} />
  </>
);

export default Demo;
```

### 设置图标

- demo: `with-icon`

给 Segmented Item 设置 Icon。

```jsx
import { Segmented } from 'antd';
import { AppstoreOutlined, BarsOutlined } from '@ant-design/icons';

const Demo = () => (
  <Segmented
    options={[
      {
        label: 'List',
        value: 'List',
        icon: <BarsOutlined />,
      },
      {
        label: 'Kanban',
        value: 'Kanban',
        icon: <AppstoreOutlined />,
      },
    ]}
  />
);

export default Demo;
```

### 只设置图标

- demo: `icon-only`

在 Segmented Item 选项中只设置 Icon。

```jsx
import { Segmented } from 'antd';
import { AppstoreOutlined, BarsOutlined } from '@ant-design/icons';

const Demo = () => (
  <Segmented
    options={[
      {
        value: 'List',
        icon: <BarsOutlined />,
      },
      {
        value: 'Kanban',
        icon: <AppstoreOutlined />,
      },
    ]}
  />
);

export default Demo;
```
