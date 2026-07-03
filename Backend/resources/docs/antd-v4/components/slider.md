---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "slider"
component: "Slider"
component_code_name: "Slider"
recommended_import: "import { Slider } from 'antd';"
subtitle_zh: "滑动输入条"
type_zh: "数据录入"
demo_count: 10
---

# Slider 滑动输入条

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Slider`
- 推荐导入：`import { Slider } from 'antd';`
- 组件分类：数据录入
- 简述：滑动型输入器，展示当前值和可选范围。

## 示例索引

- 基本（basic）
- 带输入框的滑块（input-number）
- 带 icon 的滑块（icon-slider）
- 自定义提示（tip-formatter）
- 事件（event）
- 带标签的滑块（mark）
- 垂直（vertical）
- 控制 ToolTip 的显示（show-tooltip）
- 反向（reverse）
- 范围可拖拽（dragableTrack）

## 使用文档

滑动型输入器，展示当前值和可选范围。

### 何时使用

当用户需要在数值区间/自定义区间内进行选择时，可为连续或离散值。

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| allowClear | 支持清除, 单选模式有效 | boolean | false |  |
| defaultValue | 设置初始取值。当 `range` 为 false 时，使用 number，否则用 \[number, number] | number \| \[number, number] | 0 \| \[0, 0] |  |
| disabled | 值为 true 时，滑块为禁用状态 | boolean | false |  |
| dots | 是否只能拖拽到刻度上 | boolean | false |  |
| getTooltipPopupContainer | Tooltip 渲染父节点，默认渲染到 body 上 | (triggerNode) => HTMLElement | () => document.body |  |
| included | `marks` 不为空对象时有效，值为 true 时表示值为包含关系，false 表示并列 | boolean | true |  |
| marks | 刻度标记，key 的类型必须为 `number` 且取值在闭区间 \[min, max] 内，每个标签可以单独设置样式 | object | { number: ReactNode } or { number: { style: CSSProperties, label: ReactNode } } |  |
| max | 最大值 | number | 100 |  |
| min | 最小值 | number | 0 |  |
| range | 双滑块模式 | boolean \| range | false |  |
| reverse | 反向坐标轴 | boolean | false |  |
| step | 步长，取值必须大于 0，并且可被 (max - min) 整除。当 `marks` 不为空对象时，可以设置 `step` 为 null，此时 Slider 的可选值仅有 marks 标出来的部分 | number \| null | 1 |  |
| tooltip | 设置 Tooltip 相关属性 | tooltip | - | 4.23.0 |
| value | 设置当前取值。当 `range` 为 false 时，使用 number，否则用 \[number, number] | number \| \[number, number] | - |  |
| vertical | 值为 true 时，Slider 为垂直方向 | boolean | false |  |
| onAfterChange | 与 `onmouseup` 触发时机一致，把当前值作为参数传入 | (value) => void | - |  |
| onChange | 当 Slider 的值发生改变时，会触发 onChange 事件，并把改变后的值作为参数传入 | (value) => void | - |  |

#### range

| 参数           | 说明                 | 类型    | 默认值 | 版本   |
| -------------- | -------------------- | ------- | ------ | ------ |
| draggableTrack | 范围刻度是否可被拖拽 | boolean | false  | 4.10.0 |

#### tooltip

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| open | 值为 true 时，Tooltip 将会始终显示；否则始终不显示，哪怕在拖拽及移入时 | boolean | - | 4.23.0 |
| placement | 设置 Tooltip 展示位置。参考 Tooltip | string | - | 4.23.0 |
| getPopupContainer | Tooltip 渲染父节点，默认渲染到 body 上 | (triggerNode) => HTMLElement | () => document.body | 4.23.0 |
| formatter | Slider 会把当前值传给 `formatter`，并在 Tooltip 中显示 `formatter` 的返回值，若为 null，则隐藏 Tooltip | value => ReactNode \| null | IDENTITY | 4.23.0 |

### 方法

| 名称    | 描述     | 版本 |
| ------- | -------- | ---- |
| blur()  | 移除焦点 |      |
| focus() | 获取焦点 |      |

## 示例代码

### 基本

- demo: `basic`

基本滑动条。当 `range` 为 `true` 时，渲染为双滑块。当 `disabled` 为 `true` 时，滑块处于不可用状态。

```tsx
import { Slider, Switch } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [disabled, setDisabled] = useState(false);

  const onChange = (checked: boolean) => {
    setDisabled(checked);
  };

  return (
    <>
      <Slider defaultValue={30} disabled={disabled} />
      <Slider range defaultValue={[20, 50]} disabled={disabled} />
      Disabled: <Switch size="small" checked={disabled} onChange={onChange} />
    </>
  );
};

export default App;
```

### 带输入框的滑块

- demo: `input-number`

和 数字输入框 组件保持同步。

```tsx
import { Col, InputNumber, Row, Slider } from 'antd';
import React, { useState } from 'react';

const IntegerStep = () => {
  const [inputValue, setInputValue] = useState(1);

  const onChange = (newValue: number) => {
    setInputValue(newValue);
  };

  return (
    <Row>
      <Col span={12}>
        <Slider
          min={1}
          max={20}
          onChange={onChange}
          value={typeof inputValue === 'number' ? inputValue : 0}
        />
      </Col>
      <Col span={4}>
        <InputNumber
          min={1}
          max={20}
          style={{ margin: '0 16px' }}
          value={inputValue}
          onChange={onChange}
        />
      </Col>
    </Row>
  );
};

const DecimalStep = () => {
  const [inputValue, setInputValue] = useState(0);

  const onChange = (value: number) => {
    if (isNaN(value)) {
      return;
    }

    setInputValue(value);
  };

  return (
    <Row>
      <Col span={12}>
        <Slider
          min={0}
          max={1}
          onChange={onChange}
          value={typeof inputValue === 'number' ? inputValue : 0}
          step={0.01}
        />
      </Col>
      <Col span={4}>
        <InputNumber
          min={0}
          max={1}
          style={{ margin: '0 16px' }}
          step={0.01}
          value={inputValue}
          onChange={onChange}
        />
      </Col>
    </Row>
  );
};

const App: React.FC = () => (
  <div>
    <IntegerStep />
    <DecimalStep />
  </div>
);

export default App;
```

### 带 icon 的滑块

- demo: `icon-slider`

滑块左右可以设置图标来表达业务含义。

```tsx
import { FrownOutlined, SmileOutlined } from '@ant-design/icons';
import { Slider } from 'antd';
import React, { useState } from 'react';

interface IconSliderProps {
  max: number;
  min: number;
}

const IconSlider: React.FC<IconSliderProps> = props => {
  const { max, min } = props;
  const [value, setValue] = useState(0);

  const mid = Number(((max - min) / 2).toFixed(5));
  const preColorCls = value >= mid ? '' : 'icon-wrapper-active';
  const nextColorCls = value >= mid ? 'icon-wrapper-active' : '';

  return (
    <div className="icon-wrapper">
      <FrownOutlined className={preColorCls} />
      <Slider {...props} onChange={setValue} value={value} />
      <SmileOutlined className={nextColorCls} />
    </div>
  );
};

const App: React.FC = () => <IconSlider min={0} max={20} />;

export default App;
```

```css
.icon-wrapper {
  position: relative;
  padding: 0px 30px;
}

.icon-wrapper .anticon {
  position: absolute;
  top: -2px;
  width: 16px;
  height: 16px;
  color: rgba(0, 0, 0, 0.25);
  font-size: 16px;
  line-height: 1;
}

.icon-wrapper .icon-wrapper-active {
  color: rgba(0, 0, 0, 0.45);
}

.icon-wrapper .anticon:first-child {
  left: 0;
}

.icon-wrapper .anticon:last-child {
  right: 0;
}
```

### 自定义提示

- demo: `tip-formatter`

使用 `tooltip.formatter` 可以格式化 `Tooltip` 的内容，设置 `tooltip.formatter={null}`，则隐藏 `Tooltip`。

```tsx
import { Slider } from 'antd';
import React from 'react';

const formatter = (value: number) => `${value}%`;

const App: React.FC = () => (
  <>
    <Slider tooltip={{ formatter }} />
    <Slider tooltip={{ formatter: null }} />
  </>
);

export default App;
```

### 事件

- demo: `event`

当 Slider 的值发生改变时，会触发 `onChange` 事件，并把改变后的值作为参数传入。在 `onmouseup` 时，会触发 `onAfterChange` 事件，并把当前值作为参数传入。

```tsx
import { Slider } from 'antd';
import React from 'react';

const onChange = (value: number | [number, number]) => {
  console.log('onChange: ', value);
};

const onAfterChange = (value: number | [number, number]) => {
  console.log('onAfterChange: ', value);
};

const App: React.FC = () => (
  <>
    <Slider defaultValue={30} onChange={onChange} onAfterChange={onAfterChange} />
    <Slider
      range
      step={10}
      defaultValue={[20, 50]}
      onChange={onChange}
      onAfterChange={onAfterChange}
    />
  </>
);

export default App;
```

### 带标签的滑块

- demo: `mark`

使用 `marks` 属性标注分段式滑块，使用 `value` / `defaultValue` 指定滑块位置。当 `included=false` 时，表明不同标记间为并列关系。当 `step=null` 时，Slider 的可选值仅有 `marks` 标出来的部分。

```tsx
import { Slider } from 'antd';
import type { SliderMarks } from 'antd/es/slider';
import React from 'react';

const marks: SliderMarks = {
  0: '0°C',
  26: '26°C',
  37: '37°C',
  100: {
    style: {
      color: '#f50',
    },
    label: <strong>100°C</strong>,
  },
};

const App: React.FC = () => (
  <>
    <h4>included=true</h4>
    <Slider marks={marks} defaultValue={37} />
    <Slider range marks={marks} defaultValue={[26, 37]} />

    <h4>included=false</h4>
    <Slider marks={marks} included={false} defaultValue={37} />

    <h4>marks & step</h4>
    <Slider marks={marks} step={10} defaultValue={37} />

    <h4>step=null</h4>
    <Slider marks={marks} step={null} defaultValue={37} />
  </>
);

export default App;
```

### 垂直

- demo: `vertical`

垂直方向的 Slider。

```tsx
import { Slider } from 'antd';
import type { SliderMarks } from 'antd/es/slider';
import React from 'react';

const style: React.CSSProperties = {
  display: 'inline-block',
  height: 300,
  marginLeft: 70,
};

const marks: SliderMarks = {
  0: '0°C',
  26: '26°C',
  37: '37°C',
  100: {
    style: {
      color: '#f50',
    },
    label: <strong>100°C</strong>,
  },
};

const App: React.FC = () => (
  <>
    <div style={style}>
      <Slider vertical defaultValue={30} />
    </div>
    <div style={style}>
      <Slider vertical range step={10} defaultValue={[20, 50]} />
    </div>
    <div style={style}>
      <Slider vertical range marks={marks} defaultValue={[26, 37]} />
    </div>
  </>
);

export default App;
```

### 控制 ToolTip 的显示

- demo: `show-tooltip`

当 `tooltip.open` 为 `true` 时，将始终显示 ToolTip；反之则始终不显示，即使在拖动、移入时也是如此。

```tsx
import { Slider } from 'antd';
import React from 'react';

const App: React.FC = () => <Slider defaultValue={30} tooltip={{ open: true }} />;

export default App;
```

### 反向

- demo: `reverse`

设置 `reverse` 可以将滑动条置反。

```tsx
import { Slider, Switch } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [reverse, setReverse] = useState(true);

  return (
    <>
      <Slider defaultValue={30} reverse={reverse} />
      <Slider range defaultValue={[20, 50]} reverse={reverse} />
      Reversed: <Switch size="small" checked={reverse} onChange={setReverse} />
    </>
  );
};

export default App;
```

### 范围可拖拽

- demo: `dragableTrack`

可以设置 `range.draggableTrack`，使得范围刻度整体可拖拽。

```tsx
import { Slider } from 'antd';
import React from 'react';

const App: React.FC = () => <Slider range={{ draggableTrack: true }} defaultValue={[20, 50]} />;

export default App;
```
