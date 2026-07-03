---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "statistic"
component: "Statistic"
component_code_name: "Statistic"
recommended_import: "import { Statistic } from 'antd';"
subtitle_zh: "统计数值"
type_zh: "数据展示"
demo_count: 4
---

# Statistic 统计数值

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Statistic`
- 推荐导入：`import { Statistic } from 'antd';`
- 组件分类：数据展示
- 简述：展示统计数值。

## 示例索引

- 基本（basic）
- 单位（unit）
- 在卡片中使用（card）
- 倒计时（countdown）

## 使用文档

展示统计数值。

### 何时使用

- 当需要突出某个或某组数字时。
- 当需要展示带描述的统计类数据时使用。

### API

##### Statistic

| 参数             | 说明               | 类型                 | 默认值 | 版本  |
| ---------------- | ------------------ | -------------------- | ------ | ----- |
| decimalSeparator | 设置小数点         | string               | `.`    |       |
| formatter        | 自定义数值展示     | (value) => ReactNode | -      |       |
| groupSeparator   | 设置千分位标识符   | string               | `,`    |       |
| loading          | 数值是否加载中     | boolean              | false  | 4.8.0 |
| precision        | 数值精度           | number               | -      |       |
| prefix           | 设置数值的前缀     | ReactNode            | -      |       |
| suffix           | 设置数值的后缀     | ReactNode            | -      |       |
| title            | 数值的标题         | ReactNode            | -      |       |
| value            | 数值内容           | string \| number     | -      |       |
| valueStyle       | 设置数值区域的样式 | CSSProperties        | -      |       |

##### Statistic.Countdown

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| format | 格式化倒计时展示，参考 moment | string | `HH:mm:ss` |  |
| prefix | 设置数值的前缀 | ReactNode | - |  |
| suffix | 设置数值的后缀 | ReactNode | - |  |
| title | 数值的标题 | ReactNode | - |  |
| value | 数值内容 | number \| moment | - |  |
| valueStyle | 设置数值区域的样式 | CSSProperties | - |  |
| onFinish | 倒计时完成时触发 | () => void | - |  |
| onChange | 倒计时时间变化时触发 | (value: number) => void | - | 4.16.0 |

## 示例代码

### 基本

- demo: `basic`

简单的展示。

```tsx
import { Button, Col, Row, Statistic } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Row gutter={16}>
    <Col span={12}>
      <Statistic title="Active Users" value={112893} />
    </Col>
    <Col span={12}>
      <Statistic title="Account Balance (CNY)" value={112893} precision={2} />
      <Button style={{ marginTop: 16 }} type="primary">
        Recharge
      </Button>
    </Col>
    <Col span={12}>
      <Statistic title="Active Users" value={112893} loading />
    </Col>
  </Row>
);

export default App;
```

### 单位

- demo: `unit`

通过前缀和后缀添加单位。

```tsx
import { LikeOutlined } from '@ant-design/icons';
import { Col, Row, Statistic } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Row gutter={16}>
    <Col span={12}>
      <Statistic title="Feedback" value={1128} prefix={<LikeOutlined />} />
    </Col>
    <Col span={12}>
      <Statistic title="Unmerged" value={93} suffix="/ 100" />
    </Col>
  </Row>
);

export default App;
```

### 在卡片中使用

- demo: `card`

在卡片中展示统计数值。

```tsx
import { ArrowDownOutlined, ArrowUpOutlined } from '@ant-design/icons';
import { Card, Col, Row, Statistic } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <div className="site-statistic-demo-card">
    <Row gutter={16}>
      <Col span={12}>
        <Card>
          <Statistic
            title="Active"
            value={11.28}
            precision={2}
            valueStyle={{ color: '#3f8600' }}
            prefix={<ArrowUpOutlined />}
            suffix="%"
          />
        </Card>
      </Col>
      <Col span={12}>
        <Card>
          <Statistic
            title="Idle"
            value={9.3}
            precision={2}
            valueStyle={{ color: '#cf1322' }}
            prefix={<ArrowDownOutlined />}
            suffix="%"
          />
        </Card>
      </Col>
    </Row>
  </div>
);

export default App;
```

```css
.site-statistic-demo-card {
  padding: 30px;
  background: #ececec;
}
```

### 倒计时

- demo: `countdown`

倒计时组件。

```tsx
import { Col, Row, Statistic } from 'antd';
import type { countdownValueType } from 'antd/es/statistic/utils';
import React from 'react';

const { Countdown } = Statistic;
const deadline = Date.now() + 1000 * 60 * 60 * 24 * 2 + 1000 * 30; // Moment is also OK

const App: React.FC = () => {
  const onFinish = () => {
    console.log('finished!');
  };

  const onChange = (val: countdownValueType) => {
    if (4.95 * 1000 < val && val < 5 * 1000) {
      console.log('changed!');
    }
  };

  return (
    <Row gutter={16}>
      <Col span={12}>
        <Countdown title="Countdown" value={deadline} onFinish={onFinish} />
      </Col>
      <Col span={12}>
        <Countdown title="Million Seconds" value={deadline} format="HH:mm:ss:SSS" />
      </Col>
      <Col span={24} style={{ marginTop: 32 }}>
        <Countdown title="Day Level" value={deadline} format="D 天 H 时 m 分 s 秒" />
      </Col>
      <Col span={12}>
        <Countdown title="Countdown" value={Date.now() + 10 * 1000} onChange={onChange} />
      </Col>
    </Row>
  );
};

export default App;
```
