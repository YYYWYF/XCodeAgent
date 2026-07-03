---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "card"
component: "Card"
component_code_name: "Card"
recommended_import: "import { Card } from 'antd';"
subtitle_zh: "卡片"
type_zh: "数据展示"
demo_count: 10
---

# Card 卡片

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Card`
- 推荐导入：`import { Card } from 'antd';`
- 组件分类：数据展示
- 简述：通用卡片容器。

## 示例索引

- 典型卡片（basic）
- 无边框（border-less）
- 简洁卡片（simple）
- 更灵活的内容展示（flexible-content）
- 栅格卡片（in-column）
- 预加载的卡片（loading）
- 网格型内嵌卡片（grid-card）
- 内部卡片（inner）
- 带页签的卡片（tabs）
- 支持更多内容配置（meta）

## 使用文档

通用卡片容器。

### 何时使用

最基础的卡片容器，可承载文字、列表、图片、段落，常用于后台概览页面。

### API

```jsx
<Card title="卡片标题">卡片内容</Card>
```

#### Card

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| actions | 卡片操作组，位置在卡片底部 | Array&lt;ReactNode> | - |  |
| activeTabKey | 当前激活页签的 key | string | - |  |
| bodyStyle | 内容区域自定义样式 | CSSProperties | - |  |
| bordered | 是否有边框 | boolean | true |  |
| cover | 卡片封面 | ReactNode | - |  |
| defaultActiveTabKey | 初始化选中页签的 key，如果没有设置 activeTabKey | string | `第一个页签` |  |
| extra | 卡片右上角的操作区域 | ReactNode | - |  |
| headStyle | 自定义标题区域样式 | CSSProperties | - |  |
| hoverable | 鼠标移过时可浮起 | boolean | false |  |
| loading | 当卡片内容还在加载中时，可以用 loading 展示一个占位 | boolean | false |  |
| size | card 的尺寸 | `default` \| `small` | `default` |  |
| tabBarExtraContent | tab bar 上额外的元素 | ReactNode | - |  |
| tabList | 页签标题列表 | Array&lt;{key: string, tab: ReactNode}> | - |  |
| tabProps | Tabs | - | - |  |
| title | 卡片标题 | ReactNode | - |  |
| type | 卡片类型，可设置为 `inner` 或 不设置 | string | - |  |
| onTabChange | 页签切换的回调 | (key) => void | - |  |

#### Card.Grid

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| className | 网格容器类名 | string | - |  |
| hoverable | 鼠标移过时可浮起 | boolean | true |  |
| style | 定义网格容器类名的样式 | CSSProperties | - |  |

#### Card.Meta

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| avatar | 头像/图标 | ReactNode | - |  |
| className | 容器类名 | string | - |  |
| description | 描述内容 | ReactNode | - |  |
| style | 定义容器类名的样式 | CSSProperties | - |  |
| title | 标题内容 | ReactNode | - |  |

## 示例代码

### 典型卡片

- demo: `basic`

包含标题、内容、操作区域。

```tsx
import { Card } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Card title="Default size card" extra={<a href="#">More</a>} style={{ width: 300 }}>
      <p>Card content</p>
      <p>Card content</p>
      <p>Card content</p>
    </Card>
    <Card size="small" title="Small size card" extra={<a href="#">More</a>} style={{ width: 300 }}>
      <p>Card content</p>
      <p>Card content</p>
      <p>Card content</p>
    </Card>
  </>
);

export default App;
```

### 无边框

- demo: `border-less`

在灰色背景上使用无边框的卡片。

```tsx
import { Card } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <div className="site-card-border-less-wrapper">
    <Card title="Card title" bordered={false} style={{ width: 300 }}>
      <p>Card content</p>
      <p>Card content</p>
      <p>Card content</p>
    </Card>
  </div>
);

export default App;
```

```css
.site-card-border-less-wrapper {
  padding: 30px;
  background: #ececec;
}
```

### 简洁卡片

- demo: `simple`

只包含内容区域。

```tsx
import { Card } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Card style={{ width: 300 }}>
    <p>Card content</p>
    <p>Card content</p>
    <p>Card content</p>
  </Card>
);

export default App;
```

### 更灵活的内容展示

- demo: `flexible-content`

可以利用 `Card.Meta` 支持更灵活的内容。

```tsx
import { Card } from 'antd';
import React from 'react';

const { Meta } = Card;

const App: React.FC = () => (
  <Card
    hoverable
    style={{ width: 240 }}
    cover={<img alt="example" src="https://os.alipayobjects.com/rmsportal/QBnOOoLaAfKPirc.png" />}
  >
    <Meta title="Europe Street beat" description="www.instagram.com" />
  </Card>
);

export default App;
```

### 栅格卡片

- demo: `in-column`

在系统概览页面常常和栅格进行配合。

```tsx
import { Card, Col, Row } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <div className="site-card-wrapper">
    <Row gutter={16}>
      <Col span={8}>
        <Card title="Card title" bordered={false}>
          Card content
        </Card>
      </Col>
      <Col span={8}>
        <Card title="Card title" bordered={false}>
          Card content
        </Card>
      </Col>
      <Col span={8}>
        <Card title="Card title" bordered={false}>
          Card content
        </Card>
      </Col>
    </Row>
  </div>
);

export default App;
```

### 预加载的卡片

- demo: `loading`

数据读入前会有文本块样式。

```tsx
import { EditOutlined, EllipsisOutlined, SettingOutlined } from '@ant-design/icons';
import { Avatar, Card, Skeleton, Switch } from 'antd';
import React, { useState } from 'react';

const { Meta } = Card;

const App: React.FC = () => {
  const [loading, setLoading] = useState(true);

  const onChange = (checked: boolean) => {
    setLoading(!checked);
  };

  return (
    <>
      <Switch checked={!loading} onChange={onChange} />

      <Card style={{ width: 300, marginTop: 16 }} loading={loading}>
        <Meta
          avatar={<Avatar src="https://joeschmoe.io/api/v1/random" />}
          title="Card title"
          description="This is the description"
        />
      </Card>

      <Card
        style={{ width: 300, marginTop: 16 }}
        actions={[
          <SettingOutlined key="setting" />,
          <EditOutlined key="edit" />,
          <EllipsisOutlined key="ellipsis" />,
        ]}
      >
        <Skeleton loading={loading} avatar active>
          <Meta
            avatar={<Avatar src="https://joeschmoe.io/api/v1/random" />}
            title="Card title"
            description="This is the description"
          />
        </Skeleton>
      </Card>
    </>
  );
};

export default App;
```

### 网格型内嵌卡片

- demo: `grid-card`

一种常见的卡片内容区隔模式。

```tsx
import { Card } from 'antd';
import React from 'react';

const gridStyle: React.CSSProperties = {
  width: '25%',
  textAlign: 'center',
};

const App: React.FC = () => (
  <Card title="Card Title">
    <Card.Grid style={gridStyle}>Content</Card.Grid>
    <Card.Grid hoverable={false} style={gridStyle}>
      Content
    </Card.Grid>
    <Card.Grid style={gridStyle}>Content</Card.Grid>
    <Card.Grid style={gridStyle}>Content</Card.Grid>
    <Card.Grid style={gridStyle}>Content</Card.Grid>
    <Card.Grid style={gridStyle}>Content</Card.Grid>
    <Card.Grid style={gridStyle}>Content</Card.Grid>
  </Card>
);

export default App;
```

### 内部卡片

- demo: `inner`

可以放在普通卡片内部，展示多层级结构的信息。

```tsx
import { Card } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Card title="Card title">
    <Card type="inner" title="Inner Card title" extra={<a href="#">More</a>}>
      Inner Card content
    </Card>
    <Card
      style={{ marginTop: 16 }}
      type="inner"
      title="Inner Card title"
      extra={<a href="#">More</a>}
    >
      Inner Card content
    </Card>
  </Card>
);

export default App;
```

### 带页签的卡片

- demo: `tabs`

可承载更多内容。

```tsx
import { Card } from 'antd';
import React, { useState } from 'react';

const tabList = [
  {
    key: 'tab1',
    tab: 'tab1',
  },
  {
    key: 'tab2',
    tab: 'tab2',
  },
];

const contentList: Record<string, React.ReactNode> = {
  tab1: <p>content1</p>,
  tab2: <p>content2</p>,
};

const tabListNoTitle = [
  {
    key: 'article',
    tab: 'article',
  },
  {
    key: 'app',
    tab: 'app',
  },
  {
    key: 'project',
    tab: 'project',
  },
];

const contentListNoTitle: Record<string, React.ReactNode> = {
  article: <p>article content</p>,
  app: <p>app content</p>,
  project: <p>project content</p>,
};

const App: React.FC = () => {
  const [activeTabKey1, setActiveTabKey1] = useState<string>('tab1');
  const [activeTabKey2, setActiveTabKey2] = useState<string>('app');

  const onTab1Change = (key: string) => {
    setActiveTabKey1(key);
  };
  const onTab2Change = (key: string) => {
    setActiveTabKey2(key);
  };

  return (
    <>
      <Card
        style={{ width: '100%' }}
        title="Card title"
        extra={<a href="#">More</a>}
        tabList={tabList}
        activeTabKey={activeTabKey1}
        onTabChange={key => {
          onTab1Change(key);
        }}
      >
        {contentList[activeTabKey1]}
      </Card>
      <br />
      <br />
      <Card
        style={{ width: '100%' }}
        tabList={tabListNoTitle}
        activeTabKey={activeTabKey2}
        tabBarExtraContent={<a href="#">More</a>}
        onTabChange={key => {
          onTab2Change(key);
        }}
      >
        {contentListNoTitle[activeTabKey2]}
      </Card>
    </>
  );
};

export default App;
```

### 支持更多内容配置

- demo: `meta`

一种支持封面、头像、标题和描述信息的卡片。

```tsx
import { EditOutlined, EllipsisOutlined, SettingOutlined } from '@ant-design/icons';
import { Avatar, Card } from 'antd';
import React from 'react';

const { Meta } = Card;

const App: React.FC = () => (
  <Card
    style={{ width: 300 }}
    cover={
      <img
        alt="example"
        src="https://gw.alipayobjects.com/zos/rmsportal/JiqGstEfoWAOHiTxclqi.png"
      />
    }
    actions={[
      <SettingOutlined key="setting" />,
      <EditOutlined key="edit" />,
      <EllipsisOutlined key="ellipsis" />,
    ]}
  >
    <Meta
      avatar={<Avatar src="https://joeschmoe.io/api/v1/random" />}
      title="Card title"
      description="This is the description"
    />
  </Card>
);

export default App;
```
