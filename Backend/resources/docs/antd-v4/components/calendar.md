---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "calendar"
component: "Calendar"
component_code_name: "Calendar"
recommended_import: "import { Calendar } from 'antd';"
subtitle_zh: "日历"
type_zh: "数据展示"
demo_count: 5
---

# Calendar 日历

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Calendar`
- 推荐导入：`import { Calendar } from 'antd';`
- 组件分类：数据展示
- 简述：按照日历形式展示数据的容器。

## 示例索引

- 基本（basic）
- 通知事项日历（notice-calendar）
- 卡片模式（card）
- 选择功能（select）
- 自定义头部（customize-header）

## 使用文档

按照日历形式展示数据的容器。

### 何时使用

当数据是日期或按照日期划分时，例如日程、课表、价格日历等，农历等。目前支持年/月切换。

### API

```jsx
<Calendar
  dateCellRender={dateCellRender}
  monthCellRender={monthCellRender}
  onPanelChange={onPanelChange}
  onSelect={onSelect}
/>
```

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| dateCellRender | 自定义渲染日期单元格，返回内容会被追加到单元格 | function(date: Moment): ReactNode | - |  |
| dateFullCellRender | 自定义渲染日期单元格，返回内容覆盖单元格 | function(date: Moment): ReactNode | - |  |
| defaultValue | 默认展示的日期 | moment | - |  |
| disabledDate | 不可选择的日期，参数为当前 `value`，注意使用时不要直接修改 | (currentDate: Moment) => boolean | - |  |
| fullscreen | 是否全屏显示 | boolean | true |  |
| headerRender | 自定义头部内容 | function(object:{value: Moment, type: string, onChange: f(), onTypeChange: f()}) | - |  |
| locale | 国际化配置 | object | (默认配置) |  |
| mode | 初始模式 | `month` \| `year` | `month` |  |
| monthCellRender | 自定义渲染月单元格，返回内容会被追加到单元格 | function(date: Moment): ReactNode | - |  |
| monthFullCellRender | 自定义渲染月单元格，返回内容覆盖单元格 | function(date: Moment): ReactNode | - |  |
| validRange | 设置可以显示的日期 | \[moment, moment] | - |  |
| value | 展示日期 | moment | - |  |
| onChange | 日期变化回调 | function(date: Moment) | - |  |
| onPanelChange | 日期面板变化回调 | function(date: Moment, mode: string) | - |  |
| onSelect | 点击选择日期回调 | function(date: Moment） | - |  |

### FAQ

#### 如何在 Calendar 中使用自定义日期库（如 dayjs ）

参考 如何在 Calendar 中使用自定义日期库（如 dayjs ）。

#### 如何给日期类组件配置国际化？

参考 如何给日期类组件配置国际化。

#### 为什么时间类组件的国际化 locale 设置不生效？

参考 FAQ 为什么时间类组件的国际化 locale 设置不生效？。

## 示例代码

### 基本

- demo: `basic`

一个通用的日历面板，支持年/月切换。

```tsx
import { Calendar } from 'antd';
import type { CalendarMode } from 'antd/es/calendar/generateCalendar';
import type { Moment } from 'moment';
import React from 'react';

const App: React.FC = () => {
  const onPanelChange = (value: Moment, mode: CalendarMode) => {
    console.log(value.format('YYYY-MM-DD'), mode);
  };

  return <Calendar onPanelChange={onPanelChange} />;
};

export default App;
```

### 通知事项日历

- demo: `notice-calendar`

一个复杂的应用示例，用 `dateCellRender` 和 `monthCellRender` 函数来自定义需要渲染的数据。

```tsx
import type { BadgeProps } from 'antd';
import { Badge, Calendar } from 'antd';
import type { Moment } from 'moment';
import React from 'react';

const getListData = (value: Moment) => {
  let listData;
  switch (value.date()) {
    case 8:
      listData = [
        { type: 'warning', content: 'This is warning event.' },
        { type: 'success', content: 'This is usual event.' },
      ];
      break;
    case 10:
      listData = [
        { type: 'warning', content: 'This is warning event.' },
        { type: 'success', content: 'This is usual event.' },
        { type: 'error', content: 'This is error event.' },
      ];
      break;
    case 15:
      listData = [
        { type: 'warning', content: 'This is warning event' },
        { type: 'success', content: 'This is very long usual event。。....' },
        { type: 'error', content: 'This is error event 1.' },
        { type: 'error', content: 'This is error event 2.' },
        { type: 'error', content: 'This is error event 3.' },
        { type: 'error', content: 'This is error event 4.' },
      ];
      break;
    default:
  }
  return listData || [];
};

const getMonthData = (value: Moment) => {
  if (value.month() === 8) {
    return 1394;
  }
};

const App: React.FC = () => {
  const monthCellRender = (value: Moment) => {
    const num = getMonthData(value);
    return num ? (
      <div className="notes-month">
        <section>{num}</section>
        <span>Backlog number</span>
      </div>
    ) : null;
  };

  const dateCellRender = (value: Moment) => {
    const listData = getListData(value);
    return (
      <ul className="events">
        {listData.map(item => (
          <li key={item.content}>
            <Badge status={item.type as BadgeProps['status']} text={item.content} />
          </li>
        ))}
      </ul>
    );
  };

  return <Calendar dateCellRender={dateCellRender} monthCellRender={monthCellRender} />;
};

export default App;
```

```css
.events {
  margin: 0;
  padding: 0;
  list-style: none;
}
.events .ant-badge-status {
  width: 100%;
  overflow: hidden;
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.notes-month {
  font-size: 28px;
  text-align: center;
}
.notes-month section {
  font-size: 28px;
}
```

### 卡片模式

- demo: `card`

用于嵌套在空间有限的容器中。

```tsx
import { Calendar } from 'antd';
import type { CalendarMode } from 'antd/es/calendar/generateCalendar';
import type { Moment } from 'moment';
import React from 'react';

const App: React.FC = () => {
  const onPanelChange = (value: Moment, mode: CalendarMode) => {
    console.log(value.format('YYYY-MM-DD'), mode);
  };

  return (
    <div className="site-calendar-demo-card">
      <Calendar fullscreen={false} onPanelChange={onPanelChange} />
    </div>
  );
};

export default App;
```

```css
.site-calendar-demo-card {
  width: 300px;
  border: 1px solid #f0f0f0;
  border-radius: 2px;
}
```

### 选择功能

- demo: `select`

一个通用的日历面板，支持年/月切换。

```tsx
import { Alert, Calendar } from 'antd';
import type { Moment } from 'moment';
import moment from 'moment';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [value, setValue] = useState(() => moment('2017-01-25'));
  const [selectedValue, setSelectedValue] = useState(() => moment('2017-01-25'));

  const onSelect = (newValue: Moment) => {
    setValue(newValue);
    setSelectedValue(newValue);
  };

  const onPanelChange = (newValue: Moment) => {
    setValue(newValue);
  };

  return (
    <>
      <Alert message={`You selected date: ${selectedValue?.format('YYYY-MM-DD')}`} />
      <Calendar value={value} onSelect={onSelect} onPanelChange={onPanelChange} />
    </>
  );
};

export default App;
```

### 自定义头部

- demo: `customize-header`

自定义日历头部内容。

```tsx
import { Calendar, Col, Radio, Row, Select, Typography } from 'antd';
import type { CalendarMode } from 'antd/es/calendar/generateCalendar';
import type { Moment } from 'moment';
import React from 'react';

const App: React.FC = () => {
  const onPanelChange = (value: Moment, mode: CalendarMode) => {
    console.log(value.format('YYYY-MM-DD'), mode);
  };

  return (
    <div className="site-calendar-customize-header-wrapper">
      <Calendar
        fullscreen={false}
        headerRender={({ value, type, onChange, onTypeChange }) => {
          const start = 0;
          const end = 12;
          const monthOptions = [];

          const current = value.clone();
          const localeData = value.localeData();
          const months = [];
          for (let i = 0; i < 12; i++) {
            current.month(i);
            months.push(localeData.monthsShort(current));
          }

          for (let i = start; i < end; i++) {
            monthOptions.push(
              <Select.Option key={i} value={i} className="month-item">
                {months[i]}
              </Select.Option>,
            );
          }

          const year = value.year();
          const month = value.month();
          const options = [];
          for (let i = year - 10; i < year + 10; i += 1) {
            options.push(
              <Select.Option key={i} value={i} className="year-item">
                {i}
              </Select.Option>,
            );
          }
          return (
            <div style={{ padding: 8 }}>
              <Typography.Title level={4}>Custom header</Typography.Title>
              <Row gutter={8}>
                <Col>
                  <Radio.Group
                    size="small"
                    onChange={e => onTypeChange(e.target.value)}
                    value={type}
                  >
                    <Radio.Button value="month">Month</Radio.Button>
                    <Radio.Button value="year">Year</Radio.Button>
                  </Radio.Group>
                </Col>
                <Col>
                  <Select
                    size="small"
                    dropdownMatchSelectWidth={false}
                    className="my-year-select"
                    value={year}
                    onChange={newYear => {
                      const now = value.clone().year(newYear);
                      onChange(now);
                    }}
                  >
                    {options}
                  </Select>
                </Col>
                <Col>
                  <Select
                    size="small"
                    dropdownMatchSelectWidth={false}
                    value={month}
                    onChange={newMonth => {
                      const now = value.clone().month(newMonth);
                      onChange(now);
                    }}
                  >
                    {monthOptions}
                  </Select>
                </Col>
              </Row>
            </div>
          );
        }}
        onPanelChange={onPanelChange}
      />
    </div>
  );
};

export default App;
```

```css
.site-calendar-customize-header-wrapper {
  width: 300px;
  border: 1px solid #f0f0f0;
  border-radius: 2px;
}
```
