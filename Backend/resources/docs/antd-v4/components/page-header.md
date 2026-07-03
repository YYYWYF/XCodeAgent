---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "page-header"
component: "PageHeader"
component_code_name: "PageHeader"
recommended_import: "import { PageHeader } from 'antd';"
subtitle_zh: "页头"
type_zh: "导航"
demo_count: 6
---

# PageHeader 页头

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`PageHeader`
- 推荐导入：`import { PageHeader } from 'antd';`
- 组件分类：导航
- 简述：页头位于页容器中，页容器顶部，起到了内容概览和引导页级操作的作用。包括由面包屑、标题、页面内容简介、页面级操作等、页面级导航组成。

## 示例索引

- 标准样式（basic）
- 白底模式（ghost）
- 带面包屑页头（breadcrumb）
- 组合示例（content）
- 多种形态的 PageHeader（actions）
- 响应式（responsive）

## 使用文档

页头位于页容器中，页容器顶部，起到了内容概览和引导页级操作的作用。包括由面包屑、标题、页面内容简介、页面级操作等、页面级导航组成。

### 何时使用

当需要使用户快速理解当前页是什么以及方便用户使用页面功能时使用，通常也可被用作页面间导航。

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| avatar | 标题栏旁的头像 | AvatarProps | - |  |
| backIcon | 自定义 back icon ，如果为 false 不渲染 back icon | ReactNode \| boolean | &lt;ArrowLeft /> |  |
| breadcrumb | 面包屑的配置 | Breadcrumb | - |  |
| breadcrumbRender | 自定义面包屑区域的内容 | `(props, originBreadcrumb) => ReactNode` | - | 4.11.0 |
| extra | 操作区，位于 title 行的行尾 | ReactNode | - |  |
| footer | PageHeader 的页脚，一般用于渲染 TabBar | ReactNode | - |  |
| ghost | pageHeader 的类型，将会改变背景颜色 | boolean | true |  |
| subTitle | 自定义的二级标题文字 | ReactNode | - |  |
| tags | title 旁的 tag 列表 | Tag\[] \| Tag | - |  |
| title | 自定义标题文字 | ReactNode | - |  |
| onBack | 返回按钮的点击事件 | () => void | - |  |

## 示例代码

### 标准样式

- demo: `basic`

标准页头，适合使用在需要简单描述的场景。

```tsx
import { PageHeader } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <PageHeader
    className="site-page-header"
    onBack={() => null}
    title="Title"
    subTitle="This is a subtitle"
  />
);

export default App;
```

```css
.site-page-header {
  border: 1px solid rgb(235, 237, 240);
}
```

### 白底模式

- demo: `ghost`

默认 PageHeader 是透明底色的。在某些情况下，PageHeader 需要自己的背景颜色。

```tsx
import { Button, Descriptions, PageHeader } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <div className="site-page-header-ghost-wrapper">
    <PageHeader
      ghost={false}
      onBack={() => window.history.back()}
      title="Title"
      subTitle="This is a subtitle"
      extra={[
        <Button key="3">Operation</Button>,
        <Button key="2">Operation</Button>,
        <Button key="1" type="primary">
          Primary
        </Button>,
      ]}
    >
      <Descriptions size="small" column={3}>
        <Descriptions.Item label="Created">Lili Qu</Descriptions.Item>
        <Descriptions.Item label="Association">
          <a>421421</a>
        </Descriptions.Item>
        <Descriptions.Item label="Creation Time">2017-01-10</Descriptions.Item>
        <Descriptions.Item label="Effective Time">2017-10-10</Descriptions.Item>
        <Descriptions.Item label="Remarks">
          Gonghu Road, Xihu District, Hangzhou, Zhejiang, China
        </Descriptions.Item>
      </Descriptions>
    </PageHeader>
  </div>
);

export default App;
```

```css
.site-page-header-ghost-wrapper {
  padding: 24px;
  background-color: #f5f5f5;
}
```

### 带面包屑页头

- demo: `breadcrumb`

带面包屑页头，适合层级比较深的页面，让用户可以快速导航。

```tsx
import { PageHeader } from 'antd';
import React from 'react';

const routes = [
  {
    path: 'index',
    breadcrumbName: 'First-level Menu',
  },
  {
    path: 'first',
    breadcrumbName: 'Second-level Menu',
  },
  {
    path: 'second',
    breadcrumbName: 'Third-level Menu',
  },
];

const App: React.FC = () => (
  <PageHeader
    className="site-page-header"
    title="Title"
    breadcrumb={{ routes }}
    subTitle="This is a subtitle"
  />
);

export default App;
```

### 组合示例

- demo: `content`

使用了 PageHeader 提供的所有能力。

```tsx
import { MoreOutlined } from '@ant-design/icons';
import { Button, Dropdown, PageHeader, Row, Tag, Typography } from 'antd';
import React from 'react';

const { Paragraph } = Typography;

const items = [
  {
    key: '1',
    label: (
      <a target="_blank" rel="noopener noreferrer" href="http://www.alipay.com/">
        1st menu item
      </a>
    ),
  },
  {
    key: '2',
    label: (
      <a target="_blank" rel="noopener noreferrer" href="http://www.taobao.com/">
        2nd menu item
      </a>
    ),
  },
  {
    key: '3',
    label: (
      <a target="_blank" rel="noopener noreferrer" href="http://www.tmall.com/">
        3rd menu item
      </a>
    ),
  },
];

const DropdownMenu = () => (
  <Dropdown key="more" menu={{ items }} placement="bottomRight">
    <Button type="text" icon={<MoreOutlined style={{ fontSize: 20 }} />} />
  </Dropdown>
);

const routes = [
  {
    path: 'index',
    breadcrumbName: 'First-level Menu',
  },
  {
    path: 'first',
    breadcrumbName: 'Second-level Menu',
  },
  {
    path: 'second',
    breadcrumbName: 'Third-level Menu',
  },
];

const IconLink = ({ src, text }: { src: string; text: string }) => (
  <a className="example-link">
    <img className="example-link-icon" src={src} alt={text} />
    {text}
  </a>
);

const content = (
  <>
    <Paragraph>
      Ant Design interprets the color system into two levels: a system-level color system and a
      product-level color system.
    </Paragraph>
    <Paragraph>
      Ant Design&#x27;s design team preferred to design with the HSB color model, which makes it
      easier for designers to have a clear psychological expectation of color when adjusting colors,
      as well as facilitate communication in teams.
    </Paragraph>
    <div>
      <IconLink
        src="https://gw.alipayobjects.com/zos/rmsportal/MjEImQtenlyueSmVEfUD.svg"
        text="Quick Start"
      />
      <IconLink
        src="https://gw.alipayobjects.com/zos/rmsportal/NbuDUAuBlIApFuDvWiND.svg"
        text=" Product Info"
      />
      <IconLink
        src="https://gw.alipayobjects.com/zos/rmsportal/ohOEPSYdDTNnyMbGuyLb.svg"
        text="Product Doc"
      />
    </div>
  </>
);

const Content: React.FC<{ children: React.ReactNode; extraContent: React.ReactNode }> = ({
  children,
  extraContent,
}) => (
  <Row>
    <div style={{ flex: 1 }}>{children}</div>
    <div className="image">{extraContent}</div>
  </Row>
);

const App: React.FC = () => (
  <PageHeader
    title="Title"
    className="site-page-header"
    subTitle="This is a subtitle"
    tags={<Tag color="blue">Running</Tag>}
    extra={[
      <Button key="3">Operation</Button>,
      <Button key="2">Operation</Button>,
      <Button key="1" type="primary">
        Primary
      </Button>,
      <DropdownMenu key="more" />,
    ]}
    avatar={{ src: 'https://avatars1.githubusercontent.com/u/8186664?s=460&v=4' }}
    breadcrumb={{ routes }}
  >
    <Content
      extraContent={
        <img
          src="https://gw.alipayobjects.com/zos/antfincdn/K%24NnlsB%26hz/pageHeader.svg"
          alt="content"
          width="100%"
        />
      }
    >
      {content}
    </Content>
  </PageHeader>
);

export default App;
```

```css
#components-page-header-demo-content .image {
  display: flex;
  align-items: center;
  margin: 0 0 0 60px;
}

#components-page-header-demo-content .ant-page-header-rtl .image {
  margin: 0 60px 0 0;
}

#components-page-header-demo-content .example-link {
  margin-right: 16px;
  line-height: 24px;
}
[data-theme='compact'] #components-page-header-demo-content .example-link {
  line-height: 20px;
}
#components-page-header-demo-content .example-link-icon {
  margin-right: 8px;
}

[data-theme='compact'] #components-page-header-demo-content .example-link-icon {
  width: 20px;
  height: 20px;
}

#components-page-header-demo-content .ant-page-header-rtl .example-link {
  float: right;
  margin-right: 0;
  margin-left: 16px;
}
#components-page-header-demo-content .ant-page-header-rtl .example-link-icon {
  margin-right: 0;
  margin-left: 8px;
}

@media (max-width: 768px) {
  #components-page-header-demo-content .image {
    flex: 100%;
    margin: 24px 0 0;
  }
}
```

### 多种形态的 PageHeader

- demo: `actions`

使用操作区，并自定义子节点，适合使用在需要展示一些复杂的信息，帮助用户快速了解这个页面的信息和操作。

```tsx
import { Button, Descriptions, PageHeader, Row, Statistic, Tag } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <PageHeader
      className="site-page-header"
      onBack={() => window.history.back()}
      title="Title"
      subTitle="This is a subtitle"
      extra={[
        <Button key="3">Operation</Button>,
        <Button key="2">Operation</Button>,
        <Button key="1" type="primary">
          Primary
        </Button>,
      ]}
    >
      <Descriptions size="small" column={3}>
        <Descriptions.Item label="Created">Lili Qu</Descriptions.Item>
        <Descriptions.Item label="Association">
          <a>421421</a>
        </Descriptions.Item>
        <Descriptions.Item label="Creation Time">2017-01-10</Descriptions.Item>
        <Descriptions.Item label="Effective Time">2017-10-10</Descriptions.Item>
        <Descriptions.Item label="Remarks">
          Gonghu Road, Xihu District, Hangzhou, Zhejiang, China
        </Descriptions.Item>
      </Descriptions>
    </PageHeader>
    <br />
    <PageHeader
      onBack={() => window.history.back()}
      title="Title"
      tags={<Tag color="blue">Running</Tag>}
      subTitle="This is a subtitle"
      extra={[
        <Button key="3">Operation</Button>,
        <Button key="2">Operation</Button>,
        <Button key="1" type="primary">
          Primary
        </Button>,
      ]}
    >
      <Row>
        <Statistic title="Status" value="Pending" />
        <Statistic
          title="Price"
          prefix="$"
          value={568.08}
          style={{
            margin: '0 32px',
          }}
        />
        <Statistic title="Balance" prefix="$" value={3345.08} />
      </Row>
    </PageHeader>
  </>
);

export default App;
```

### 响应式

- demo: `responsive`

在不同大小的屏幕下，应该有不同的表现。

```tsx
import { Button, Descriptions, PageHeader, Statistic, Tabs } from 'antd';
import React from 'react';

const { TabPane } = Tabs;

const renderContent = (column = 2) => (
  <Descriptions size="small" column={column}>
    <Descriptions.Item label="Created">Lili Qu</Descriptions.Item>
    <Descriptions.Item label="Association">
      <a>421421</a>
    </Descriptions.Item>
    <Descriptions.Item label="Creation Time">2017-01-10</Descriptions.Item>
    <Descriptions.Item label="Effective Time">2017-10-10</Descriptions.Item>
    <Descriptions.Item label="Remarks">
      Gonghu Road, Xihu District, Hangzhou, Zhejiang, China
    </Descriptions.Item>
  </Descriptions>
);

const extraContent = (
  <div
    style={{
      display: 'flex',
      width: 'max-content',
      justifyContent: 'flex-end',
    }}
  >
    <Statistic
      title="Status"
      value="Pending"
      style={{
        marginRight: 32,
      }}
    />
    <Statistic title="Price" prefix="$" value={568.08} />
  </div>
);

const Content: React.FC<{ children: React.ReactNode; extra: React.ReactNode }> = ({
  children,
  extra,
}) => (
  <div className="content">
    <div className="main">{children}</div>
    <div className="extra">{extra}</div>
  </div>
);

const App: React.FC = () => (
  <PageHeader
    className="site-page-header-responsive"
    onBack={() => window.history.back()}
    title="Title"
    subTitle="This is a subtitle"
    extra={[
      <Button key="3">Operation</Button>,
      <Button key="2">Operation</Button>,
      <Button key="1" type="primary">
        Primary
      </Button>,
    ]}
    footer={
      <Tabs defaultActiveKey="1">
        <TabPane tab="Details" key="1" />
        <TabPane tab="Rule" key="2" />
      </Tabs>
    }
  >
    <Content extra={extraContent}>{renderContent()}</Content>
  </PageHeader>
);

export default App;
```
