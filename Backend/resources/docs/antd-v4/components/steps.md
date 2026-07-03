---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "steps"
component: "Steps"
component_code_name: "Steps"
recommended_import: "import { Steps } from 'antd';"
subtitle_zh: "步骤条"
type_zh: "导航"
demo_count: 14
---

# Steps 步骤条

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Steps`
- 推荐导入：`import { Steps } from 'antd';`
- 组件分类：导航
- 简述：引导用户按照流程完成任务的导航条。

## 示例索引

- 基本用法（废弃的语法糖）（deprecated）
- 基本用法（simple）
- 迷你版（small-size）
- 带图标的步骤条（icon）
- 步骤切换（step-next）
- 竖直方向的步骤条（vertical）
- 竖直方向的小型步骤条（vertical-small）
- 步骤运行错误（error）
- 点状步骤条（progress-dot）
- 自定义点状步骤条（customized-progress-dot）
- 可点击（clickable）
- 导航步骤（nav）
- 带有进度的步骤（progress）
- 标签放置位置（label-placement）

## 使用文档

引导用户按照流程完成任务的导航条。

### 何时使用

当任务复杂或者存在先后关系时，将其分解成一系列步骤，从而简化任务。

#### 4.24.0 用法升级

```__react
import Alert from '../alert';
ReactDOM.render(<Alert message="在 4.24.0 版本后，我们提供了 <Steps items={[...]} /> 的简写方式，有更好的性能和更方便的数据组织方式，开发者不再需要自行拼接 JSX。同时我们废弃了原先的写法，你还是可以在 4.x 继续使用，但会在控制台看到警告，并会在 5.0 后移除。" />, mountNode);
```

```jsx
// >=4.24.0 可用，推荐的写法 ✅
const items = [{ title: '第一步' }, { title: '第二步' }, { title: '第三步' }];
return <Steps items={items} />;

// <4.24.0 可用，>=4.24.0 时不推荐 🙅🏻‍♀️
<Steps>
  <Step title="第一步" />
  <Step title="第二步" />
  <Step title="第三步" />
</Steps>;
```

### API

#### Steps

整体步骤条。

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| className | 步骤条类名 | string | - |  |
| current | 指定当前步骤，从 0 开始记数。在子 Step 元素中，可以通过 `status` 属性覆盖状态 | number | 0 |  |
| direction | 指定步骤条方向。目前支持水平（`horizontal`）和竖直（`vertical`）两种方向 | string | `horizontal` |  |
| initial | 起始序号，从 0 开始记数 | number | 0 |  |
| labelPlacement | 指定标签放置位置，默认水平放图标右侧，可选 `vertical` 放图标下方 | string | `horizontal` |  |
| percent | 当前 `process` 步骤显示的进度条进度（只对基本类型的 Steps 生效） | number | - | 4.5.0 |
| progressDot | 点状步骤条，可以设置为一个 function，labelPlacement 将强制为 `vertical` | boolean \| (iconDot, {index, status, title, description}) => ReactNode | false |  |
| responsive | 当屏幕宽度小于 `532px` 时自动变为垂直模式 | boolean | true |  |
| size | 指定大小，目前支持普通（`default`）和迷你（`small`） | string | `default` |  |
| status | 指定当前步骤的状态，可选 `wait` `process` `finish` `error` | string | `process` |  |
| type | 步骤条类型，有 `default` 和 `navigation` 两种 | string | `default` |  |
| onChange | 点击切换步骤时触发 | (current) => void | - |  |
| items | 配置选项卡内容 | StepItem | [] | 4.24.0 |

#### StepItem

步骤条内的每一个步骤。

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| description | 步骤的详情描述，可选 | ReactNode | - |  |
| disabled | 禁用点击 | boolean | false |  |
| icon | 步骤图标的类型，可选 | ReactNode | - |  |
| status | 指定状态。当不配置该属性时，会使用 Steps 的 `current` 来自动指定状态。可选：`wait` `process` `finish` `error` | string | `wait` |  |
| subTitle | 子标题 | ReactNode | - |  |
| title | 标题 | ReactNode | - |  |

## 示例代码

### 基本用法（废弃的语法糖）

- demo: `deprecated`

简单的步骤条。

```tsx
import { Steps } from 'antd';
import React from 'react';

const { Step } = Steps;
const description = 'This is a description.';
const App: React.FC = () => (
  <Steps current={1}>
    <Step title="Finished" description={description} />
    <Step title="In Progress" description={description} subTitle="Left 00:00:08" />
    <Step title="Waiting" description={description} />
  </Steps>
);

export default App;
```

### 基本用法

- demo: `simple`

简单的步骤条。

```tsx
import { Steps } from 'antd';
import React from 'react';

const description = 'This is a description.';
const App: React.FC = () => (
  <Steps
    current={1}
    items={[
      {
        title: 'Finished',
        description,
      },
      {
        title: 'In Progress',
        description,
        subTitle: 'Left 00:00:08',
      },
      {
        title: 'Waiting',
        description,
      },
    ]}
  />
);

export default App;
```

### 迷你版

- demo: `small-size`

迷你版的步骤条，通过设置 `` 启用.

```tsx
import { Steps } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Steps
    size="small"
    current={1}
    items={[
      {
        title: 'Finished',
      },
      {
        title: 'In Progress',
      },
      {
        title: 'Waiting',
      },
    ]}
  />
);

export default App;
```

### 带图标的步骤条

- demo: `icon`

通过设置 `items` 的 `icon` 属性，可以启用自定义图标。

```tsx
import { LoadingOutlined, SmileOutlined, SolutionOutlined, UserOutlined } from '@ant-design/icons';
import { Steps } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Steps
    items={[
      {
        title: 'Login',
        status: 'finish',
        icon: <UserOutlined />,
      },
      {
        title: 'Verification',
        status: 'finish',
        icon: <SolutionOutlined />,
      },
      {
        title: 'Pay',
        status: 'process',
        icon: <LoadingOutlined />,
      },
      {
        title: 'Done',
        status: 'wait',
        icon: <SmileOutlined />,
      },
    ]}
  />
);

export default App;
```

### 步骤切换

- demo: `step-next`

通常配合内容及按钮使用，表示一个流程的处理进度。

```tsx
import { Button, message, Steps } from 'antd';
import React, { useState } from 'react';

const steps = [
  {
    title: 'First',
    content: 'First-content',
  },
  {
    title: 'Second',
    content: 'Second-content',
  },
  {
    title: 'Last',
    content: 'Last-content',
  },
];

const App: React.FC = () => {
  const [current, setCurrent] = useState(0);

  const next = () => {
    setCurrent(current + 1);
  };

  const prev = () => {
    setCurrent(current - 1);
  };
  const items = steps.map(item => ({ key: item.title, title: item.title }));

  return (
    <>
      <Steps current={current} items={items} />
      <div className="steps-content">{steps[current].content}</div>
      <div className="steps-action">
        {current < steps.length - 1 && (
          <Button type="primary" onClick={() => next()}>
            Next
          </Button>
        )}
        {current === steps.length - 1 && (
          <Button type="primary" onClick={() => message.success('Processing complete!')}>
            Done
          </Button>
        )}
        {current > 0 && (
          <Button style={{ margin: '0 8px' }} onClick={() => prev()}>
            Previous
          </Button>
        )}
      </div>
    </>
  );
};

export default App;
```

```css
.steps-content {
  min-height: 200px;
  margin-top: 16px;
  padding-top: 80px;
  text-align: center;
  background-color: #fafafa;
  border: 1px dashed #e9e9e9;
  border-radius: 2px;
}

.steps-action {
  margin-top: 24px;
}
```

### 竖直方向的步骤条

- demo: `vertical`

简单的竖直方向的步骤条。

```tsx
import { Steps } from 'antd';
import React from 'react';

const description = 'This is a description.';
const App: React.FC = () => (
  <Steps
    direction="vertical"
    current={1}
    items={[
      {
        title: 'Finished',
        description,
      },
      {
        title: 'In Progress',
        description,
      },
      {
        title: 'Waiting',
        description,
      },
    ]}
  />
);

export default App;
```

### 竖直方向的小型步骤条

- demo: `vertical-small`

简单的竖直方向的小型步骤条。

```tsx
import { Steps } from 'antd';
import React from 'react';

const description = 'This is a description.';
const App: React.FC = () => (
  <Steps
    direction="vertical"
    size="small"
    current={1}
    items={[
      { title: 'Finished', description },
      {
        title: 'In Progress',
        description,
      },
      {
        title: 'Waiting',
        description,
      },
    ]}
  />
);

export default App;
```

### 步骤运行错误

- demo: `error`

使用 Steps 的 `status` 属性来指定当前步骤的状态。

```tsx
import { Steps } from 'antd';
import React from 'react';

const description = 'This is a description';
const App: React.FC = () => (
  <Steps
    current={1}
    status="error"
    items={[
      {
        title: 'Finished',
        description,
      },
      {
        title: 'In Process',
        description,
      },
      {
        title: 'Waiting',
        description,
      },
    ]}
  />
);

export default App;
```

### 点状步骤条

- demo: `progress-dot`

包含步骤点的进度条。

```tsx
import { Divider, Steps } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Steps
      progressDot
      current={1}
      items={[
        {
          title: 'Finished',
          description: 'This is a description.',
        },
        {
          title: 'In Progress',
          description: 'This is a description.',
        },
        {
          title: 'Waiting',
          description: 'This is a description.',
        },
      ]}
    />
    <Divider />
    <Steps
      progressDot
      current={1}
      direction="vertical"
      items={[
        {
          title: 'Finished',
          description: 'This is a description. This is a description.',
        },
        {
          title: 'Finished',
          description: 'This is a description. This is a description.',
        },
        {
          title: 'In Progress',
          description: 'This is a description. This is a description.',
        },
        {
          title: 'Waiting',
          description: 'This is a description.',
        },
        {
          title: 'Waiting',
          description: 'This is a description.',
        },
      ]}
    />
  </>
);

export default App;
```

### 自定义点状步骤条

- demo: `customized-progress-dot`

为点状步骤条增加自定义展示。

```tsx
import type { StepsProps } from 'antd';
import { Popover, Steps } from 'antd';
import React from 'react';

const customDot: StepsProps['progressDot'] = (dot, { status, index }) => (
  <Popover
    content={
      <span>
        step {index} status: {status}
      </span>
    }
  >
    {dot}
  </Popover>
);
const description = 'You can hover on the dot.';
const App: React.FC = () => (
  <Steps
    current={1}
    progressDot={customDot}
    items={[
      {
        title: 'Finished',
        description,
      },
      {
        title: 'In Progress',
        description,
      },
      {
        title: 'Waiting',
        description,
      },
      {
        title: 'Waiting',
        description,
      },
    ]}
  />
);

export default App;
```

### 可点击

- demo: `clickable`

设置 `onChange` 后，Steps 变为可点击状态。

```tsx
import { Divider, Steps } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [current, setCurrent] = useState(0);

  const onChange = (value: number) => {
    console.log('onChange:', current);
    setCurrent(value);
  };
  const description = 'This is a description.';

  return (
    <>
      <Steps
        current={current}
        onChange={onChange}
        items={[
          {
            title: 'Step 1',
            description,
          },
          {
            title: 'Step 2',
            description,
          },
          {
            title: 'Step 3',
            description,
          },
        ]}
      />

      <Divider />

      <Steps
        current={current}
        onChange={onChange}
        direction="vertical"
        items={[
          {
            title: 'Step 1',
            description,
          },
          {
            title: 'Step 2',
            description,
          },
          {
            title: 'Step 3',
            description,
          },
        ]}
      />
    </>
  );
};

export default App;
```

### 导航步骤

- demo: `nav`

导航类型的步骤条。

```tsx
import { Steps } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [current, setCurrent] = useState(0);

  const onChange = (value: number) => {
    console.log('onChange:', value);
    setCurrent(value);
  };

  return (
    <>
      <Steps
        type="navigation"
        size="small"
        current={current}
        onChange={onChange}
        className="site-navigation-steps"
        items={[
          {
            title: 'Step 1',
            subTitle: '00:00:05',
            status: 'finish',
            description: 'This is a description.',
          },
          {
            title: 'Step 2',
            subTitle: '00:01:02',
            status: 'process',
            description: 'This is a description.',
          },
          {
            title: 'Step 3',
            subTitle: 'waiting for longlong time',
            status: 'wait',
            description: 'This is a description.',
          },
        ]}
      />
      <Steps
        type="navigation"
        current={current}
        onChange={onChange}
        className="site-navigation-steps"
        items={[
          {
            status: 'finish',
            title: 'Step 1',
          },
          {
            status: 'process',
            title: 'Step 2',
          },
          {
            status: 'wait',
            title: 'Step 3',
          },
          {
            status: 'wait',
            title: 'Step 4',
          },
        ]}
      />
      <Steps
        type="navigation"
        size="small"
        current={current}
        onChange={onChange}
        className="site-navigation-steps"
        items={[
          {
            status: 'finish',
            title: 'finish 1',
          },
          {
            status: 'finish',
            title: 'finish 2',
          },
          {
            status: 'process',
            title: 'current process',
          },
          {
            status: 'wait',
            title: 'wait',
            disabled: true,
          },
        ]}
      />
    </>
  );
};

export default App;
```

```css
[data-theme='compact'] .site-navigation-steps,
.site-navigation-steps {
  margin-bottom: 60px;
  box-shadow: 0px -1px 0 0 #e8e8e8 inset;
}
```

### 带有进度的步骤

- demo: `progress`

带有进度的步骤。

```tsx
import { Steps } from 'antd';
import React from 'react';

const description = 'This is a description.';
const App: React.FC = () => (
  <Steps
    current={1}
    percent={60}
    items={[
      {
        title: 'Finished',
        description,
      },
      {
        title: 'In Progress',
        subTitle: 'Left 00:00:08',
        description,
      },
      {
        title: 'Waiting',
        description,
      },
    ]}
  />
);

export default App;
```

### 标签放置位置

- demo: `label-placement`

修改标签放置位置为 `vertical`。

```tsx
import { Steps } from 'antd';
import React from 'react';

const description = 'This is a description.';
const items = [
  {
    title: 'Finished',
    description,
  },
  {
    title: 'In Progress',
    description,
  },
  {
    title: 'Waiting',
    description,
  },
];
const App: React.FC = () => (
  <>
    <Steps current={1} labelPlacement="vertical" items={items} />
    <br />
    <Steps current={1} percent={60} labelPlacement="vertical" items={items} />
    <br />
    <Steps current={1} size="small" labelPlacement="vertical" items={items} />
  </>
);

export default App;
```
