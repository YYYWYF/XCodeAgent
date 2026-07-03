---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "input-number"
component: "InputNumber"
component_code_name: "InputNumber"
recommended_import: "import { InputNumber } from 'antd';"
subtitle_zh: "数字输入框"
type_zh: "数据录入"
demo_count: 11
---

# InputNumber 数字输入框

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`InputNumber`
- 推荐导入：`import { InputNumber } from 'antd';`
- 组件分类：数据录入
- 简述：通过鼠标或键盘，输入范围内的数值。

## 示例索引

- 基本（basic）
- 三种大小（size）
- 前置/后置标签（addon）
- 不可用（disabled）
- 高精度小数（digit）
- 格式化展示（formatter）
- 键盘行为（keyboard）
- 无边框（borderless）
- 超出边界（out-of-range）
- 前缀（prefix）
- 自定义状态（status）

## 使用文档

通过鼠标或键盘，输入范围内的数值。

### 何时使用

当需要获取标准数值时。

### API

属性如下

| 成员 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| addonAfter | 带标签的 input，设置后置标签 | ReactNode | - | 4.17.0 |
| addonBefore | 带标签的 input，设置前置标签 | ReactNode | - | 4.17.0 |
| autoFocus | 自动获取焦点 | boolean | false | - |
| bordered | 是否有边框 | boolean | true | 4.12.0 |
| controls | 是否显示增减按钮，也可设置自定义箭头图标 | boolean \| { upIcon?: React.ReactNode; downIcon?: React.ReactNode; } | - | 4.19.0 |
| decimalSeparator | 小数点 | string | - | - |
| defaultValue | 初始值 | number | - | - |
| disabled | 禁用 | boolean | false | - |
| formatter | 指定输入框展示值的格式 | function(value: number \| string, info: { userTyping: boolean, input: string }): string | - | info: 4.17.0 |
| keyboard | 是否启用键盘快捷行为 | boolean | true | 4.12.0 |
| max | 最大值 | number | Number.MAX_SAFE_INTEGER | - |
| min | 最小值 | number | Number.MIN_SAFE_INTEGER | - |
| parser | 指定从 `formatter` 里转换回数字的方式，和 `formatter` 搭配使用 | function(string): number | - | - |
| precision | 数值精度，配置 `formatter` 时会以 `formatter` 为准 | number | - | - |
| readOnly | 只读 | boolean | false | - |
| status | 设置校验状态 | 'error' \| 'warning' | - | 4.19.0 |
| prefix | 带有前缀图标的 input | ReactNode | - | 4.17.0 |
| size | 输入框大小 | `large` \| `middle` \| `small` | - | - |
| step | 每次改变步数，可以为小数 | number \| string | 1 | - |
| stringMode | 字符值模式，开启后支持高精度小数。同时 `onChange` 将返回 string 类型 | boolean | false | 4.13.0 |
| value | 当前值 | number | - | - |
| onChange | 变化回调 | function(value: number \| string \| null) | - | - |
| onPressEnter | 按下回车的回调 | function(e) | - | - |
| onStep | 点击上下箭头的回调 | (value: number, info: { offset: number, type: 'up' \| 'down' }) => void | - | 4.7.0 |

### 方法

| 名称    | 描述     |
| ------- | -------- |
| blur()  | 移除焦点 |
| focus() | 获取焦点 |

### FAQ

#### 为何受控模式下，`value` 可以超出 `min` 和 `max` 范围？

在受控模式下，开发者可能自行存储相关数据。如果组件将数据约束回范围内，会导致展示数据与实际存储数据不一致的情况。这使得一些如表单场景存在潜在的数据问题。

#### 为何动态修改 `min` 和 `max` 让 `value` 超出范围不会触发 `onChange` 事件？

`onChange` 事件为用户触发事件，自行触发会导致表单库误以为变更来自用户操作。我们以错误样式展示超出范围的数值。

## 示例代码

### 基本

- demo: `basic`

数字输入框。

```tsx
import { InputNumber } from 'antd';
import React from 'react';

const onChange = (value: number) => {
  console.log('changed', value);
};

const App: React.FC = () => <InputNumber min={1} max={10} defaultValue={3} onChange={onChange} />;

export default App;
```

### 三种大小

- demo: `size`

三种大小的数字输入框，当 size 分别为 `large` 和 `small` 时，输入框高度为 `40px` 和 `24px` ，默认高度为 `32px`。

```tsx
import { InputNumber, Space } from 'antd';
import React from 'react';

const onChange = (value: number) => {
  console.log('changed', value);
};

const App: React.FC = () => (
  <Space>
    <InputNumber size="large" min={1} max={100000} defaultValue={3} onChange={onChange} />
    <InputNumber min={1} max={100000} defaultValue={3} onChange={onChange} />
    <InputNumber size="small" min={1} max={100000} defaultValue={3} onChange={onChange} />
  </Space>
);

export default App;
```

### 前置/后置标签

- demo: `addon`

用于配置一些固定组合。

```tsx
import { SettingOutlined } from '@ant-design/icons';
import { Cascader, InputNumber, Select, Space } from 'antd';
import React from 'react';

const { Option } = Select;

const selectBefore = (
  <Select defaultValue="add" style={{ width: 60 }}>
    <Option value="add">+</Option>
    <Option value="minus">-</Option>
  </Select>
);
const selectAfter = (
  <Select defaultValue="USD" style={{ width: 60 }}>
    <Option value="USD">$</Option>
    <Option value="EUR">€</Option>
    <Option value="GBP">£</Option>
    <Option value="CNY">¥</Option>
  </Select>
);

const App: React.FC = () => (
  <Space direction="vertical">
    <InputNumber addonBefore="+" addonAfter="$" defaultValue={100} />
    <InputNumber addonBefore={selectBefore} addonAfter={selectAfter} defaultValue={100} />
    <InputNumber addonAfter={<SettingOutlined />} defaultValue={100} />
    <InputNumber
      addonBefore={<Cascader placeholder="cascader" style={{ width: 150 }} />}
      defaultValue={100}
    />
  </Space>
);

export default App;
```

### 不可用

- demo: `disabled`

点击按钮切换可用状态。

```tsx
import { Button, InputNumber } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [disabled, setDisabled] = useState(true);

  const toggle = () => {
    setDisabled(!disabled);
  };

  return (
    <>
      <InputNumber min={1} max={10} disabled={disabled} defaultValue={3} />
      <div style={{ marginTop: 20 }}>
        <Button onClick={toggle} type="primary">
          Toggle disabled
        </Button>
      </div>
    </>
  );
};

export default App;
```

### 高精度小数

- demo: `digit`

通过 `stringMode` 开启高精度小数支持，`onChange` 事件将返回 string 类型。对于旧版浏览器，你需要 BigInt polyfill。

```tsx
import { InputNumber } from 'antd';
import React from 'react';

const onChange = (value: string) => {
  console.log('changed', value);
};

const App: React.FC = () => (
  <InputNumber<string>
    style={{ width: 200 }}
    defaultValue="1"
    min="0"
    max="10"
    step="0.00000000000001"
    onChange={onChange}
    stringMode
  />
);

export default App;
```

### 格式化展示

- demo: `formatter`

通过 `formatter` 格式化数字，以展示具有具体含义的数据，往往需要配合 `parser` 一起使用。

> 这里有一个更复杂的货币格式化输入框：

```tsx
import { InputNumber, Space } from 'antd';
import React from 'react';

const onChange = (value: number | string) => {
  console.log('changed', value);
};

const App: React.FC = () => (
  <Space>
    <InputNumber
      defaultValue={1000}
      formatter={value => `$ ${value}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
      parser={value => value!.replace(/\$\s?|(,*)/g, '')}
      onChange={onChange}
    />
    <InputNumber
      defaultValue={100}
      min={0}
      max={100}
      formatter={value => `${value}%`}
      parser={value => value!.replace('%', '')}
      onChange={onChange}
    />
  </Space>
);

export default App;
```

### 键盘行为

- demo: `keyboard`

使用 `keyboard` 属性可以控制键盘行为。

```tsx
import { Checkbox, InputNumber, Space } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [keyboard, setKeyboard] = useState(true);

  return (
    <Space>
      <InputNumber min={1} max={10} keyboard={keyboard} defaultValue={3} />
      <Checkbox
        onChange={() => {
          setKeyboard(!keyboard);
        }}
        checked={keyboard}
      >
        Toggle keyboard
      </Checkbox>
    </Space>
  );
};

export default App;
```

### 无边框

- demo: `borderless`

没有边框。

```tsx
import { InputNumber } from 'antd';
import React from 'react';

const App: React.FC = () => <InputNumber min={1} max={10} defaultValue={3} bordered={false} />;

export default App;
```

### 超出边界

- demo: `out-of-range`

当通过受控将 `value` 超出边界时，提供警告样式。

```tsx
import { Button, InputNumber, Space } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [value, setValue] = useState<string | number | null>('99');

  return (
    <Space>
      <InputNumber min={1} max={10} value={value} onChange={setValue} />
      <Button
        type="primary"
        onClick={() => {
          setValue(99);
        }}
      >
        Reset
      </Button>
    </Space>
  );
};

export default App;
```

### 前缀

- demo: `prefix`

在输入框上添加前缀图标。

```tsx
import { UserOutlined } from '@ant-design/icons';
import { InputNumber } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <InputNumber prefix="￥" style={{ width: '100%' }} />
    <br />
    <br />
    <InputNumber addonBefore={<UserOutlined />} prefix="￥" style={{ width: '100%' }} />
    <br />
    <br />
    <InputNumber prefix="￥" disabled style={{ width: '100%' }} />
  </>
);

export default App;
```

### 自定义状态

- demo: `status`

使用 `status` 为 InputNumber 添加状态，可选 `error` 或者 `warning`。

```tsx
import ClockCircleOutlined from '@ant-design/icons/ClockCircleOutlined';
import { InputNumber, Space } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Space direction="vertical" style={{ width: '100%' }}>
    <InputNumber status="error" style={{ width: '100%' }} />
    <InputNumber status="warning" style={{ width: '100%' }} />
    <InputNumber status="error" style={{ width: '100%' }} prefix={<ClockCircleOutlined />} />
    <InputNumber status="warning" style={{ width: '100%' }} prefix={<ClockCircleOutlined />} />
  </Space>
);

export default App;
```
