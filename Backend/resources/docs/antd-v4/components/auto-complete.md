---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "auto-complete"
component: "AutoComplete"
component_code_name: "AutoComplete"
recommended_import: "import { AutoComplete } from 'antd';"
subtitle_zh: "自动完成"
type_zh: "数据录入"
demo_count: 7
---

# AutoComplete 自动完成

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`AutoComplete`
- 推荐导入：`import { AutoComplete } from 'antd';`
- 组件分类：数据录入
- 简述：输入框自动完成功能。

## 示例索引

- 基本使用（basic）
- 自定义选项（options）
- 自定义输入组件（custom）
- 不区分大小写（non-case-sensitive）
- 查询模式 - 确定类目（certain-category）
- 查询模式 - 不确定类目（uncertain-category）
- 自定义状态（status）

## 使用文档

输入框自动完成功能。

### 何时使用

- 需要一个输入框而不是选择器。
- 需要输入建议/辅助提示。

和 Select 的区别是：

- AutoComplete 是一个带提示的文本输入框，用户可以自由输入，关键词是辅助**输入**。
- Select 是在限定的可选项中进行选择，关键词是**选择**。

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| allowClear | 支持清除 | boolean | false |  |
| autoFocus | 自动获取焦点 | boolean | false |  |
| backfill | 使用键盘选择选项的时候把选中项回填到输入框中 | boolean | false |  |
| children (自动完成的数据源) | 自动完成的数据源 | React.ReactElement&lt;OptionProps> \| Array&lt;React.ReactElement&lt;OptionProps>> | - |  |
| children (自定义输入框) | 自定义输入框 | HTMLInputElement \| HTMLTextAreaElement \| React.ReactElement&lt;InputProps> | &lt;Input /> |  |
| defaultActiveFirstOption | 是否默认高亮第一个选项 | boolean | true |  |
| defaultOpen | 是否默认展开下拉菜单 | boolean | - |  |
| defaultValue | 指定默认选中的条目 | string | - |  |
| disabled | 是否禁用 | boolean | false |  |
| popupClassName | 下拉菜单的 className 属性 | string | - | 4.23.0 |
| dropdownMatchSelectWidth | 下拉菜单和选择器同宽。默认将设置 `min-width`，当值小于选择框宽度时会被忽略。false 时会关闭虚拟滚动 | boolean \| number | true |  |
| filterOption | 是否根据输入项进行筛选。当其为一个函数时，会接收 `inputValue` `option` 两个参数，当 `option` 符合筛选条件时，应返回 true，反之则返回 false | boolean \| function(inputValue, option) | true |  |
| getPopupContainer | 菜单渲染父节点。默认渲染到 body 上，如果你遇到菜单滚动定位问题，试试修改为滚动的区域，并相对其定位。示例 | function(triggerNode) | () => document.body |  |
| notFoundContent | 当下拉列表为空时显示的内容 | ReactNode | - |  |
| open | 是否展开下拉菜单 | boolean | - |  |
| options | 数据化配置选项内容，相比 jsx 定义会获得更好的渲染性能 | { label, value }\[] | - |  |
| placeholder | 输入框提示 | string | - |  |
| status | 设置校验状态 | 'error' \| 'warning' | - | 4.19.0 |
| value | 指定当前选中的条目 | string | - |  |
| onBlur | 失去焦点时的回调 | function() | - |  |
| onChange | 选中 option，或 input 的 value 变化时，调用此函数 | function(value) | - |  |
| onDropdownVisibleChange | 展开下拉菜单的回调 | function(open) | - |  |
| onFocus | 获得焦点时的回调 | function() | - |  |
| onSearch | 搜索补全项的时候调用 | function(value) | - |  |
| onSelect | 被选中时调用，参数为选中项的 value 值 | function(value, option) | - |  |
| onClear | 清除内容时的回调 | function | - | 4.6.0 |

### 方法

| 名称    | 描述     | 版本 |
| ------- | -------- | ---- |
| blur()  | 移除焦点 |      |
| focus() | 获取焦点 |      |

### FAQ

#### 为何受控状态下使用 onSearch 无法输入中文？

请使用 `onChange` 进行受控管理。`onSearch` 触发于搜索输入，与 `onChange` 时机不同。此外，点选选项时也不会触发 `onSearch` 事件。

相关 issue：#18230 #17916

#### v3 的部分属性为何在 v4 中没有了？

AutoComplete 组件是一个支持自动提示的 Input 组件，因而其不具有 `labelInValue` 等影响 value 展示的属性。在 v3 版本，AutoComplete 实现存在输入值如果遇到 `value` 与 `label` 相同时无法映射的问题。 v4 中不再支持 `label` 为值的输入形态。

此外为了统一 API，`dataSource` 改为 `options` 你可以如下转换：

##### v3

```tsx
dataSource = ['light', 'bamboo'];
// or
dataSource = [
  { value: 'light', text: 'Light' },
  { value: 'bamboo', text: 'Bamboo' },
];
```

##### v4

```tsx
options = [
  { value: 'light', label: 'Light' },
  { value: 'bamboo', label: 'Bamboo' },
];
```

## 示例代码

### 基本使用

- demo: `basic`

基本使用，通过 `options` 设置自动完成的数据源。

```tsx
import { AutoComplete } from 'antd';
import React, { useState } from 'react';

const mockVal = (str: string, repeat = 1) => ({
  value: str.repeat(repeat),
});

const App: React.FC = () => {
  const [value, setValue] = useState('');
  const [options, setOptions] = useState<{ value: string }[]>([]);

  const onSearch = (searchText: string) => {
    setOptions(
      !searchText ? [] : [mockVal(searchText), mockVal(searchText, 2), mockVal(searchText, 3)],
    );
  };

  const onSelect = (data: string) => {
    console.log('onSelect', data);
  };

  const onChange = (data: string) => {
    setValue(data);
  };

  return (
    <>
      <AutoComplete
        options={options}
        style={{ width: 200 }}
        onSelect={onSelect}
        onSearch={onSearch}
        placeholder="input here"
      />
      <br />
      <br />
      <AutoComplete
        value={value}
        options={options}
        style={{ width: 200 }}
        onSelect={onSelect}
        onSearch={onSearch}
        onChange={onChange}
        placeholder="control mode"
      />
    </>
  );
};

export default App;
```

### 自定义选项

- demo: `options`

可以返回自定义的 `Option` label

```tsx
import { AutoComplete } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [options, setOptions] = useState<{ value: string; label: string }[]>([]);

  const handleSearch = (value: string) => {
    let res: { value: string; label: string }[] = [];
    if (!value || value.indexOf('@') >= 0) {
      res = [];
    } else {
      res = ['gmail.com', '163.com', 'qq.com'].map(domain => ({
        value,
        label: `${value}@${domain}`,
      }));
    }
    setOptions(res);
  };

  return (
    <AutoComplete
      style={{ width: 200 }}
      onSearch={handleSearch}
      placeholder="input here"
      options={options}
    />
  );
};

export default App;
```

### 自定义输入组件

- demo: `custom`

自定义输入组件。

```tsx
import { AutoComplete, Input } from 'antd';
import React, { useState } from 'react';

const { TextArea } = Input;

const App: React.FC = () => {
  const [options, setOptions] = useState<{ value: string }[]>([]);

  const handleSearch = (value: string) => {
    setOptions(
      !value ? [] : [{ value }, { value: value + value }, { value: value + value + value }],
    );
  };

  const handleKeyPress = (ev: React.KeyboardEvent<HTMLTextAreaElement>) => {
    console.log('handleKeyPress', ev);
  };

  const onSelect = (value: string) => {
    console.log('onSelect', value);
  };

  return (
    <AutoComplete
      options={options}
      style={{ width: 200 }}
      onSelect={onSelect}
      onSearch={handleSearch}
    >
      <TextArea
        placeholder="input here"
        className="custom"
        style={{ height: 50 }}
        onKeyPress={handleKeyPress}
      />
    </AutoComplete>
  );
};

export default App;
```

### 不区分大小写

- demo: `non-case-sensitive`

不区分大小写的 AutoComplete

```tsx
import { AutoComplete } from 'antd';
import React from 'react';

const options = [
  { value: 'Burns Bay Road' },
  { value: 'Downing Street' },
  { value: 'Wall Street' },
];

const App: React.FC = () => (
  <AutoComplete
    style={{ width: 200 }}
    options={options}
    placeholder="try to type `b`"
    filterOption={(inputValue, option) =>
      option!.value.toUpperCase().indexOf(inputValue.toUpperCase()) !== -1
    }
  />
);

export default App;
```

### 查询模式 - 确定类目

- demo: `certain-category`

查询模式: 确定类目 示例。

```tsx
import { UserOutlined } from '@ant-design/icons';
import { AutoComplete, Input } from 'antd';
import React from 'react';

const renderTitle = (title: string) => (
  <span>
    {title}
    <a
      style={{ float: 'right' }}
      href="https://www.google.com/search?q=antd"
      target="_blank"
      rel="noopener noreferrer"
    >
      more
    </a>
  </span>
);

const renderItem = (title: string, count: number) => ({
  value: title,
  label: (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
      }}
    >
      {title}
      <span>
        <UserOutlined /> {count}
      </span>
    </div>
  ),
});

const options = [
  {
    label: renderTitle('Libraries'),
    options: [renderItem('AntDesign', 10000), renderItem('AntDesign UI', 10600)],
  },
  {
    label: renderTitle('Solutions'),
    options: [renderItem('AntDesign UI FAQ', 60100), renderItem('AntDesign FAQ', 30010)],
  },
  {
    label: renderTitle('Articles'),
    options: [renderItem('AntDesign design language', 100000)],
  },
];

const App: React.FC = () => (
  <AutoComplete
    popupClassName="certain-category-search-dropdown"
    dropdownMatchSelectWidth={500}
    style={{ width: 250 }}
    options={options}
  >
    <Input.Search size="large" placeholder="input here" />
  </AutoComplete>
);

export default App;
```

```css
.certain-category-search-dropdown .ant-select-dropdown-menu-item-group-title {
  color: #666;
  font-weight: bold;
}

.certain-category-search-dropdown .ant-select-dropdown-menu-item-group {
  border-bottom: 1px solid #f6f6f6;
}

.certain-category-search-dropdown .ant-select-dropdown-menu-item {
  padding-left: 16px;
}

.certain-category-search-dropdown .ant-select-dropdown-menu-item.show-all {
  text-align: center;
  cursor: default;
}

.certain-category-search-dropdown .ant-select-dropdown-menu {
  max-height: 300px;
}
```

### 查询模式 - 不确定类目

- demo: `uncertain-category`

查询模式: 不确定类目 示例。

```tsx
import { AutoComplete, Input } from 'antd';
import type { SelectProps } from 'antd/es/select';
import React, { useState } from 'react';

const getRandomInt = (max: number, min = 0) => Math.floor(Math.random() * (max - min + 1)) + min;

const searchResult = (query: string) =>
  new Array(getRandomInt(5))
    .join('.')
    .split('.')
    .map((_, idx) => {
      const category = `${query}${idx}`;
      return {
        value: category,
        label: (
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
            }}
          >
            <span>
              Found {query} on{' '}
              <a
                href={`https://s.taobao.com/search?q=${query}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                {category}
              </a>
            </span>
            <span>{getRandomInt(200, 100)} results</span>
          </div>
        ),
      };
    });

const App: React.FC = () => {
  const [options, setOptions] = useState<SelectProps<object>['options']>([]);

  const handleSearch = (value: string) => {
    setOptions(value ? searchResult(value) : []);
  };

  const onSelect = (value: string) => {
    console.log('onSelect', value);
  };

  return (
    <AutoComplete
      dropdownMatchSelectWidth={252}
      style={{ width: 300 }}
      options={options}
      onSelect={onSelect}
      onSearch={handleSearch}
    >
      <Input.Search size="large" placeholder="input here" enterButton />
    </AutoComplete>
  );
};

export default App;
```

### 自定义状态

- demo: `status`

使用 `status` 为 AutoComplete 添加状态，可选 `error` 或者 `warning`。

```tsx
import { AutoComplete, Space } from 'antd';
import React, { useState } from 'react';

const mockVal = (str: string, repeat = 1) => ({
  value: str.repeat(repeat),
});

const App: React.FC = () => {
  const [options, setOptions] = useState<{ value: string }[]>([]);

  const onSearch = (searchText: string) => {
    setOptions(
      !searchText ? [] : [mockVal(searchText), mockVal(searchText, 2), mockVal(searchText, 3)],
    );
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <AutoComplete options={options} onSearch={onSearch} status="error" style={{ width: 200 }} />
      <AutoComplete options={options} onSearch={onSearch} status="warning" style={{ width: 200 }} />
    </Space>
  );
};

export default App;
```
