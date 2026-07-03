---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "radio"
component: "Radio"
component_code_name: "Radio"
recommended_import: "import { Radio } from 'antd';"
subtitle_zh: "单选框"
type_zh: "数据录入"
demo_count: 9
---

# Radio 单选框

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Radio`
- 推荐导入：`import { Radio } from 'antd';`
- 组件分类：数据录入
- 简述：单选框。

## 示例索引

- 基本（basic）
- 不可用（disabled）
- 单选组合（radiogroup）
- Radio.Group 垂直（radiogroup-more）
- Radio.Group 组合 - 配置方式（radiogroup-options）
- 按钮样式（radiobutton）
- 单选组合 - 配合 name 使用（radiogroup-with-name）
- 大小（size）
- 填底的按钮样式（radiobutton-solid）

## 使用文档

单选框。

### 何时使用

- 用于在多个备选项中选中单个状态。
- 和 Select 的区别是，Radio 所有选项默认可见，方便用户在比较中选择，因此选项不宜过多。

### API

#### Radio/Radio.Button

| 参数           | 说明                              | 类型    | 默认值 |
| -------------- | --------------------------------- | ------- | ------ |
| autoFocus      | 自动获取焦点                      | boolean | false  |
| checked        | 指定当前是否选中                  | boolean | false  |
| defaultChecked | 初始是否选中                      | boolean | false  |
| disabled       | 禁用 Radio                        | boolean | false  |
| value          | 根据 value 进行比较，判断是否选中 | any     | -      |

#### RadioGroup

单选框组合，用于包裹一组 `Radio`。

| 参数 | 说明 | 类型 | 默认值 | 版本 |  |
| --- | --- | --- | --- | --- | --- |
| buttonStyle | RadioButton 的风格样式，目前有描边和填色两种风格 | `outline` \| `solid` | `outline` |  |  |
| defaultValue | 默认选中的值 | any | - |  |  |
| disabled | 禁选所有子单选器 | boolean | false |  |  |
| name | RadioGroup 下所有 `input[type="radio"]` 的 `name` 属性 | string | - |  |  |
| options | 以配置形式设置子元素 | string\[] \| number\[] \| Array&lt;{ label: string value: string disabled?: boolean }> | - |  |  |
| optionType | 用于设置 Radio `options` 类型 | `default` \| `button` | `default` | 4.4.0 |  |
| size | 大小，只对按钮样式生效 | `large` \| `middle` \| `small` | - |  |  |
| value | 用于设置当前选中的值 | any | - |  |  |
| onChange | 选项变化时的回调函数 | function(e:Event) | - |  |  |

### 方法

#### Radio

| 名称    | 描述     |
| ------- | -------- |
| blur()  | 移除焦点 |
| focus() | 获取焦点 |

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```tsx
import { Radio } from 'antd';
import React from 'react';

const App: React.FC = () => <Radio>Radio</Radio>;

export default App;
```

### 不可用

- demo: `disabled`

Radio 不可用。

```tsx
import { Button, Radio } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [disabled, setDisabled] = useState(true);

  const toggleDisabled = () => {
    setDisabled(!disabled);
  };

  return (
    <>
      <Radio defaultChecked={false} disabled={disabled}>
        Disabled
      </Radio>
      <Radio defaultChecked disabled={disabled}>
        Disabled
      </Radio>
      <br />
      <Button type="primary" onClick={toggleDisabled} style={{ marginTop: 16 }}>
        Toggle disabled
      </Button>
    </>
  );
};

export default App;
```

### 单选组合

- demo: `radiogroup`

一组互斥的 Radio 配合使用。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Radio } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [value, setValue] = useState(1);

  const onChange = (e: RadioChangeEvent) => {
    console.log('radio checked', e.target.value);
    setValue(e.target.value);
  };

  return (
    <Radio.Group onChange={onChange} value={value}>
      <Radio value={1}>A</Radio>
      <Radio value={2}>B</Radio>
      <Radio value={3}>C</Radio>
      <Radio value={4}>D</Radio>
    </Radio.Group>
  );
};

export default App;
```

### Radio.Group 垂直

- demo: `radiogroup-more`

垂直的 Radio.Group，配合更多输入框选项。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Input, Radio, Space } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [value, setValue] = useState(1);

  const onChange = (e: RadioChangeEvent) => {
    console.log('radio checked', e.target.value);
    setValue(e.target.value);
  };

  return (
    <Radio.Group onChange={onChange} value={value}>
      <Space direction="vertical">
        <Radio value={1}>Option A</Radio>
        <Radio value={2}>Option B</Radio>
        <Radio value={3}>Option C</Radio>
        <Radio value={4}>
          More...
          {value === 4 ? <Input style={{ width: 100, marginLeft: 10 }} /> : null}
        </Radio>
      </Space>
    </Radio.Group>
  );
};

export default App;
```

### Radio.Group 组合 - 配置方式

- demo: `radiogroup-options`

通过配置 `options` 参数来渲染单选框。也可通过 `optionType` 参数来设置 Radio 类型。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Radio } from 'antd';
import React, { useState } from 'react';

const plainOptions = ['Apple', 'Pear', 'Orange'];
const options = [
  { label: 'Apple', value: 'Apple' },
  { label: 'Pear', value: 'Pear' },
  { label: 'Orange', value: 'Orange' },
];
const optionsWithDisabled = [
  { label: 'Apple', value: 'Apple' },
  { label: 'Pear', value: 'Pear' },
  { label: 'Orange', value: 'Orange', disabled: true },
];

const App: React.FC = () => {
  const [value1, setValue1] = useState('Apple');
  const [value2, setValue2] = useState('Apple');
  const [value3, setValue3] = useState('Apple');
  const [value4, setValue4] = useState('Apple');

  const onChange1 = ({ target: { value } }: RadioChangeEvent) => {
    console.log('radio1 checked', value);
    setValue1(value);
  };

  const onChange2 = ({ target: { value } }: RadioChangeEvent) => {
    console.log('radio2 checked', value);
    setValue2(value);
  };

  const onChange3 = ({ target: { value } }: RadioChangeEvent) => {
    console.log('radio3 checked', value);
    setValue3(value);
  };

  const onChange4 = ({ target: { value } }: RadioChangeEvent) => {
    console.log('radio4 checked', value);
    setValue4(value);
  };

  return (
    <>
      <Radio.Group options={plainOptions} onChange={onChange1} value={value1} />
      <br />
      <Radio.Group options={optionsWithDisabled} onChange={onChange2} value={value2} />
      <br />
      <br />
      <Radio.Group options={options} onChange={onChange3} value={value3} optionType="button" />
      <br />
      <br />
      <Radio.Group
        options={optionsWithDisabled}
        onChange={onChange4}
        value={value4}
        optionType="button"
        buttonStyle="solid"
      />
    </>
  );
};

export default App;
```

### 按钮样式

- demo: `radiobutton`

按钮样式的单选组合。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Radio } from 'antd';
import React from 'react';

const onChange = (e: RadioChangeEvent) => {
  console.log(`radio checked:${e.target.value}`);
};

const App: React.FC = () => (
  <>
    <Radio.Group onChange={onChange} defaultValue="a">
      <Radio.Button value="a">Hangzhou</Radio.Button>
      <Radio.Button value="b">Shanghai</Radio.Button>
      <Radio.Button value="c">Beijing</Radio.Button>
      <Radio.Button value="d">Chengdu</Radio.Button>
    </Radio.Group>
    <Radio.Group onChange={onChange} defaultValue="a" style={{ marginTop: 16 }}>
      <Radio.Button value="a">Hangzhou</Radio.Button>
      <Radio.Button value="b" disabled>
        Shanghai
      </Radio.Button>
      <Radio.Button value="c">Beijing</Radio.Button>
      <Radio.Button value="d">Chengdu</Radio.Button>
    </Radio.Group>
    <Radio.Group disabled onChange={onChange} defaultValue="a" style={{ marginTop: 16 }}>
      <Radio.Button value="a">Hangzhou</Radio.Button>
      <Radio.Button value="b">Shanghai</Radio.Button>
      <Radio.Button value="c">Beijing</Radio.Button>
      <Radio.Button value="d">Chengdu</Radio.Button>
    </Radio.Group>
  </>
);

export default App;
```

### 单选组合 - 配合 name 使用

- demo: `radiogroup-with-name`

可以为 Radio.Group 配置 `name` 参数，为组合内的 input 元素赋予相同的 `name` 属性，使浏览器把 Radio.Group 下的 Radio 真正看作是一组（例如可以通过方向键始终**在同一组内**更改选项）。

```tsx
import { Radio } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Radio.Group name="radiogroup" defaultValue={1}>
    <Radio value={1}>A</Radio>
    <Radio value={2}>B</Radio>
    <Radio value={3}>C</Radio>
    <Radio value={4}>D</Radio>
  </Radio.Group>
);

export default App;
```

### 大小

- demo: `size`

大中小三种组合，可以和表单输入框进行对应配合。

```tsx
import { Radio } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Radio.Group defaultValue="a" size="large">
      <Radio.Button value="a">Hangzhou</Radio.Button>
      <Radio.Button value="b">Shanghai</Radio.Button>
      <Radio.Button value="c">Beijing</Radio.Button>
      <Radio.Button value="d">Chengdu</Radio.Button>
    </Radio.Group>
    <Radio.Group defaultValue="a" style={{ marginTop: 16 }}>
      <Radio.Button value="a">Hangzhou</Radio.Button>
      <Radio.Button value="b">Shanghai</Radio.Button>
      <Radio.Button value="c">Beijing</Radio.Button>
      <Radio.Button value="d">Chengdu</Radio.Button>
    </Radio.Group>
    <Radio.Group defaultValue="a" size="small" style={{ marginTop: 16 }}>
      <Radio.Button value="a">Hangzhou</Radio.Button>
      <Radio.Button value="b">Shanghai</Radio.Button>
      <Radio.Button value="c">Beijing</Radio.Button>
      <Radio.Button value="d">Chengdu</Radio.Button>
    </Radio.Group>
  </>
);

export default App;
```

### 填底的按钮样式

- demo: `radiobutton-solid`

实色填底的单选按钮样式。

```tsx
import { Radio } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Radio.Group defaultValue="a" buttonStyle="solid">
      <Radio.Button value="a">Hangzhou</Radio.Button>
      <Radio.Button value="b">Shanghai</Radio.Button>
      <Radio.Button value="c">Beijing</Radio.Button>
      <Radio.Button value="d">Chengdu</Radio.Button>
    </Radio.Group>
    <Radio.Group defaultValue="c" buttonStyle="solid" style={{ marginTop: 16 }}>
      <Radio.Button value="a">Hangzhou</Radio.Button>
      <Radio.Button value="b" disabled>
        Shanghai
      </Radio.Button>
      <Radio.Button value="c">Beijing</Radio.Button>
      <Radio.Button value="d">Chengdu</Radio.Button>
    </Radio.Group>
  </>
);

export default App;
```
