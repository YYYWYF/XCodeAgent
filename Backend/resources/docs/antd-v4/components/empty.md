---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "empty"
component: "Empty"
component_code_name: "Empty"
recommended_import: "import { Empty } from 'antd';"
subtitle_zh: "空状态"
type_zh: "数据展示"
demo_count: 5
---

# Empty 空状态

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Empty`
- 推荐导入：`import { Empty } from 'antd';`
- 组件分类：数据展示
- 简述：空状态时的展示占位图。

## 示例索引

- 基本（basic）
- 选择图片（simple）
- 自定义（customize）
- 全局化配置（config-provider）
- 无描述（description）

## 使用文档

空状态时的展示占位图。

### 何时使用

- 当目前没有数据时，用于显式的用户提示。
- 初始化场景时的引导创建流程。

### API

```jsx
<Empty>
  <Button>创建</Button>
</Empty>
```

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| description | 自定义描述内容 | ReactNode | - |  |
| image | 设置显示图片，为 string 时表示自定义图片地址。 | ReactNode | `Empty.PRESENTED_IMAGE_DEFAULT` |  |
| imageStyle | 图片样式 | CSSProperties | - |  |

### 内置图片

- Empty.PRESENTED_IMAGE_SIMPLE

- Empty.PRESENTED_IMAGE_DEFAULT

## 示例代码

### 基本

- demo: `basic`

简单的展示。

```tsx
import { Empty } from 'antd';

const App: React.FC = () => <Empty />;

export default App;
```

### 选择图片

- demo: `simple`

可以通过设置 `image` 为 `Empty.PRESENTED_IMAGE_SIMPLE` 选择另一种风格的图片。

```tsx
import { Empty } from 'antd';

const App: React.FC = () => <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />;

export default App;
```

### 自定义

- demo: `customize`

自定义图片链接、图片大小、描述、附属内容。

```tsx
import { Button, Empty } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Empty
    image="https://gw.alipayobjects.com/zos/antfincdn/ZHrcdLPrvN/empty.svg"
    imageStyle={{
      height: 60,
    }}
    description={
      <span>
        Customize <a href="#API">Description</a>
      </span>
    }
  >
    <Button type="primary">Create Now</Button>
  </Empty>
);

export default App;
```

### 全局化配置

- demo: `config-provider`

自定义全局组件的 Empty 样式。

```tsx
import { SmileOutlined } from '@ant-design/icons';
import {
  Cascader,
  ConfigProvider,
  Divider,
  List,
  Select,
  Switch,
  Table,
  Transfer,
  TreeSelect,
} from 'antd';
import React, { useState } from 'react';

const customizeRenderEmpty = () => (
  <div style={{ textAlign: 'center' }}>
    <SmileOutlined style={{ fontSize: 20 }} />
    <p>Data Not Found</p>
  </div>
);

const style = { width: 200 };

const App: React.FC = () => {
  const [customize, setCustomize] = useState(false);

  return (
    <div>
      <Switch
        unCheckedChildren="default"
        checkedChildren="customize"
        checked={customize}
        onChange={val => {
          setCustomize(val);
        }}
      />

      <Divider />

      <ConfigProvider renderEmpty={customize ? customizeRenderEmpty : undefined}>
        <div className="config-provider">
          <h4>Select</h4>
          <Select style={style} />

          <h4>TreeSelect</h4>
          <TreeSelect style={style} treeData={[]} />

          <h4>Cascader</h4>
          <Cascader style={style} options={[]} showSearch />

          <h4>Transfer</h4>
          <Transfer />

          <h4>Table</h4>
          <Table
            style={{ marginTop: 8 }}
            columns={[
              {
                title: 'Name',
                dataIndex: 'name',
                key: 'name',
              },
              {
                title: 'Age',
                dataIndex: 'age',
                key: 'age',
              },
            ]}
          />

          <h4>List</h4>
          <List />
        </div>
      </ConfigProvider>
    </div>
  );
};

export default App;
```

### 无描述

- demo: `description`

无描述展示。

```tsx
import { Empty } from 'antd';

const App: React.FC = () => <Empty description={false} />;

export default App;
```
