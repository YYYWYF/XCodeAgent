---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "progress"
component: "Progress"
component_code_name: "Progress"
recommended_import: "import { Progress } from 'antd';"
subtitle_zh: "进度条"
type_zh: "反馈"
demo_count: 12
---

# Progress 进度条

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Progress`
- 推荐导入：`import { Progress } from 'antd';`
- 组件分类：反馈
- 简述：展示操作的当前进度。

## 示例索引

- 进度条（line）
- 进度圈（circle）
- 小型进度条（line-mini）
- 小型进度圈（circle-mini）
- 进度圈动态展示（circle-dynamic）
- 动态展示（dynamic）
- 自定义文字格式（format）
- 仪表盘（dashboard）
- 分段进度条（segment）
- 边缘形状（linecap）
- 自定义进度条渐变色（gradient-line）
- 步骤进度条（steps）

## 使用文档

展示操作的当前进度。

### 何时使用

在操作需要较长时间才能完成时，为用户显示该操作的当前进度和状态。

- 当一个操作会打断当前界面，或者需要在后台运行，且耗时可能超过 2 秒时；
- 当需要显示一个操作完成的百分比时。

### API

各类型共用的属性。

| 属性 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| format | 内容的模板函数 | function(percent, successPercent) | (percent) => percent + `%` |
| percent | 百分比 | number | 0 |
| showInfo | 是否显示进度数值或状态图标 | boolean | true |
| status | 状态，可选：`success` `exception` `normal` `active`(仅限 line) | string | - |
| strokeColor | 进度条的色彩 | string | - |
| strokeLinecap | 进度条的样式 | `round` \| `butt` \| `square`，区别详见 stroke-linecap | `round` |
| success | 成功进度条相关配置 | { percent: number, strokeColor: string } | - |
| trailColor | 未完成的分段的颜色 | string | - |
| type | 类型，可选 `line` `circle` `dashboard` | string | `line` |

#### `type="line"`

| 属性 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| steps | 进度条总共步数 | number | - | - |
| strokeColor | 进度条的色彩，传入 object 时为渐变。当有 `steps` 时支持传入一个数组。 | string \| string[] \| { from: string; to: string; direction: string } | - | 4.21.0: `string[]` |
| strokeWidth | 进度条线的宽度，单位 px | number | 10 | - |

#### `type="circle"`

| 属性        | 说明                                             | 类型             | 默认值 |
| ----------- | ------------------------------------------------ | ---------------- | ------ |
| strokeColor | 圆形进度条线的色彩，传入 object 时为渐变         | string \| object | -      |
| strokeWidth | 圆形进度条线的宽度，单位是进度条画布宽度的百分比 | number           | 6      |
| width       | 圆形进度条画布宽度，单位 px                      | number           | 132    |

#### `type="dashboard"`

| 属性 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| gapDegree | 仪表盘进度条缺口角度，可取值 0 ~ 295 | number | 75 |
| gapPosition | 仪表盘进度条缺口位置 | `top` \| `bottom` \| `left` \| `right` | `bottom` |
| strokeWidth | 仪表盘进度条线的宽度，单位是进度条画布宽度的百分比 | number | 6 |
| width | 仪表盘进度条画布宽度，单位 px | number | 132 |

## 示例代码

### 进度条

- demo: `line`

标准的进度条。

```tsx
import { Progress } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Progress percent={30} />
    <Progress percent={50} status="active" />
    <Progress percent={70} status="exception" />
    <Progress percent={100} />
    <Progress percent={50} showInfo={false} />
  </>
);

export default App;
```

### 进度圈

- demo: `circle`

圈形的进度。

```tsx
import { Progress } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Progress type="circle" percent={75} />
    <Progress type="circle" percent={70} status="exception" />
    <Progress type="circle" percent={100} />
  </>
);

export default App;
```

### 小型进度条

- demo: `line-mini`

适合放在较狭窄的区域内。

```tsx
import { Progress } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <div style={{ width: 170 }}>
    <Progress percent={30} size="small" />
    <Progress percent={50} size="small" status="active" />
    <Progress percent={70} size="small" status="exception" />
    <Progress percent={100} size="small" />
  </div>
);

export default App;
```

### 小型进度圈

- demo: `circle-mini`

小一号的圈形进度。

```tsx
import { Progress } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Progress type="circle" percent={30} width={80} />
    <Progress type="circle" percent={70} width={80} status="exception" />
    <Progress type="circle" percent={100} width={80} />
  </>
);

export default App;
```

### 进度圈动态展示

- demo: `circle-dynamic`

会动的进度条才是好进度条。

```tsx
import { MinusOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Progress } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [percent, setPercent] = useState(0);

  const increase = () => {
    let newPercent = percent + 10;
    if (newPercent > 100) {
      newPercent = 100;
    }
    setPercent(newPercent);
  };

  const decline = () => {
    let newPercent = percent - 10;
    if (newPercent < 0) {
      newPercent = 0;
    }
    setPercent(newPercent);
  };

  return (
    <>
      <Progress type="circle" percent={percent} />
      <Button.Group>
        <Button onClick={decline} icon={<MinusOutlined />} />
        <Button onClick={increase} icon={<PlusOutlined />} />
      </Button.Group>
    </>
  );
};

export default App;
```

### 动态展示

- demo: `dynamic`

会动的进度条才是好进度条。

```tsx
import { MinusOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Progress } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [percent, setPercent] = useState(0);

  const increase = () => {
    let newPercent = percent + 10;
    if (newPercent > 100) {
      newPercent = 100;
    }
    setPercent(newPercent);
  };

  const decline = () => {
    let newPercent = percent - 10;
    if (newPercent < 0) {
      newPercent = 0;
    }
    setPercent(newPercent);
  };

  return (
    <>
      <Progress percent={percent} />
      <Button.Group>
        <Button onClick={decline} icon={<MinusOutlined />} />
        <Button onClick={increase} icon={<PlusOutlined />} />
      </Button.Group>
    </>
  );
};

export default App;
```

### 自定义文字格式

- demo: `format`

`format` 属性指定格式。

```tsx
import { Progress } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Progress type="circle" percent={75} format={percent => `${percent} Days`} />
    <Progress type="circle" percent={100} format={() => 'Done'} />
  </>
);

export default App;
```

### 仪表盘

- demo: `dashboard`

通过设置 `type=dashboard`，可以很方便地实现仪表盘样式的进度条。若想要修改缺口的角度，可以设置 `gapDegree` 为你想要的值。

```tsx
import { Progress } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Progress type="dashboard" percent={75} />
    <Progress type="dashboard" percent={75} gapDegree={30} />
  </>
);

export default App;
```

### 分段进度条

- demo: `segment`

标准的进度条。`type="circle|dashboard"` 时不支持分段颜色。

```tsx
import { Progress, Tooltip } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Tooltip title="3 done / 3 in progress / 4 to do">
      <Progress percent={60} success={{ percent: 30 }} />
    </Tooltip>

    <Tooltip title="3 done / 3 in progress / 4 to do">
      <Progress percent={60} success={{ percent: 30 }} type="circle" />
    </Tooltip>

    <Tooltip title="3 done / 3 in progress / 4 to do">
      <Progress percent={60} success={{ percent: 30 }} type="dashboard" />
    </Tooltip>
  </>
);

export default App;
```

### 边缘形状

- demo: `linecap`

通过设定 `strokeLinecap="butt"` 可以调整进度条边缘的形状为方形，详见 stroke-linecap。

```tsx
import { Progress } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Progress strokeLinecap="butt" percent={75} />
    <Progress strokeLinecap="butt" type="circle" percent={75} />
    <Progress strokeLinecap="butt" type="dashboard" percent={75} />
  </>
);

export default App;
```

### 自定义进度条渐变色

- demo: `gradient-line`

`linear-gradient` 的封装。推荐只传两种颜色。

```tsx
import { Progress } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Progress
      strokeColor={{
        '0%': '#108ee9',
        '100%': '#87d068',
      }}
      percent={99.9}
    />
    <Progress
      strokeColor={{
        from: '#108ee9',
        to: '#87d068',
      }}
      percent={99.9}
      status="active"
    />
    <Progress
      type="circle"
      strokeColor={{
        '0%': '#108ee9',
        '100%': '#87d068',
      }}
      percent={90}
    />
    <Progress
      type="circle"
      strokeColor={{
        '0%': '#108ee9',
        '100%': '#87d068',
      }}
      percent={100}
    />
  </>
);

export default App;
```

### 步骤进度条

- demo: `steps`

带步骤的进度条。

```tsx
import React from 'react';
import { Progress } from 'antd';
import { red, green } from '@ant-design/colors';

const App: React.FC = () => (
  <>
    <Progress percent={50} steps={3} />
    <br />
    <Progress percent={30} steps={5} />
    <br />
    <Progress percent={100} steps={5} size="small" strokeColor={green[6]} />
    <br />
    <Progress percent={60} steps={5} strokeColor={[green[6], green[6], red[5]]} />
  </>
);

export default App;
```
