---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "time-picker"
component: "TimePicker"
component_code_name: "TimePicker"
recommended_import: "import { TimePicker } from 'antd';"
subtitle_zh: "时间选择框"
type_zh: "数据录入"
demo_count: 11
---

# TimePicker 时间选择框

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`TimePicker`
- 推荐导入：`import { TimePicker } from 'antd';`
- 组件分类：数据录入
- 简述：输入或选择时间的控件。

## 示例索引

- 基本（basic）
- 受控组件（value）
- 三种大小（size）
- 禁用（disabled）
- 选择时分（hide-column）
- 步长选项（interval-options）
- 附加内容（addon）
- 12 小时制（12hours）
- 范围选择器（range-picker）
- 无边框（bordered）
- 自定义状态（status）

## 使用文档

输入或选择时间的控件。

### 何时使用

---

当用户需要输入一个时间，可以点击标准输入框，弹出时间面板进行选择。

### API

---

```jsx
import moment from 'moment';
<TimePicker defaultValue={moment('13:30:56', 'HH:mm:ss')} />;
```

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| allowClear | 是否展示清除按钮 | boolean | true |  |
| autoFocus | 自动获取焦点 | boolean | false |  |
| bordered | 是否有边框 | boolean | true |  |
| className | 选择器类名 | string | - |  |
| clearIcon | 自定义的清除图标 | ReactNode | - |  |
| clearText | 清除按钮的提示文案 | string | clear |  |
| defaultValue | 默认时间 | moment | - |  |
| disabled | 禁用全部操作 | boolean | false |  |
| disabledTime | 不可选择的时间 | DisabledTime | - | 4.19.0 |
| format | 展示的时间格式 | string | `HH:mm:ss` |  |
| getPopupContainer | 定义浮层的容器，默认为 body 上新建 div | function(trigger) | - |  |
| hideDisabledOptions | 隐藏禁止选择的选项 | boolean | false |  |
| hourStep | 小时选项间隔 | number | 1 |  |
| inputReadOnly | 设置输入框为只读（避免在移动设备上打开虚拟键盘） | boolean | false |  |
| minuteStep | 分钟选项间隔 | number | 1 |  |
| open | 面板是否打开 | boolean | false |  |
| placeholder | 没有值的时候显示的内容 | string \| \[string, string] | `请选择时间` |  |
| placement | 选择框弹出的位置 | `bottomLeft` `bottomRight` `topLeft` `topRight` | bottomLeft |  |
| popupClassName | 弹出层类名 | string | - |  |
| popupStyle | 弹出层样式对象 | object | - |  |
| renderExtraFooter | 选择框底部显示自定义的内容 | () => ReactNode | - |  |
| secondStep | 秒选项间隔 | number | 1 |  |
| showNow | 面板是否显示“此刻”按钮 | boolean | - | 4.4.0 |
| status | 设置校验状态 | 'error' \| 'warning' | - | 4.19.0 |
| suffixIcon | 自定义的选择框后缀图标 | ReactNode | - |  |
| use12Hours | 使用 12 小时制，为 true 时 `format` 默认为 `h:mm:ss a` | boolean | false |  |
| value | 当前时间 | moment | - |  |
| onChange | 时间发生变化的回调 | function(time: moment, timeString: string): void | - |  |
| onOpenChange | 面板打开/关闭时的回调 | (open: boolean) => void | - |  |

##### DisabledTime

```typescript
type DisabledTime = (now: Moment) => {
  disabledHours?: () => number[];
  disabledMinutes?: (selectedHour: number) => number[];
  disabledSeconds?: (selectedHour: number, selectedMinute: number) => number[];
};
```

### 方法

| 名称    | 描述     | 版本 |
| ------- | -------- | ---- |
| blur()  | 移除焦点 |      |
| focus() | 获取焦点 |      |

### RangePicker

属性与 DatePicker 的 RangePicker 相同。还包含以下属性：

| 参数         | 说明                 | 类型                                    | 默认值 | 版本   |
| ------------ | -------------------- | --------------------------------------- | ------ | ------ |
| disabledTime | 不可选择的时间       | RangeDisabledTime | -      | 4.19.0 |
| order        | 始末时间是否自动排序 | boolean                                 | true   | 4.1.0  |

#### RangeDisabledTime

```typescript
type RangeDisabledTime = (
  now: Moment,
  type = 'start' | 'end',
) => {
  disabledHours?: () => number[];
  disabledMinutes?: (selectedHour: number) => number[];
  disabledSeconds?: (selectedHour: number, selectedMinute: number) => number[];
};
```

### FAQ

- 如何在 TimePicker 中使用自定义日期库（如 dayjs ）

## 示例代码

### 基本

- demo: `basic`

点击 TimePicker，然后可以在浮层中选择或者输入某一时间。

```tsx
import { TimePicker } from 'antd';
import type { Moment } from 'moment';
import moment from 'moment';
import React from 'react';

const onChange = (time: Moment, timeString: string) => {
  console.log(time, timeString);
};

const App: React.FC = () => (
  <TimePicker onChange={onChange} defaultOpenValue={moment('00:00:00', 'HH:mm:ss')} />
);

export default App;
```

### 受控组件

- demo: `value`

value 和 onChange 需要配合使用。

```tsx
import { TimePicker } from 'antd';
import type { Moment } from 'moment';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [value, setValue] = useState<Moment | null>(null);

  const onChange = (time: Moment) => {
    setValue(time);
  };

  return <TimePicker value={value} onChange={onChange} />;
};

export default App;
```

### 三种大小

- demo: `size`

三种大小的输入框，大的用在表单中，中的为默认。

```tsx
import { TimePicker } from 'antd';
import moment from 'moment';
import React from 'react';

const App: React.FC = () => (
  <>
    <TimePicker defaultValue={moment('12:08:23', 'HH:mm:ss')} size="large" />
    <TimePicker defaultValue={moment('12:08:23', 'HH:mm:ss')} />
    <TimePicker defaultValue={moment('12:08:23', 'HH:mm:ss')} size="small" />
  </>
);

export default App;
```

### 禁用

- demo: `disabled`

禁用时间选择。

```tsx
import { TimePicker } from 'antd';
import moment from 'moment';
import React from 'react';

const App: React.FC = () => <TimePicker defaultValue={moment('12:08:23', 'HH:mm:ss')} disabled />;

export default App;
```

### 选择时分

- demo: `hide-column`

TimePicker 浮层中的列会随着 `format` 变化，当略去 `format` 中的某部分时，浮层中对应的列也会消失。

```tsx
import { TimePicker } from 'antd';
import moment from 'moment';
import React from 'react';

const format = 'HH:mm';

const App: React.FC = () => <TimePicker defaultValue={moment('12:08', format)} format={format} />;

export default App;
```

### 步长选项

- demo: `interval-options`

可以使用 `hourStep` `minuteStep` `secondStep` 按步长展示可选的时分秒。

```tsx
import { TimePicker } from 'antd';
import React from 'react';

const App: React.FC = () => <TimePicker minuteStep={15} secondStep={10} />;

export default App;
```

### 附加内容

- demo: `addon`

在 TimePicker 选择框底部显示自定义的内容。

```tsx
import { Button, TimePicker } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [open, setOpen] = useState(false);

  return (
    <TimePicker
      open={open}
      onOpenChange={setOpen}
      renderExtraFooter={() => (
        <Button size="small" type="primary" onClick={() => setOpen(false)}>
          OK
        </Button>
      )}
    />
  );
};

export default App;
```

### 12 小时制

- demo: `12hours`

12 小时制的时间选择器，默认的 format 为 `h:mm:ss a`。

```tsx
import { TimePicker } from 'antd';
import type { Moment } from 'moment';
import React from 'react';

const onChange = (time: Moment, timeString: string) => {
  console.log(time, timeString);
};

const App: React.FC = () => (
  <>
    <TimePicker use12Hours onChange={onChange} />
    <TimePicker use12Hours format="h:mm:ss A" onChange={onChange} style={{ width: 140 }} />
    <TimePicker use12Hours format="h:mm a" onChange={onChange} />
  </>
);

export default App;
```

### 范围选择器

- demo: `range-picker`

通过 `TimePicker.RangePicker` 使用时间范围选择器。

```tsx
import { TimePicker } from 'antd';
import React from 'react';

const App: React.FC = () => <TimePicker.RangePicker />;

export default App;
```

### 无边框

- demo: `bordered`

无边框样式。

```tsx
import { TimePicker } from 'antd';
import React from 'react';

const { RangePicker } = TimePicker;

const App: React.FC = () => (
  <>
    <TimePicker bordered={false} />
    <RangePicker bordered={false} />
  </>
);

export default App;
```

### 自定义状态

- demo: `status`

使用 `status` 为 TimePicker 添加状态，可选 `error` 或者 `warning`。

```tsx
import { Space, TimePicker } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Space direction="vertical">
    <TimePicker status="error" />
    <TimePicker status="warning" />
    <TimePicker.RangePicker status="error" />
    <TimePicker.RangePicker status="warning" />
  </Space>
);

export default App;
```
