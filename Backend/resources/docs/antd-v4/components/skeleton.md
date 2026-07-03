---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "skeleton"
component: "Skeleton"
component_code_name: "Skeleton"
recommended_import: "import { Skeleton } from 'antd';"
subtitle_zh: "骨架屏"
type_zh: "反馈"
demo_count: 6
---

# Skeleton 骨架屏

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Skeleton`
- 推荐导入：`import { Skeleton } from 'antd';`
- 组件分类：反馈
- 简述：在需要等待加载内容的位置提供一个占位图形组合。

## 示例索引

- 基本（basic）
- 复杂的组合（complex）
- 动画效果（active）
- 按钮/头像/输入框/图像/自定义节点（element）
- 包含子组件（children）
- 列表（list）

## 使用文档

在需要等待加载内容的位置提供一个占位图形组合。

### 何时使用

- 网络较慢，需要长时间等待加载处理的情况下。
- 图文信息内容较多的列表/卡片中。
- 只在第一次加载数据的时候使用。
- 可以被 Spin 完全代替，但是在可用的场景下可以比 Spin 提供更好的视觉效果和用户体验。

### API

#### Skeleton

| 属性 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| active | 是否展示动画效果 | boolean | false |
| avatar | 是否显示头像占位图 | boolean \| SkeletonAvatarProps | false |
| loading | 为 true 时，显示占位图。反之则直接展示子组件 | boolean | - |
| paragraph | 是否显示段落占位图 | boolean \| SkeletonParagraphProps | true |
| round | 为 true 时，段落和标题显示圆角 | boolean | false |
| title | 是否显示标题占位图 | boolean \| SkeletonTitleProps | true |

#### SkeletonAvatarProps

| 属性 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| active | 是否展示动画效果，仅在单独使用头像骨架时生效 | boolean | false |
| shape | 指定头像的形状 | `circle` \| `square` | - |
| size | 设置头像占位图的大小 | number \| `large` \| `small` \| `default` | - |

#### SkeletonTitleProps

| 属性  | 说明                 | 类型             | 默认值 |
| ----- | -------------------- | ---------------- | ------ |
| width | 设置标题占位图的宽度 | number \| string | -      |

#### SkeletonParagraphProps

| 属性 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| rows | 设置段落占位图的行数 | number | - |
| width | 设置段落占位图的宽度，若为数组时则为对应的每行宽度，反之则是最后一行的宽度 | number \| string \| Array&lt;number \| string> | - |

#### SkeletonButtonProps

| 属性 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| active | 是否展示动画效果 | boolean | false |  |
| block | 将按钮宽度调整为其父宽度的选项 | boolean | false | 4.17.0 |
| shape | 指定按钮的形状 | `circle` \| `round` \| `square` \| `default` | - |  |
| size | 设置按钮的大小 | `large` \| `small` \| `default` | - |  |

#### SkeletonInputProps

| 属性   | 说明             | 类型                            | 默认值 |
| ------ | ---------------- | ------------------------------- | ------ |
| active | 是否展示动画效果 | boolean                         | false  |
| size   | 设置输入框的大小 | `large` \| `small` \| `default` | -      |

## 示例代码

### 基本

- demo: `basic`

最简单的占位效果。

```tsx
import { Skeleton } from 'antd';
import React from 'react';

const App: React.FC = () => <Skeleton />;

export default App;
```

### 复杂的组合

- demo: `complex`

更复杂的组合。

```tsx
import { Skeleton } from 'antd';
import React from 'react';

const App: React.FC = () => <Skeleton avatar paragraph={{ rows: 4 }} />;

export default App;
```

### 动画效果

- demo: `active`

显示动画效果。

```tsx
import { Skeleton } from 'antd';
import React from 'react';

const App: React.FC = () => <Skeleton active />;

export default App;
```

### 按钮/头像/输入框/图像/自定义节点

- demo: `element`

骨架按钮、头像、输入框、图像和自定义节点。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Divider, Form, Radio, Skeleton, Space, Switch } from 'antd';
import { DotChartOutlined } from '@ant-design/icons';
import React, { useState } from 'react';

type SizeType = 'default' | 'small' | 'large';
type ButtonShapeType = 'circle' | 'square' | 'round' | 'default';
type AvatarShapeType = 'circle' | 'square';

const App: React.FC = () => {
  const [active, setActive] = useState(false);
  const [block, setBlock] = useState(false);
  const [size, setSize] = useState<SizeType>('default');
  const [buttonShape, setButtonShape] = useState<ButtonShapeType>('default');
  const [avatarShape, setAvatarShape] = useState<AvatarShapeType>('circle');

  const handleActiveChange = (checked: boolean) => {
    setActive(checked);
  };

  const handleBlockChange = (checked: boolean) => {
    setBlock(checked);
  };

  const handleSizeChange = (e: RadioChangeEvent) => {
    setSize(e.target.value);
  };

  const handleShapeButton = (e: RadioChangeEvent) => {
    setButtonShape(e.target.value);
  };

  const handleAvatarShape = (e: RadioChangeEvent) => {
    setAvatarShape(e.target.value);
  };

  return (
    <>
      <Space>
        <Skeleton.Button active={active} size={size} shape={buttonShape} block={block} />
        <Skeleton.Avatar active={active} size={size} shape={avatarShape} />
        <Skeleton.Input active={active} size={size} />
      </Space>
      <br />
      <br />
      <Skeleton.Button active={active} size={size} shape={buttonShape} block={block} />
      <br />
      <br />
      <Skeleton.Input active={active} size={size} block={block} />
      <br />
      <br />
      <Space>
        <Skeleton.Image active={active} />
        <Skeleton.Node active={active}>
          <DotChartOutlined style={{ fontSize: 40, color: '#bfbfbf' }} />
        </Skeleton.Node>
      </Space>
      <Divider />
      <Form layout="inline" style={{ margin: '16px 0' }}>
        <Form.Item label="Active">
          <Switch checked={active} onChange={handleActiveChange} />
        </Form.Item>
        <Form.Item label="Button and Input Block">
          <Switch checked={block} onChange={handleBlockChange} />
        </Form.Item>
        <Form.Item label="Size">
          <Radio.Group value={size} onChange={handleSizeChange}>
            <Radio.Button value="default">Default</Radio.Button>
            <Radio.Button value="large">Large</Radio.Button>
            <Radio.Button value="small">Small</Radio.Button>
          </Radio.Group>
        </Form.Item>
        <Form.Item label="Button Shape">
          <Radio.Group value={buttonShape} onChange={handleShapeButton}>
            <Radio.Button value="default">Default</Radio.Button>
            <Radio.Button value="square">Square</Radio.Button>
            <Radio.Button value="round">Round</Radio.Button>
            <Radio.Button value="circle">Circle</Radio.Button>
          </Radio.Group>
        </Form.Item>
        <Form.Item label="Avatar Shape">
          <Radio.Group value={avatarShape} onChange={handleAvatarShape}>
            <Radio.Button value="square">Square</Radio.Button>
            <Radio.Button value="circle">Circle</Radio.Button>
          </Radio.Group>
        </Form.Item>
      </Form>
    </>
  );
};

export default App;
```

### 包含子组件

- demo: `children`

加载占位图包含子组件。

```tsx
import { Button, Skeleton } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [loading, setLoading] = useState(false);

  const showSkeleton = () => {
    setLoading(true);

    setTimeout(() => {
      setLoading(false);
    }, 3000);
  };

  return (
    <div className="article">
      <Skeleton loading={loading}>
        <div>
          <h4>Ant Design, a design language</h4>
          <p>
            We supply a series of design principles, practical patterns and high quality design
            resources (Sketch and Axure), to help people create their product prototypes beautifully
            and efficiently.
          </p>
        </div>
      </Skeleton>
      <Button onClick={showSkeleton} disabled={loading}>
        Show Skeleton
      </Button>
    </div>
  );
};

export default App;
```

### 列表

- demo: `list`

在列表组件中使用加载占位符。

```tsx
import type Icon from '@ant-design/icons';
import { LikeOutlined, MessageOutlined, StarOutlined } from '@ant-design/icons';
import { Avatar, List, Skeleton, Switch } from 'antd';
import React, { useState } from 'react';

interface IconTextProps {
  icon: typeof Icon;
  text: React.ReactNode;
}

const listData = Array.from({ length: 3 }).map((_, i) => ({
  href: 'https://ant.design',
  title: `ant design part ${i}`,
  avatar: 'https://joeschmoe.io/api/v1/random',
  description:
    'Ant Design, a design language for background applications, is refined by Ant UED Team.',
  content:
    'We supply a series of design principles, practical patterns and high quality design resources (Sketch and Axure), to help people create their product prototypes beautifully and efficiently.',
}));

const IconText = ({ icon, text }: IconTextProps) => (
  <span>
    {React.createElement(icon, { style: { marginRight: 8 } })}
    {text}
  </span>
);

const App: React.FC = () => {
  const [loading, setLoading] = useState(true);

  const onChange = (checked: boolean) => {
    setLoading(!checked);
  };
  return (
    <>
      <Switch checked={!loading} onChange={onChange} />

      <List
        itemLayout="vertical"
        size="large"
        dataSource={listData}
        renderItem={item => (
          <List.Item
            key={item.title}
            actions={
              !loading
                ? [
                    <IconText icon={StarOutlined} text="156" key="list-vertical-star-o" />,
                    <IconText icon={LikeOutlined} text="156" key="list-vertical-like-o" />,
                    <IconText icon={MessageOutlined} text="2" key="list-vertical-message" />,
                  ]
                : undefined
            }
            extra={
              !loading && (
                <img
                  width={272}
                  alt="logo"
                  src="https://gw.alipayobjects.com/zos/rmsportal/mqaQswcyDLcXyDKnZfES.png"
                />
              )
            }
          >
            <Skeleton loading={loading} active avatar>
              <List.Item.Meta
                avatar={<Avatar src={item.avatar} />}
                title={<a href={item.href}>{item.title}</a>}
                description={item.description}
              />
              {item.content}
            </Skeleton>
          </List.Item>
        )}
      />
    </>
  );
};

export default App;
```
