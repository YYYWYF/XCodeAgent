---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "timeline"
component: "Timeline"
component_code_name: "Timeline"
recommended_import: "import { Timeline } from 'antd';"
subtitle_zh: "时间轴"
type_zh: "数据展示"
demo_count: 7
---

# Timeline 时间轴

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Timeline`
- 推荐导入：`import { Timeline } from 'antd';`
- 组件分类：数据展示
- 简述：垂直展示的时间流信息。

## 示例索引

- 基本用法（basic）
- 圆圈颜色（color）
- 最后一个及排序（pending）
- 交替展现（alternate）
- 自定义时间轴点（custom）
- 右侧时间轴点（right）
- 标签（label）

## 使用文档

垂直展示的时间流信息。

### 何时使用

- 当有一系列信息需按时间排列时，可正序和倒序。
- 需要有一条时间轴进行视觉上的串联时。

### API

```jsx
<Timeline>
  <Timeline.Item>创建服务现场 2015-09-01</Timeline.Item>
  <Timeline.Item>初步排除网络异常 2015-09-01</Timeline.Item>
  <Timeline.Item>技术测试异常 2015-09-01</Timeline.Item>
  <Timeline.Item>网络异常正在修复 2015-09-01</Timeline.Item>
</Timeline>
```

#### Timeline

时间轴。

| 参数 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| mode | 通过设置 `mode` 可以改变时间轴和内容的相对位置 | `left` \| `alternate` \| `right` | - |
| pending | 指定最后一个幽灵节点是否存在或内容 | boolean \| ReactNode | false |
| pendingDot | 当最后一个幽灵节点存在時，指定其时间图点 | ReactNode | &lt;LoadingOutlined /> |
| reverse | 节点排序 | boolean | false |

#### Timeline.Item

时间轴的每一个节点。

| 参数 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| color | 指定圆圈颜色 `blue`、`red`、`green`、`gray`，或自定义的色值 | string | `blue` |
| dot | 自定义时间轴点 | ReactNode | - |
| label | 设置标签 | ReactNode | - |
| position | 自定义节点位置 | `left` \| `right` | - |

## 示例代码

### 基本用法

- demo: `basic`

基本的时间轴。

```tsx
import { Timeline } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Timeline>
    <Timeline.Item>Create a services site 2015-09-01</Timeline.Item>
    <Timeline.Item>Solve initial network problems 2015-09-01</Timeline.Item>
    <Timeline.Item>Technical testing 2015-09-01</Timeline.Item>
    <Timeline.Item>Network problems being solved 2015-09-01</Timeline.Item>
  </Timeline>
);

export default App;
```

### 圆圈颜色

- demo: `color`

圆圈颜色，绿色用于已完成、成功状态，红色表示告警或错误状态，蓝色可表示正在进行或其他默认状态，灰色表示未完成或失效状态。

```tsx
import { SmileOutlined } from '@ant-design/icons';
import { Timeline } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Timeline>
    <Timeline.Item color="green">Create a services site 2015-09-01</Timeline.Item>
    <Timeline.Item color="green">Create a services site 2015-09-01</Timeline.Item>
    <Timeline.Item color="red">
      <p>Solve initial network problems 1</p>
      <p>Solve initial network problems 2</p>
      <p>Solve initial network problems 3 2015-09-01</p>
    </Timeline.Item>
    <Timeline.Item>
      <p>Technical testing 1</p>
      <p>Technical testing 2</p>
      <p>Technical testing 3 2015-09-01</p>
    </Timeline.Item>
    <Timeline.Item color="gray">
      <p>Technical testing 1</p>
      <p>Technical testing 2</p>
      <p>Technical testing 3 2015-09-01</p>
    </Timeline.Item>
    <Timeline.Item color="gray">
      <p>Technical testing 1</p>
      <p>Technical testing 2</p>
      <p>Technical testing 3 2015-09-01</p>
    </Timeline.Item>
    <Timeline.Item color="#00CCFF" dot={<SmileOutlined />}>
      <p>Custom color testing</p>
    </Timeline.Item>
  </Timeline>
);

export default App;
```

### 最后一个及排序

- demo: `pending`

当任务状态正在发生，还在记录过程中，可用幽灵节点来表示当前的时间节点，当 pending 为真值时展示幽灵节点，如果 pending 是 React 元素可用于定制该节点内容，同时 pendingDot 将可以用于定制其轴点。reverse 属性用于控制节点排序，为 false 时按正序排列，为 true 时按倒序排列。

```tsx
import { Button, Timeline } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [reverse, setReverse] = useState(false);

  const handleClick = () => {
    setReverse(!reverse);
  };

  return (
    <div>
      <Timeline pending="Recording..." reverse={reverse}>
        <Timeline.Item>Create a services site 2015-09-01</Timeline.Item>
        <Timeline.Item>Solve initial network problems 2015-09-01</Timeline.Item>
        <Timeline.Item>Technical testing 2015-09-01</Timeline.Item>
      </Timeline>
      <Button type="primary" style={{ marginTop: 16 }} onClick={handleClick}>
        Toggle Reverse
      </Button>
    </div>
  );
};

export default App;
```

### 交替展现

- demo: `alternate`

内容在时间轴两侧轮流出现。

```tsx
import { ClockCircleOutlined } from '@ant-design/icons';
import { Timeline } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Timeline mode="alternate">
    <Timeline.Item>Create a services site 2015-09-01</Timeline.Item>
    <Timeline.Item color="green">Solve initial network problems 2015-09-01</Timeline.Item>
    <Timeline.Item dot={<ClockCircleOutlined style={{ fontSize: '16px' }} />}>
      Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque
      laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto
      beatae vitae dicta sunt explicabo.
    </Timeline.Item>
    <Timeline.Item color="red">Network problems being solved 2015-09-01</Timeline.Item>
    <Timeline.Item>Create a services site 2015-09-01</Timeline.Item>
    <Timeline.Item dot={<ClockCircleOutlined style={{ fontSize: '16px' }} />}>
      Technical testing 2015-09-01
    </Timeline.Item>
  </Timeline>
);

export default App;
```

### 自定义时间轴点

- demo: `custom`

可以设置为图标或其他自定义元素。

```tsx
import { ClockCircleOutlined } from '@ant-design/icons';
import { Timeline } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Timeline>
    <Timeline.Item>Create a services site 2015-09-01</Timeline.Item>
    <Timeline.Item>Solve initial network problems 2015-09-01</Timeline.Item>
    <Timeline.Item dot={<ClockCircleOutlined className="timeline-clock-icon" />} color="red">
      Technical testing 2015-09-01
    </Timeline.Item>
    <Timeline.Item>Network problems being solved 2015-09-01</Timeline.Item>
  </Timeline>
);

export default App;
```

```css
.timeline-clock-icon {
  font-size: 16px;
}

[data-theme='compact'] .timeline-clock-icon {
  font-size: 14px;
}
```

### 右侧时间轴点

- demo: `right`

时间轴点可以在内容的右边。

```tsx
import { ClockCircleOutlined } from '@ant-design/icons';
import { Timeline } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Timeline mode="right">
    <Timeline.Item>Create a services site 2015-09-01</Timeline.Item>
    <Timeline.Item>Solve initial network problems 2015-09-01</Timeline.Item>
    <Timeline.Item dot={<ClockCircleOutlined style={{ fontSize: '16px' }} />} color="red">
      Technical testing 2015-09-01
    </Timeline.Item>
    <Timeline.Item>Network problems being solved 2015-09-01</Timeline.Item>
  </Timeline>
);

export default App;
```

### 标签

- demo: `label`

使用 `label` 标签单独展示时间。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Radio, Timeline } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [mode, setMode] = useState<'left' | 'alternate' | 'right'>('left');

  const onChange = (e: RadioChangeEvent) => {
    setMode(e.target.value);
  };

  return (
    <>
      <Radio.Group
        onChange={onChange}
        value={mode}
        style={{
          marginBottom: 20,
        }}
      >
        <Radio value="left">Left</Radio>
        <Radio value="right">Right</Radio>
        <Radio value="alternate">Alternate</Radio>
      </Radio.Group>
      <Timeline mode={mode}>
        <Timeline.Item label="2015-09-01">Create a services</Timeline.Item>
        <Timeline.Item label="2015-09-01 09:12:11">Solve initial network problems</Timeline.Item>
        <Timeline.Item>Technical testing</Timeline.Item>
        <Timeline.Item label="2015-09-01 09:12:11">Network problems being solved</Timeline.Item>
      </Timeline>
    </>
  );
};

export default App;
```
