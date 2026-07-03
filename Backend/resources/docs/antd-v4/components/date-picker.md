---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "date-picker"
component: "DatePicker"
component_code_name: "DatePicker"
recommended_import: "import { DatePicker } from 'antd';"
subtitle_zh: "日期选择框"
type_zh: "数据录入"
demo_count: 15
---

# DatePicker 日期选择框

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`DatePicker`
- 推荐导入：`import { DatePicker } from 'antd';`
- 组件分类：数据录入
- 简述：输入或选择日期的控件。

## 示例索引

- 基本（basic）
- 范围选择器（range-picker）
- 切换不同的选择器（switchable）
- 日期格式（format）
- 日期时间选择（time）
- 禁用（disabled）
- 不可选择日期和时间（disabled-date）
- 选择不超过七天的范围（select-in-range）
- 预设范围（presetted-ranges）
- 额外的页脚（extra-footer）
- 三种大小（size）
- 定制日期单元格（date-render）
- 自定义状态（status）
- 无边框（bordered）
- 弹出位置（placement）

## 使用文档

输入或选择日期的控件。

### 何时使用

当用户需要输入一个日期，可以点击标准输入框，弹出日期面板进行选择。

### API

日期类组件包括以下五种形式。

- DatePicker
- DatePicker\[picker="month"]
- DatePicker\[picker="week"]
- DatePicker\[picker="year"]
- DatePicker\[picker="quarter"] (4.1.0 新增)
- RangePicker

#### 国际化配置

默认配置为 en-US，如果你需要设置其他语言，推荐在入口处使用我们提供的国际化组件，详见：ConfigProvider 国际化。

如有特殊需求（仅修改单一组件的语言），请使用 locale 参数，参考：默认配置。

```jsx
import 'moment/locale/zh-cn';
import locale from 'antd/es/date-picker/locale/zh_CN';

<DatePicker locale={locale} />;
```

```jsx
// 默认语言为 en-US，如果你需要设置其他语言，推荐在入口文件全局设置 locale
import moment from 'moment';
import 'moment/locale/zh-cn';
import locale from 'antd/es/locale/zh_CN';

<ConfigProvider locale={locale}>
  <DatePicker defaultValue={moment('2015-01-01', 'YYYY-MM-DD')} />
</ConfigProvider>;
```

#### 共同的 API

以下 API 为 DatePicker、 RangePicker 共享的 API。

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| allowClear | 是否显示清除按钮 | boolean | true |  |
| autoFocus | 自动获取焦点 | boolean | false |  |
| bordered | 是否有边框 | boolean | true |  |
| className | 选择器 className | string | - |  |
| dateRender | 自定义日期单元格的内容 | function(currentDate: moment, today: moment) => React.ReactNode | - |  |
| disabled | 禁用 | boolean | false |  |
| disabledDate | 不可选择的日期 | (currentDate: moment) => boolean | - |  |
| popupClassName | 额外的弹出日历 className | string | - | 4.23.0 |
| getPopupContainer | 定义浮层的容器，默认为 body 上新建 div | function(trigger) | - |  |
| inputReadOnly | 设置输入框为只读（避免在移动设备上打开虚拟键盘） | boolean | false |  |
| locale | 国际化配置 | object | 默认配置 |  |
| mode | 日期面板的状态（设置后无法选择年份/月份？） | `time` \| `date` \| `month` \| `year` \| `decade` | - |  |
| nextIcon | 自定义下一个图标 | ReactNode | - | 4.17.0 |
| open | 控制弹层是否展开 | boolean | - |  |
| panelRender | 自定义渲染面板 | (panelNode) => ReactNode | - | 4.5.0 |
| picker | 设置选择器类型 | `date` \| `week` \| `month` \| `quarter` \| `year` | `date` | `quarter`: 4.1.0 |
| placeholder | 输入框提示文字 | string \| \[string, string] | - |  |
| placement | 选择框弹出的位置 | `bottomLeft` `bottomRight` `topLeft` `topRight` | bottomLeft |  |
| popupStyle | 额外的弹出日历样式 | CSSProperties | {} |  |
| prevIcon | 自定义上一个图标 | ReactNode | - | 4.17.0 |
| size | 输入框大小，`large` 高度为 40px，`small` 为 24px，默认是 32px | `large` \| `middle` \| `small` | - |  |
| status | 设置校验状态 | 'error' \| 'warning' | - | 4.19.0 |
| style | 自定义输入框样式 | CSSProperties | {} |  |
| suffixIcon | 自定义的选择框后缀图标 | ReactNode | - |  |
| superNextIcon | 自定义 `<<` 切换图标 | ReactNode | - | 4.17.0 |
| superPrevIcon | 自定义 `>>` 切换图标 | ReactNode | - | 4.17.0 |
| onOpenChange | 弹出日历和关闭日历的回调 | function(open) | - |  |
| onPanelChange | 日历面板切换的回调 | function(value, mode) | - |  |

#### 共同的方法

| 名称    | 描述     | 版本 |
| ------- | -------- | ---- |
| blur()  | 移除焦点 |      |
| focus() | 获取焦点 |      |

#### DatePicker

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| defaultPickerValue | 默认面板日期 | moment | - |  |
| defaultValue | 默认日期，如果开始时间或结束时间为 `null` 或者 `undefined`，日期范围将是一个开区间 | moment | - |  |
| disabledTime | 不可选择的时间 | function(date) | - |  |
| format | 设置日期格式，为数组时支持多格式匹配，展示以第一个为准。配置参考 moment.js，支持自定义格式 | string \| (value: moment) => string \| (string \| (value: moment) => string)\[] | `YYYY-MM-DD` |  |
| renderExtraFooter | 在面板中添加额外的页脚 | (mode) => React.ReactNode | - |  |
| showNow | 当设定了 `showTime` 的时候，面板是否显示“此刻”按钮 | boolean | - | 4.4.0 |
| showTime | 增加时间选择功能 | Object \| boolean | TimePicker Options |  |
| showTime.defaultValue | 设置用户选择日期时默认的时分秒，例子 | moment | moment() |  |
| showToday | 是否展示“今天”按钮 | boolean | true |  |
| value | 日期 | moment | - |  |
| onChange | 时间发生变化的回调 | function(date: moment, dateString: string) | - |  |
| onOk | 点击确定按钮的回调 | function() | - |  |
| onPanelChange | 日期面板变化时的回调 | function(value, mode) | - |  |

#### DatePicker\[picker=year]

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| defaultPickerValue | 默认面板日期 | moment | - |  |
| defaultValue | 默认日期 | moment | - |  |
| format | 展示的日期格式，配置参考 moment.js | string | `YYYY` |  |
| renderExtraFooter | 在面板中添加额外的页脚 | () => React.ReactNode | - |  |
| value | 日期 | moment | - |  |
| onChange | 时间发生变化的回调，发生在用户选择时间时 | function(date: moment, dateString: string) | - |  |

#### DatePicker\[picker=quarter]

`4.1.0` 新增。

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| defaultPickerValue | 默认面板日期 | moment | - |  |
| defaultValue | 默认日期 | moment | - |  |
| format | 展示的日期格式，配置参考 moment.js | string | `YYYY-\QQ` |  |
| renderExtraFooter | 在面板中添加额外的页脚 | () => React.ReactNode | - |  |
| value | 日期 | moment | - |  |
| onChange | 时间发生变化的回调，发生在用户选择时间时 | function(date: moment, dateString: string) | - |  |

#### DatePicker\[picker=month]

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| defaultPickerValue | 默认面板日期 | moment | - |  |
| defaultValue | 默认日期 | moment | - |  |
| format | 展示的日期格式，配置参考 moment.js | string | `YYYY-MM` |  |
| monthCellRender | 自定义的月份内容渲染方法 | function(date, locale): ReactNode | - |  |
| renderExtraFooter | 在面板中添加额外的页脚 | () => React.ReactNode | - |  |
| value | 日期 | moment | - |  |
| onChange | 时间发生变化的回调，发生在用户选择时间时 | function(date: moment, dateString: string) | - |  |

#### DatePicker\[picker=week]

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| defaultPickerValue | 默认面板日期 | moment | - |  |
| defaultValue | 默认日期 | moment | - |  |
| format | 展示的日期格式，配置参考 moment.js | string | `YYYY-wo` |  |
| renderExtraFooter | 在面板中添加额外的页脚 | (mode) => React.ReactNode | - |  |
| value | 日期 | moment | - |  |
| onChange | 时间发生变化的回调，发生在用户选择时间时 | function(date: moment, dateString: string) | - |  |

#### RangePicker

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| allowEmpty | 允许起始项部分为空 | \[boolean, boolean] | \[false, false] |  |
| dateRender | 自定义日期单元格的内容。`info` 参数自 4.3.0 添加 | function(currentDate: moment, today: moment, info: { range: `start` \| `end` }) => React.ReactNode | - |  |
| defaultPickerValue | 默认面板日期 | moment\[] | - |  |
| defaultValue | 默认日期 | moment\[] | - |  |
| disabled | 禁用起始项 | \[boolean, boolean] | - |  |
| disabledTime | 不可选择的时间 | function(date: moment, partial: `start` \| `end`) | - |  |
| format | 展示的日期格式 | string | `YYYY-MM-DD HH:mm:ss` |  |
| ranges | 预设时间范围快捷选择 | { \[range: string]: moment\[] } \| { \[range: string]: () => moment\[] } | - |  |
| renderExtraFooter | 在面板中添加额外的页脚 | () => React.ReactNode | - |  |
| separator | 设置分隔符 | React.ReactNode | `` |  |
| showTime | 增加时间选择功能 | Object\|boolean | TimePicker Options |  |
| showTime.defaultValue | 设置用户选择日期时默认的时分秒，例子 | moment\[] | \[moment(), moment()] |  |
| value | 日期 | moment\[] | - |  |
| onCalendarChange | 待选日期发生变化的回调。`info` 参数自 4.4.0 添加 | function(dates: \[moment, moment], dateStrings: \[string, string], info: { range:`start`\|`end` }) | - |  |
| onChange | 日期范围发生变化的回调 | function(dates: \[moment, moment], dateStrings: \[string, string]) | - |  |

### FAQ

#### 当我指定了 DatePicker/RangePicker 的 mode 属性后，点击后无法选择年份/月份？

请参考常见问答

#### 如何在 DatePicker 中使用自定义日期库（如 dayjs ）？

请参考《替换 Moment.js》

#### 为什么时间类组件的国际化 locale 设置不生效？

参考 FAQ 为什么时间类组件的国际化 locale 设置不生效？。

#### 如何修改周的起始日？

请使用正确的语言包（#5605），或者修改 moment 的 `locale` 配置：

```js
moment.locale('en', {
  // 注意请修改你正在使用的 locale 语言，比如 zh-cn
  week: {
    dow: 1,
  },
});
```

#### 为何使用 `panelRender` 时，原来面板无法切换？

当你通过 `panelRender` 动态改变层级结构时，会使得原本的 Panel 被当做新的节点删除并创建。这使得其原本的状态会被重置，保持结构稳定即可。详情请参考 #27263。

## 示例代码

### 基本

- demo: `basic`

最简单的用法，在浮层中可以选择或者输入日期。

```tsx
import type { DatePickerProps } from 'antd';
import { DatePicker, Space } from 'antd';
import React from 'react';

const onChange: DatePickerProps['onChange'] = (date, dateString) => {
  console.log(date, dateString);
};

const App: React.FC = () => (
  <Space direction="vertical">
    <DatePicker onChange={onChange} />
    <DatePicker onChange={onChange} picker="week" />
    <DatePicker onChange={onChange} picker="month" />
    <DatePicker onChange={onChange} picker="quarter" />
    <DatePicker onChange={onChange} picker="year" />
  </Space>
);

export default App;
```

### 范围选择器

- demo: `range-picker`

通过设置 `picker` 属性，指定范围选择器类型。

```tsx
import { DatePicker, Space } from 'antd';
import React from 'react';

const { RangePicker } = DatePicker;

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <RangePicker />
    <RangePicker showTime />
    <RangePicker picker="week" />
    <RangePicker picker="month" />
    <RangePicker picker="quarter" />
    <RangePicker picker="year" />
  </Space>
);

export default App;
```

### 切换不同的选择器

- demo: `switchable`

提供选择器，自由切换不同类型的日期选择器，常用于日期筛选场合。

```tsx
import type { DatePickerProps, TimePickerProps } from 'antd';
import { DatePicker, Select, Space, TimePicker } from 'antd';
import React, { useState } from 'react';

const { Option } = Select;

type PickerType = 'time' | 'date';

const PickerWithType = ({
  type,
  onChange,
}: {
  type: PickerType;
  onChange: TimePickerProps['onChange'] | DatePickerProps['onChange'];
}) => {
  if (type === 'time') return <TimePicker onChange={onChange} />;
  if (type === 'date') return <DatePicker onChange={onChange} />;
  return <DatePicker picker={type} onChange={onChange} />;
};

const App: React.FC = () => {
  const [type, setType] = useState<PickerType>('time');

  return (
    <Space>
      <Select value={type} onChange={setType}>
        <Option value="time">Time</Option>
        <Option value="date">Date</Option>
        <Option value="week">Week</Option>
        <Option value="month">Month</Option>
        <Option value="quarter">Quarter</Option>
        <Option value="year">Year</Option>
      </Select>
      <PickerWithType type={type} onChange={value => console.log(value)} />
    </Space>
  );
};

export default App;
```

### 日期格式

- demo: `format`

使用 `format` 属性，可以自定义日期显示格式。

```tsx
import type { DatePickerProps } from 'antd';
import { DatePicker, Space } from 'antd';
import moment from 'moment';
import React from 'react';

const { RangePicker } = DatePicker;

const dateFormat = 'YYYY/MM/DD';
const weekFormat = 'MM/DD';
const monthFormat = 'YYYY/MM';

const dateFormatList = ['DD/MM/YYYY', 'DD/MM/YY'];

const customFormat: DatePickerProps['format'] = value =>
  `custom format: ${value.format(dateFormat)}`;

const customWeekStartEndFormat: DatePickerProps['format'] = value =>
  `${moment(value).startOf('week').format(weekFormat)} ~ ${moment(value)
    .endOf('week')
    .format(weekFormat)}`;

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <DatePicker defaultValue={moment('2015/01/01', dateFormat)} format={dateFormat} />
    <DatePicker defaultValue={moment('01/01/2015', dateFormatList[0])} format={dateFormatList} />
    <DatePicker defaultValue={moment('2015/01', monthFormat)} format={monthFormat} picker="month" />
    <DatePicker defaultValue={moment()} format={customWeekStartEndFormat} picker="week" />
    <RangePicker
      defaultValue={[moment('2015/01/01', dateFormat), moment('2015/01/01', dateFormat)]}
      format={dateFormat}
    />
    <DatePicker defaultValue={moment('2015/01/01', dateFormat)} format={customFormat} />
  </Space>
);

export default App;
```

### 日期时间选择

- demo: `time`

增加选择时间功能，当 `showTime` 为一个对象时，其属性会传递给内建的 `TimePicker`。

```tsx
import { DatePicker, Space } from 'antd';
import type { DatePickerProps, RangePickerProps } from 'antd/es/date-picker';
import React from 'react';

const { RangePicker } = DatePicker;

const onChange = (
  value: DatePickerProps['value'] | RangePickerProps['value'],
  dateString: [string, string] | string,
) => {
  console.log('Selected Time: ', value);
  console.log('Formatted Selected Time: ', dateString);
};

const onOk = (value: DatePickerProps['value'] | RangePickerProps['value']) => {
  console.log('onOk: ', value);
};

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <DatePicker showTime onChange={onChange} onOk={onOk} />
    <RangePicker
      showTime={{ format: 'HH:mm' }}
      format="YYYY-MM-DD HH:mm"
      onChange={onChange}
      onOk={onOk}
    />
  </Space>
);

export default App;
```

### 禁用

- demo: `disabled`

选择框的不可用状态。你也可以通过数组单独禁用 RangePicker 的其中一项。

```tsx
import { DatePicker, Space } from 'antd';
import moment from 'moment';
import React from 'react';

const { RangePicker } = DatePicker;

const dateFormat = 'YYYY-MM-DD';

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <DatePicker defaultValue={moment('2015-06-06', dateFormat)} disabled />
    <DatePicker picker="month" defaultValue={moment('2015-06', 'YYYY-MM')} disabled />
    <RangePicker
      defaultValue={[moment('2015-06-06', dateFormat), moment('2015-06-06', dateFormat)]}
      disabled
    />
    <RangePicker
      defaultValue={[moment('2019-09-03', dateFormat), moment('2019-11-22', dateFormat)]}
      disabled={[false, true]}
    />
  </Space>
);

export default App;
```

### 不可选择日期和时间

- demo: `disabled-date`

可用 `disabledDate` 和 `disabledTime` 分别禁止选择部分日期和时间，其中 `disabledTime` 需要和 `showTime` 一起使用。

```tsx
import { DatePicker, Space } from 'antd';
import type { RangePickerProps } from 'antd/es/date-picker';
import moment from 'moment';
import React from 'react';

const { RangePicker } = DatePicker;

const range = (start: number, end: number) => {
  const result = [];
  for (let i = start; i < end; i++) {
    result.push(i);
  }
  return result;
};

// eslint-disable-next-line arrow-body-style
const disabledDate: RangePickerProps['disabledDate'] = current => {
  // Can not select days before today and today
  return current && current < moment().endOf('day');
};

const disabledDateTime = () => ({
  disabledHours: () => range(0, 24).splice(4, 20),
  disabledMinutes: () => range(30, 60),
  disabledSeconds: () => [55, 56],
});

const disabledRangeTime: RangePickerProps['disabledTime'] = (_, type) => {
  if (type === 'start') {
    return {
      disabledHours: () => range(0, 60).splice(4, 20),
      disabledMinutes: () => range(30, 60),
      disabledSeconds: () => [55, 56],
    };
  }
  return {
    disabledHours: () => range(0, 60).splice(20, 4),
    disabledMinutes: () => range(0, 31),
    disabledSeconds: () => [55, 56],
  };
};

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <DatePicker
      format="YYYY-MM-DD HH:mm:ss"
      disabledDate={disabledDate}
      disabledTime={disabledDateTime}
      showTime={{ defaultValue: moment('00:00:00', 'HH:mm:ss') }}
    />
    <DatePicker picker="month" disabledDate={disabledDate} />
    <RangePicker disabledDate={disabledDate} />
    <RangePicker
      disabledDate={disabledDate}
      disabledTime={disabledRangeTime}
      showTime={{
        hideDisabledOptions: true,
        defaultValue: [moment('00:00:00', 'HH:mm:ss'), moment('11:59:59', 'HH:mm:ss')],
      }}
      format="YYYY-MM-DD HH:mm:ss"
    />
  </Space>
);

export default App;
```

### 选择不超过七天的范围

- demo: `select-in-range`

这里举例如何用 `onCalendarChange` 和 `disabledDate` 来限制动态的日期区间选择。

```tsx
import { DatePicker } from 'antd';
import type { Moment } from 'moment';
import React, { useState } from 'react';

const { RangePicker } = DatePicker;

type RangeValue = [Moment | null, Moment | null] | null;

const App: React.FC = () => {
  const [dates, setDates] = useState<RangeValue>(null);
  const [value, setValue] = useState<RangeValue>(null);

  const disabledDate = (current: Moment) => {
    if (!dates) {
      return false;
    }
    const tooLate = dates[0] && current.diff(dates[0], 'days') > 7;
    const tooEarly = dates[1] && dates[1].diff(current, 'days') > 7;
    return !!tooEarly || !!tooLate;
  };

  const onOpenChange = (open: boolean) => {
    if (open) {
      setDates([null, null]);
    } else {
      setDates(null);
    }
  };

  return (
    <RangePicker
      value={dates || value}
      disabledDate={disabledDate}
      onCalendarChange={val => setDates(val)}
      onChange={val => setValue(val)}
      onOpenChange={onOpenChange}
      onBlur={() => console.log('blur has been triggered')}
    />
  );
};

export default App;
```

### 预设范围

- demo: `presetted-ranges`

可以预设常用的日期范围以提高用户体验。

```tsx
import { DatePicker, Space } from 'antd';
import type { RangePickerProps } from 'antd/es/date-picker';
import moment from 'moment';
import React from 'react';

const { RangePicker } = DatePicker;

const onChange: RangePickerProps['onChange'] = (dates, dateStrings) => {
  if (dates) {
    console.log('From: ', dates[0], ', to: ', dates[1]);
    console.log('From: ', dateStrings[0], ', to: ', dateStrings[1]);
  } else {
    console.log('Clear');
  }
};

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <RangePicker
      ranges={{
        Today: [moment(), moment()],
        'This Month': [moment().startOf('month'), moment().endOf('month')],
      }}
      onChange={onChange}
    />
    <RangePicker
      ranges={{
        Today: [moment(), moment()],
        'This Month': [moment().startOf('month'), moment().endOf('month')],
      }}
      showTime
      format="YYYY/MM/DD HH:mm:ss"
      onChange={onChange}
    />
  </Space>
);

export default App;
```

### 额外的页脚

- demo: `extra-footer`

在浮层中加入额外的页脚，以满足某些定制信息的需求。

```tsx
import { DatePicker, Space } from 'antd';
import React from 'react';

const { RangePicker } = DatePicker;

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <DatePicker renderExtraFooter={() => 'extra footer'} />
    <DatePicker renderExtraFooter={() => 'extra footer'} showTime />
    <RangePicker renderExtraFooter={() => 'extra footer'} />
    <RangePicker renderExtraFooter={() => 'extra footer'} showTime />
    <DatePicker renderExtraFooter={() => 'extra footer'} picker="month" />
  </Space>
);

export default App;
```

### 三种大小

- demo: `size`

三种大小的输入框，若不设置，则为 `middle`。

```tsx
import type { RadioChangeEvent } from 'antd';
import { DatePicker, Radio, Space } from 'antd';
import type { SizeType } from 'antd/es/config-provider/SizeContext';
import React, { useState } from 'react';

const { RangePicker } = DatePicker;

const App: React.FC = () => {
  const [size, setSize] = useState<SizeType>('middle');

  const handleSizeChange = (e: RadioChangeEvent) => {
    setSize(e.target.value);
  };

  return (
    <Space direction="vertical" size={12}>
      <Radio.Group value={size} onChange={handleSizeChange}>
        <Radio.Button value="large">Large</Radio.Button>
        <Radio.Button value="middle">middle</Radio.Button>
        <Radio.Button value="small">Small</Radio.Button>
      </Radio.Group>
      <DatePicker size={size} />
      <DatePicker size={size} picker="month" />
      <RangePicker size={size} />
      <DatePicker size={size} picker="week" />
    </Space>
  );
};

export default App;
```

### 定制日期单元格

- demo: `date-render`

使用 `dateRender` 可以自定义日期单元格的内容和样式。

```tsx
import { DatePicker, Space } from 'antd';
import React from 'react';

const { RangePicker } = DatePicker;

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <DatePicker
      dateRender={current => {
        const style: React.CSSProperties = {};
        if (current.date() === 1) {
          style.border = '1px solid #1890ff';
          style.borderRadius = '50%';
        }
        return (
          <div className="ant-picker-cell-inner" style={style}>
            {current.date()}
          </div>
        );
      }}
    />
    <RangePicker
      dateRender={current => {
        const style: React.CSSProperties = {};
        if (current.date() === 1) {
          style.border = '1px solid #1890ff';
          style.borderRadius = '50%';
        }
        return (
          <div className="ant-picker-cell-inner" style={style}>
            {current.date()}
          </div>
        );
      }}
    />
  </Space>
);

export default App;
```

### 自定义状态

- demo: `status`

使用 `status` 为 DatePicker 添加状态，可选 `error` 或者 `warning`。

```tsx
import { DatePicker, Space } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Space direction="vertical" style={{ width: '100%' }}>
    <DatePicker status="error" style={{ width: '100%' }} />
    <DatePicker status="warning" style={{ width: '100%' }} />
    <DatePicker.RangePicker status="error" style={{ width: '100%' }} />
    <DatePicker.RangePicker status="warning" style={{ width: '100%' }} />
  </Space>
);

export default App;
```

### 无边框

- demo: `bordered`

无边框样式。

```tsx
import { DatePicker, Space } from 'antd';
import React from 'react';

const { RangePicker } = DatePicker;

const App: React.FC = () => (
  <Space direction="vertical" size={12}>
    <DatePicker bordered={false} />
    <DatePicker picker="week" bordered={false} />
    <DatePicker picker="month" bordered={false} />
    <DatePicker picker="year" bordered={false} />
    <RangePicker bordered={false} />
    <RangePicker picker="week" bordered={false} />
    <RangePicker picker="month" bordered={false} />
    <RangePicker picker="year" bordered={false} />
  </Space>
);

export default App;
```

### 弹出位置

- demo: `placement`

可以通过 `placement` 手动指定弹出的位置。

```tsx
import type { DatePickerProps, RadioChangeEvent } from 'antd';
import { DatePicker, Radio } from 'antd';
import React, { useState } from 'react';

const { RangePicker } = DatePicker;

const App: React.FC = () => {
  const [placement, SetPlacement] = useState<DatePickerProps['placement']>('topLeft');

  const placementChange = (e: RadioChangeEvent) => {
    SetPlacement(e.target.value);
  };

  return (
    <>
      <Radio.Group value={placement} onChange={placementChange}>
        <Radio.Button value="topLeft">topLeft</Radio.Button>
        <Radio.Button value="topRight">topRight</Radio.Button>
        <Radio.Button value="bottomLeft">bottomLeft</Radio.Button>
        <Radio.Button value="bottomRight">bottomRight</Radio.Button>
      </Radio.Group>
      <br />
      <br />
      <DatePicker placement={placement} />
      <br />
      <br />
      <RangePicker placement={placement} />
    </>
  );
};

export default App;
```
