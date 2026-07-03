---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "spin"
component: "Spin"
component_code_name: "Spin"
recommended_import: "import { Spin } from 'antd';"
subtitle_zh: "加载中"
type_zh: "反馈"
demo_count: 7
---

# Spin 加载中

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Spin`
- 推荐导入：`import { Spin } from 'antd';`
- 组件分类：反馈
- 简述：用于页面和区块的加载中状态。

## 示例索引

- 基本用法（basic）
- 各种大小（size）
- 容器（inside）
- 卡片加载中（nested）
- 自定义描述文案（tip）
- 延迟（delayAndDebounce）
- 自定义指示符（custom-indicator）

## 使用文档

用于页面和区块的加载中状态。

### 何时使用

页面局部处于等待异步数据或正在渲染过程时，合适的加载动效会有效缓解用户的焦虑。

### API

| 参数 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| delay | 延迟显示加载效果的时间（防止闪烁） | number (毫秒) | - |
| indicator | 加载指示符 | ReactNode | - |
| size | 组件大小，可选值为 `small` `default` `large` | string | `default` |
| spinning | 是否为加载中状态 | boolean | true |
| tip | 当作为包裹元素时，可以自定义描述文案 | ReactNode | - |
| wrapperClassName | 包装器的类属性 | string | - |

#### 静态方法

- `Spin.setDefaultIndicator(indicator: ReactNode)`

  你可以自定义全局默认 Spin 的元素。

## 示例代码

### 基本用法

- demo: `basic`

一个简单的 loading 状态。

```tsx
import { Spin } from 'antd';
import React from 'react';

const App: React.FC = () => <Spin />;

export default App;
```

### 各种大小

- demo: `size`

小的用于文本加载，默认用于卡片容器级加载，大的用于**页面级**加载。

```tsx
import { Space, Spin } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Space size="middle">
    <Spin size="small" />
    <Spin />
    <Spin size="large" />
  </Space>
);

export default App;
```

### 容器

- demo: `inside`

放入一个容器中。

```tsx
import { Spin } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <div className="example">
    <Spin />
  </div>
);

export default App;
```

```css
.example {
  margin: 20px 0;
  margin-bottom: 20px;
  padding: 30px 50px;
  text-align: center;
  background: rgba(0, 0, 0, 0.05);
  border-radius: 4px;
}
```

### 卡片加载中

- demo: `nested`

可以直接把内容内嵌到 `Spin` 中，将现有容器变为加载状态。

```tsx
import { Alert, Spin, Switch } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [loading, setLoading] = useState(false);

  const toggle = (checked: boolean) => {
    setLoading(checked);
  };

  return (
    <div>
      <Spin spinning={loading}>
        <Alert
          message="Alert message title"
          description="Further details about the context of this alert."
          type="info"
        />
      </Spin>
      <div style={{ marginTop: 16 }}>
        Loading state：
        <Switch checked={loading} onChange={toggle} />
      </div>
    </div>
  );
};

export default App;
```

### 自定义描述文案

- demo: `tip`

自定义描述文案。

```tsx
import { Alert, Spin } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Spin tip="Loading...">
    <Alert
      message="Alert message title"
      description="Further details about the context of this alert."
      type="info"
    />
  </Spin>
);

export default App;
```

### 延迟

- demo: `delayAndDebounce`

延迟显示 loading 效果。当 spinning 状态在 `delay` 时间内结束，则不显示 loading 状态。

```tsx
import { Alert, Spin, Switch } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [loading, setLoading] = useState(false);

  const toggle = (checked: boolean) => {
    setLoading(checked);
  };
  const container = (
    <Alert
      message="Alert message title"
      description="Further details about the context of this alert."
      type="info"
    />
  );

  return (
    <div>
      <Spin spinning={loading} delay={500}>
        {container}
      </Spin>
      <div style={{ marginTop: 16 }}>
        Loading state：
        <Switch checked={loading} onChange={toggle} />
      </div>
    </div>
  );
};

export default App;
```

### 自定义指示符

- demo: `custom-indicator`

使用自定义指示符。

```tsx
import { LoadingOutlined } from '@ant-design/icons';
import { Spin } from 'antd';
import React from 'react';

const antIcon = <LoadingOutlined style={{ fontSize: 24 }} spin />;

const App: React.FC = () => <Spin indicator={antIcon} />;

export default App;
```
