---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "select"
component: "Select"
component_code_name: "Select"
recommended_import: "import { Select } from 'antd';"
subtitle_zh: "选择器"
type_zh: "数据录入"
demo_count: 21
---

# Select 选择器

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Select`
- 推荐导入：`import { Select } from 'antd';`
- 组件分类：数据录入
- 简述：下拉选择器。

## 示例索引

- 基本使用（basic）
- 带搜索框（search）
- 多选（multiple）
- 三种大小（size）
- 定制回填内容（option-label-prop）
- 带排序的搜索（search-sort）
- 标签（tags）
- 分组（optgroup）
- 联动（coordinate）
- 搜索框（search-box）
- 获得选项的文本（label-in-value）
- 自动分词（automatic-tokenization）
- 搜索用户（select-users）
- 扩展菜单（custom-dropdown-menu）
- 隐藏已选择选项（hide-selected）
- 无边框（bordered）
- 自定义选择标签（custom-tag-render）
- 响应式 maxTagCount（responsive）
- 大数据（big-data）
- 自定义状态（status）
- 弹出位置（placement）

## 使用文档

下拉选择器。

### 何时使用

- 弹出一个下拉菜单给用户选择操作，用于代替原生的选择器，或者需要一个更优雅的多选器时。
- 当选项少时（少于 5 项），建议直接将选项平铺，使用 Radio 是更好的选择。

### API

```jsx
<Select>
  <Option value="lucy">lucy</Option>
</Select>
```

#### Select props

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| allowClear | 支持清除 | boolean | false |  |
| autoClearSearchValue | 是否在选中项后清空搜索框，只在 `mode` 为 `multiple` 或 `tags` 时有效 | boolean | true |  |
| autoFocus | 默认获取焦点 | boolean | false |  |
| bordered | 是否有边框 | boolean | true |  |
| clearIcon | 自定义的多选框清空图标 | ReactNode | - |  |
| defaultActiveFirstOption | 是否默认高亮第一个选项 | boolean | true |  |
| defaultOpen | 是否默认展开下拉菜单 | boolean | - |  |
| defaultValue | 指定默认选中的条目 | string \| string\[]number \| number\[]LabeledValue \| LabeledValue\[] | - |  |
| disabled | 是否禁用 | boolean | false |  |
| popupClassName | 下拉菜单的 className 属性 | string | - | 4.23.0 |
| dropdownMatchSelectWidth | 下拉菜单和选择器同宽。默认将设置 `min-width`，当值小于选择框宽度时会被忽略。false 时会关闭虚拟滚动 | boolean \| number | true |  |
| dropdownRender | 自定义下拉框内容 | (originNode: ReactNode) => ReactNode | - |  |
| dropdownStyle | 下拉菜单的 style 属性 | CSSProperties | - |  |
| fieldNames | 自定义节点 label、value、options 的字段 | object | { label: `label`, value: `value`, options: `options` } | 4.17.0 |
| filterOption | 是否根据输入项进行筛选。当其为一个函数时，会接收 `inputValue` `option` 两个参数，当 `option` 符合筛选条件时，应返回 true，反之则返回 false | boolean \| function(inputValue, option) | true |  |
| filterSort | 搜索时对筛选结果项的排序函数, 类似Array.sort里的 compareFunction | (optionA: Option, optionB: Option) => number | - | 4.9.0 |
| getPopupContainer | 菜单渲染父节点。默认渲染到 body 上，如果你遇到菜单滚动定位问题，试试修改为滚动的区域，并相对其定位。示例 | function(triggerNode) | () => document.body |  |
| labelInValue | 是否把每个选项的 label 包装到 value 中，会把 Select 的 value 类型从 `string` 变为 { value: string, label: ReactNode } 的格式 | boolean | false |  |
| listHeight | 设置弹窗滚动高度 | number | 256 |  |
| loading | 加载中状态 | boolean | false |  |
| maxTagCount | 最多显示多少个 tag，响应式模式会对性能产生损耗 | number \| `responsive` | - | responsive: 4.10 |
| maxTagPlaceholder | 隐藏 tag 时显示的内容 | ReactNode \| function(omittedValues) | - |  |
| maxTagTextLength | 最大显示的 tag 文本长度 | number | - |  |
| menuItemSelectedIcon | 自定义多选时当前选中的条目图标 | ReactNode | - |  |
| mode | 设置 Select 的模式为多选或标签 | `multiple` \| `tags` | - |  |
| notFoundContent | 当下拉列表为空时显示的内容 | ReactNode | `Not Found` |  |
| open | 是否展开下拉菜单 | boolean | - |  |
| optionFilterProp | 搜索时过滤对应的 `option` 属性，如设置为 `children` 表示对内嵌内容进行搜索。若通过 `options` 属性配置选项内容，建议设置 `optionFilterProp="label"` 来对内容进行搜索。 | string | `value` |  |
| optionLabelProp | 回填到选择框的 Option 的属性值，默认是 Option 的子元素。比如在子元素需要高亮效果时，此值可以设为 `value`。示例 | string | `children` |  |
| options | 数据化配置选项内容，相比 jsx 定义会获得更好的渲染性能 | { label, value }\[] | - |  |
| placeholder | 选择框默认文本 | string | - |  |
| placement | 选择框弹出的位置 | `bottomLeft` `bottomRight` `topLeft` `topRight` | bottomLeft |  |
| removeIcon | 自定义的多选框清除图标 | ReactNode | - |  |
| searchValue | 控制搜索文本 | string | - |  |
| showArrow | 是否显示下拉小箭头 | boolean | 单选为 true，多选为 false |  |
| showSearch | 配置是否可搜索 | boolean | 单选为 false，多选为 true |  |
| size | 选择框大小 | `large` \| `middle` \| `small` | `middle` |  |
| status | 设置校验状态 | 'error' \| 'warning' | - | 4.19.0 |
| suffixIcon | 自定义的选择框后缀图标 | ReactNode | - |  |
| tagRender | 自定义 tag 内容 render，仅在 `mode` 为 `multiple` 或 `tags` 时生效 | (props) => ReactNode | - |  |
| tokenSeparators | 自动分词的分隔符，仅在 `mode="tags"` 时生效 | string\[] | - |  |
| value | 指定当前选中的条目，多选时为一个数组。（value 数组引用未变化时，Select 不会更新） | string \| string\[]number \| number\[]LabeledValue \| LabeledValue\[] | - |  |
| virtual | 设置 false 时关闭虚拟滚动 | boolean | true | 4.1.0 |
| onBlur | 失去焦点时的回调 | function | - |  |
| onChange | 选中 option，或 input 的 value 变化时，调用此函数 | function(value, option:Option \| Array&lt;Option>) | - |  |
| onClear | 清除内容时的回调 | function | - | 4.6.0 |
| onDeselect | 取消选中时调用，参数为选中项的 value (或 key) 值，仅在 `multiple` 或 `tags` 模式下生效 | function(string \| number \| LabeledValue) | - |  |
| onDropdownVisibleChange | 展开下拉菜单的回调 | function(open) | - |  |
| onFocus | 获得焦点时的回调 | function | - |  |
| onInputKeyDown | 按键按下时的回调 | function | - |  |
| onMouseEnter | 鼠标移入时的回调 | function | - |  |
| onMouseLeave | 鼠标移出时的回调 | function | - |  |
| onPopupScroll | 下拉列表滚动时的回调 | function | - |  |
| onSearch | 文本框值变化时的回调 | function(value: string) | - |  |
| onSelect | 被选中时调用，参数为选中项的 value (或 key) 值 | function(string \| number \| LabeledValue, option: Option) | - |  |

> 注意，如果发现下拉菜单跟随页面滚动，或者需要在其他弹层中触发 Select，请尝试使用 `getPopupContainer={triggerNode => triggerNode.parentElement}` 将下拉弹层渲染节点固定在触发器的父元素中。

#### Select Methods

| 名称    | 说明     | 版本 |
| ------- | -------- | ---- |
| blur()  | 取消焦点 |      |
| focus() | 获取焦点 |      |

#### Option props

| 参数      | 说明                     | 类型             | 默认值 | 版本 |
| --------- | ------------------------ | ---------------- | ------ | ---- |
| className | Option 器类名            | string           | -      |      |
| disabled  | 是否禁用                 | boolean          | false  |      |
| title     | 选项上的原生 title 提示  | string           | -      |      |
| value     | 默认根据此属性值进行筛选 | string \| number | -      |      |

#### OptGroup props

| 参数  | 说明 | 类型                    | 默认值 | 版本 |
| ----- | ---- | ----------------------- | ------ | ---- |
| key   | Key  | string                  | -      |      |
| label | 组名 | string \| React.Element | -      |      |

### FAQ

#### `mode="tags"` 模式下为何搜索有时会出现两个相同选项？

这一般是 `options` 中的 `label` 和 `value` 不同导致的，你可以通过 `optionFilterProp="label"` 将过滤设置为展示值以避免这种情况。

#### 点击 `dropdownRender` 里的元素，下拉菜单不会自动消失？

你可以使用受控模式，手动设置 `open` 属性：codesandbox。

#### 反过来希望点击 `dropdownRender` 里元素不消失该怎么办？

Select 当失去焦点时会关闭下拉框，如果你可以通过阻止默认行为避免丢失焦点导致的关闭：

```jsx
<Select
  dropdownRender={() => (
    <div
      onMouseDown={e => {
        e.preventDefault();
        e.stopPropagation();
      }}
    >
      Some Content
    </div>
  )}
/>
```

#### 自定义 Option 样式导致滚动异常怎么办？

这是由于虚拟滚动默认选项高度为 `24px`，如果你的选项高度小于该值则需要通过 `listItemHeight` 属性调整，而 `listHeight` 用于设置滚动容器高度：

```tsx
<Select listItemHeight={10} listHeight={250} />
```

注意：`listItemHeight` 和 `listHeight` 为内部属性，如无必要，请勿修改该值。

#### 为何无障碍测试会报缺失 `aria-` 属性？

Select 无障碍辅助元素仅在弹窗展开时创建，因而当你在进行无障碍检测时请先打开下拉后再进行测试。对于 `aria-label` 与 `aria-labelledby` 属性缺失警告，请自行为 Select 组件添加相应无障碍属性。

## 示例代码

### 基本使用

- demo: `basic`

基本使用。

```tsx
import { Select } from 'antd';
import React from 'react';

const handleChange = (value: string) => {
  console.log(`selected ${value}`);
};

const App: React.FC = () => (
  <>
    <Select
      defaultValue="lucy"
      style={{ width: 120 }}
      onChange={handleChange}
      options={[
        {
          value: 'jack',
          label: 'Jack',
        },
        {
          value: 'lucy',
          label: 'Lucy',
        },
        {
          value: 'disabled',
          disabled: true,
          label: 'Disabled',
        },
        {
          value: 'Yiminghe',
          label: 'yiminghe',
        },
      ]}
    />
    <Select
      defaultValue="lucy"
      style={{ width: 120 }}
      disabled
      options={[
        {
          value: 'lucy',
          label: 'Lucy',
        },
      ]}
    />
    <Select
      defaultValue="lucy"
      style={{ width: 120 }}
      loading
      options={[
        {
          value: 'lucy',
          label: 'Lucy',
        },
      ]}
    />
    <Select
      defaultValue="lucy"
      style={{ width: 120 }}
      allowClear
      options={[
        {
          value: 'lucy',
          label: 'Lucy',
        },
      ]}
    />
  </>
);

export default App;
```

### 带搜索框

- demo: `search`

展开后可对选项进行搜索。

```tsx
import { Select } from 'antd';
import React from 'react';

const onChange = (value: string) => {
  console.log(`selected ${value}`);
};

const onSearch = (value: string) => {
  console.log('search:', value);
};

const App: React.FC = () => (
  <Select
    showSearch
    placeholder="Select a person"
    optionFilterProp="children"
    onChange={onChange}
    onSearch={onSearch}
    filterOption={(input, option) =>
      (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
    }
    options={[
      {
        value: 'jack',
        label: 'Jack',
      },
      {
        value: 'lucy',
        label: 'Lucy',
      },
      {
        value: 'tom',
        label: 'Tom',
      },
    ]}
  />
);

export default App;
```

### 多选

- demo: `multiple`

多选，从已有条目中选择。

```tsx
import { Select } from 'antd';
import React from 'react';
import type { SelectProps } from 'antd';

const options: SelectProps['options'] = [];
for (let i = 10; i < 36; i++) {
  options.push({
    label: i.toString(36) + i,
    value: i.toString(36) + i,
  });
}

const handleChange = (value: string[]) => {
  console.log(`selected ${value}`);
};

const App: React.FC = () => (
  <>
    <Select
      mode="multiple"
      allowClear
      style={{ width: '100%' }}
      placeholder="Please select"
      defaultValue={['a10', 'c12']}
      onChange={handleChange}
      options={options}
    />
    <br />
    <Select
      mode="multiple"
      disabled
      style={{ width: '100%' }}
      placeholder="Please select"
      defaultValue={['a10', 'c12']}
      onChange={handleChange}
      options={options}
    />
  </>
);

export default App;
```

### 三种大小

- demo: `size`

三种大小的选择框，当 size 分别为 `large` 和 `small` 时，输入框高度为 `40px` 和 `24px` ，默认高度为 `32px`。

```tsx
import { Radio, Select } from 'antd';
import type { SizeType } from 'antd/es/config-provider/SizeContext';
import React, { useState } from 'react';
import type { SelectProps, RadioChangeEvent } from 'antd';

const options: SelectProps['options'] = [];
for (let i = 10; i < 36; i++) {
  options.push({
    value: i.toString(36) + i,
    label: i.toString(36) + i,
  });
}

const handleChange = (value: string | string[]) => {
  console.log(`Selected: ${value}`);
};

const App: React.FC = () => {
  const [size, setSize] = useState<SizeType>('middle');

  const handleSizeChange = (e: RadioChangeEvent) => {
    setSize(e.target.value);
  };

  return (
    <>
      <Radio.Group value={size} onChange={handleSizeChange}>
        <Radio.Button value="large">Large</Radio.Button>
        <Radio.Button value="middle">Default</Radio.Button>
        <Radio.Button value="small">Small</Radio.Button>
      </Radio.Group>
      <br />
      <br />
      <Select
        size={size}
        defaultValue="a1"
        onChange={handleChange}
        style={{ width: 200 }}
        options={options}
      />
      <br />
      <Select
        mode="multiple"
        size={size}
        placeholder="Please select"
        defaultValue={['a10', 'c12']}
        onChange={handleChange}
        style={{ width: '100%' }}
        options={options}
      />
      <br />
      <Select
        mode="tags"
        size={size}
        placeholder="Please select"
        defaultValue={['a10', 'c12']}
        onChange={handleChange}
        style={{ width: '100%' }}
        options={options}
      />
    </>
  );
};

export default App;
```

```css
.code-box-demo .ant-select {
  margin: 0 8px 10px 0;
}

.ant-row-rtl .code-box-demo .ant-select {
  margin: 0 0 10px 8px;
}

#components-select-demo-search-box .code-box-demo .ant-select {
  margin: 0;
}
```

### 定制回填内容

- demo: `option-label-prop`

使用 `optionLabelProp` 指定回填到选择框的 `Option` 属性。

```tsx
import { Select } from 'antd';
import React from 'react';

const { Option } = Select;

const handleChange = (value: string[]) => {
  console.log(`selected ${value}`);
};

const App: React.FC = () => (
  <Select
    mode="multiple"
    style={{ width: '100%' }}
    placeholder="select one country"
    defaultValue={['china']}
    onChange={handleChange}
    optionLabelProp="label"
  >
    <Option value="china" label="China">
      <div className="demo-option-label-item">
        <span role="img" aria-label="China">
          🇨🇳
        </span>
        China (中国)
      </div>
    </Option>
    <Option value="usa" label="USA">
      <div className="demo-option-label-item">
        <span role="img" aria-label="USA">
          🇺🇸
        </span>
        USA (美国)
      </div>
    </Option>
    <Option value="japan" label="Japan">
      <div className="demo-option-label-item">
        <span role="img" aria-label="Japan">
          🇯🇵
        </span>
        Japan (日本)
      </div>
    </Option>
    <Option value="korea" label="Korea">
      <div className="demo-option-label-item">
        <span role="img" aria-label="Korea">
          🇰🇷
        </span>
        Korea (韩国)
      </div>
    </Option>
  </Select>
);

export default App;
```

```css
.demo-option-label-item > span {
  margin-right: 6px;
}
```

### 带排序的搜索

- demo: `search-sort`

在搜索模式下对过滤结果项进行排序。

```tsx
import { Select } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Select
    showSearch
    style={{ width: 200 }}
    placeholder="Search to Select"
    optionFilterProp="children"
    filterOption={(input, option) => (option?.label ?? '').includes(input)}
    filterSort={(optionA, optionB) =>
      (optionA?.label ?? '').toLowerCase().localeCompare((optionB?.label ?? '').toLowerCase())
    }
    options={[
      {
        value: '1',
        label: 'Not Identified',
      },
      {
        value: '2',
        label: 'Closed',
      },
      {
        value: '3',
        label: 'Communicated',
      },
      {
        value: '4',
        label: 'Identified',
      },
      {
        value: '5',
        label: 'Resolved',
      },
      {
        value: '6',
        label: 'Cancelled',
      },
    ]}
  />
);

export default App;
```

### 标签

- demo: `tags`

tags select，随意输入的内容（scroll the menu）。

```tsx
import { Select } from 'antd';
import React from 'react';
import type { SelectProps } from 'antd';

const options: SelectProps['options'] = [];

for (let i = 10; i < 36; i++) {
  options.push({
    value: i.toString(36) + i,
    label: i.toString(36) + i,
  });
}

const handleChange = (value: string) => {
  console.log(`selected ${value}`);
};

const App: React.FC = () => (
  <Select
    mode="tags"
    style={{ width: '100%' }}
    placeholder="Tags Mode"
    onChange={handleChange}
    options={options}
  />
);

export default App;
```

### 分组

- demo: `optgroup`

用 `OptGroup` 进行选项分组。

```tsx
import { Select } from 'antd';
import React from 'react';

const handleChange = (value: string) => {
  console.log(`selected ${value}`);
};

const App: React.FC = () => (
  <Select
    defaultValue="lucy"
    style={{ width: 200 }}
    onChange={handleChange}
    options={[
      {
        label: 'Manager',
        options: [
          { label: 'Jack', value: 'jack' },
          { label: 'Lucy', value: 'lucy' },
        ],
      },
      {
        label: 'Engineer',
        options: [{ label: 'yiminghe', value: 'Yiminghe' }],
      },
    ]}
  />
);

export default App;
```

### 联动

- demo: `coordinate`

省市联动是典型的例子。

推荐使用 Cascader 组件。

```tsx
import { Select } from 'antd';
import React, { useState } from 'react';

const provinceData = ['Zhejiang', 'Jiangsu'];
const cityData = {
  Zhejiang: ['Hangzhou', 'Ningbo', 'Wenzhou'],
  Jiangsu: ['Nanjing', 'Suzhou', 'Zhenjiang'],
};

type CityName = keyof typeof cityData;

const App: React.FC = () => {
  const [cities, setCities] = useState(cityData[provinceData[0] as CityName]);
  const [secondCity, setSecondCity] = useState(cityData[provinceData[0] as CityName][0]);

  const handleProvinceChange = (value: CityName) => {
    setCities(cityData[value]);
    setSecondCity(cityData[value][0]);
  };

  const onSecondCityChange = (value: CityName) => {
    setSecondCity(value);
  };

  return (
    <>
      <Select
        defaultValue={provinceData[0]}
        style={{ width: 120 }}
        onChange={handleProvinceChange}
        options={provinceData.map(province => ({ label: province, value: province }))}
      />
      <Select
        style={{ width: 120 }}
        value={secondCity}
        onChange={onSecondCityChange}
        options={cities.map(city => ({ label: city, value: city }))}
      />
    </>
  );
};

export default App;
```

### 搜索框

- demo: `search-box`

搜索和远程数据结合。

```tsx
import React, { useState } from 'react';
import { Select } from 'antd';
import jsonp from 'fetch-jsonp';
import qs from 'qs';
import type { SelectProps } from 'antd';

let timeout: ReturnType<typeof setTimeout> | null;
let currentValue: string;

const fetch = (value: string, callback: Function) => {
  if (timeout) {
    clearTimeout(timeout);
    timeout = null;
  }
  currentValue = value;

  const fake = () => {
    const str = qs.stringify({
      code: 'utf-8',
      q: value,
    });
    jsonp(`https://suggest.taobao.com/sug?${str}`)
      .then((response: any) => response.json())
      .then((d: any) => {
        if (currentValue === value) {
          const { result } = d;
          const data = result.map((item: any) => ({
            value: item[0],
            text: item[0],
          }));
          callback(data);
        }
      });
  };

  timeout = setTimeout(fake, 300);
};

const SearchInput: React.FC<{ placeholder: string; style: React.CSSProperties }> = props => {
  const [data, setData] = useState<SelectProps['options']>([]);
  const [value, setValue] = useState<string>();

  const handleSearch = (newValue: string) => {
    if (newValue) {
      fetch(newValue, setData);
    } else {
      setData([]);
    }
  };

  const handleChange = (newValue: string) => {
    setValue(newValue);
  };

  return (
    <Select
      showSearch
      value={value}
      placeholder={props.placeholder}
      style={props.style}
      defaultActiveFirstOption={false}
      showArrow={false}
      filterOption={false}
      onSearch={handleSearch}
      onChange={handleChange}
      notFoundContent={null}
      options={(data || []).map(d => ({
        value: d.value,
        label: d.text,
      }))}
    />
  );
};

const App: React.FC = () => <SearchInput placeholder="input search text" style={{ width: 200 }} />;

export default App;
```

### 获得选项的文本

- demo: `label-in-value`

默认情况下 `onChange` 里只能拿到 `value`，如果需要拿到选中的节点文本 `label`，可以使用 `labelInValue` 属性。

选中项的 `label` 会被包装到 `value` 中传递给 `onChange` 等函数，此时 `value` 是一个对象。

```tsx
import { Select } from 'antd';
import React from 'react';

const handleChange = (value: { value: string; label: React.ReactNode }) => {
  console.log(value); // { value: "lucy", key: "lucy", label: "Lucy (101)" }
};

const App: React.FC = () => (
  <Select
    labelInValue
    defaultValue={{ value: 'lucy', label: 'Lucy (101)' }}
    style={{ width: 120 }}
    onChange={handleChange}
    options={[
      {
        value: 'jack',
        label: 'Jack (100)',
      },
      {
        value: 'lucy',
        label: 'Lucy (101)',
      },
    ]}
  />
);

export default App;
```

### 自动分词

- demo: `automatic-tokenization`

试下复制 `露西,杰克` 并粘贴到输入框里。只在 tags 和 multiple 模式下可用。

```tsx
import { Select } from 'antd';
import React from 'react';
import type { SelectProps } from 'antd';

const options: SelectProps['options'] = [];

for (let i = 10; i < 36; i++) {
  options.push({
    value: i.toString(36) + i,
    label: i.toString(36) + i,
  });
}

const handleChange = (value: string) => {
  console.log(`selected ${value}`);
};

const App: React.FC = () => (
  <Select
    mode="tags"
    style={{ width: '100%' }}
    onChange={handleChange}
    tokenSeparators={[',']}
    options={options}
  />
);

export default App;
```

### 搜索用户

- demo: `select-users`

一个带有远程搜索，防抖控制，请求时序控制，加载状态的多选示例。

```tsx
import { Select, Spin } from 'antd';
import type { SelectProps } from 'antd/es/select';
import debounce from 'lodash/debounce';
import React, { useMemo, useRef, useState } from 'react';

export interface DebounceSelectProps<ValueType = any>
  extends Omit<SelectProps<ValueType | ValueType[]>, 'options' | 'children'> {
  fetchOptions: (search: string) => Promise<ValueType[]>;
  debounceTimeout?: number;
}

function DebounceSelect<
  ValueType extends { key?: string; label: React.ReactNode; value: string | number } = any,
>({ fetchOptions, debounceTimeout = 800, ...props }: DebounceSelectProps<ValueType>) {
  const [fetching, setFetching] = useState(false);
  const [options, setOptions] = useState<ValueType[]>([]);
  const fetchRef = useRef(0);

  const debounceFetcher = useMemo(() => {
    const loadOptions = (value: string) => {
      fetchRef.current += 1;
      const fetchId = fetchRef.current;
      setOptions([]);
      setFetching(true);

      fetchOptions(value).then(newOptions => {
        if (fetchId !== fetchRef.current) {
          // for fetch callback order
          return;
        }

        setOptions(newOptions);
        setFetching(false);
      });
    };

    return debounce(loadOptions, debounceTimeout);
  }, [fetchOptions, debounceTimeout]);

  return (
    <Select
      labelInValue
      filterOption={false}
      onSearch={debounceFetcher}
      notFoundContent={fetching ? <Spin size="small" /> : null}
      {...props}
      options={options}
    />
  );
}

// Usage of DebounceSelect
interface UserValue {
  label: string;
  value: string;
}

async function fetchUserList(username: string): Promise<UserValue[]> {
  console.log('fetching user', username);

  return fetch('https://randomuser.me/api/?results=5')
    .then(response => response.json())
    .then(body =>
      body.results.map(
        (user: { name: { first: string; last: string }; login: { username: string } }) => ({
          label: `${user.name.first} ${user.name.last}`,
          value: user.login.username,
        }),
      ),
    );
}

const App: React.FC = () => {
  const [value, setValue] = useState<UserValue[]>([]);

  return (
    <DebounceSelect
      mode="multiple"
      value={value}
      placeholder="Select users"
      fetchOptions={fetchUserList}
      onChange={newValue => {
        setValue(newValue as UserValue[]);
      }}
      style={{ width: '100%' }}
    />
  );
};

export default App;
```

### 扩展菜单

- demo: `custom-dropdown-menu`

使用 `open` 对下拉菜单进行自由扩展。如果希望点击自定义内容后关闭浮层，你需要使用受控模式自行控制（codesandbox）。

```tsx
import { PlusOutlined } from '@ant-design/icons';
import { Divider, Input, Select, Space, Button } from 'antd';
import type { InputRef } from 'antd';
import React, { useState, useRef } from 'react';

let index = 0;

const App: React.FC = () => {
  const [items, setItems] = useState(['jack', 'lucy']);
  const [name, setName] = useState('');
  const inputRef = useRef<InputRef>(null);

  const onNameChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setName(event.target.value);
  };

  const addItem = (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    setItems([...items, name || `New item ${index++}`]);
    setName('');
    setTimeout(() => {
      inputRef.current?.focus();
    }, 0);
  };

  return (
    <Select
      style={{ width: 300 }}
      placeholder="custom dropdown render"
      dropdownRender={menu => (
        <>
          {menu}
          <Divider style={{ margin: '8px 0' }} />
          <Space style={{ padding: '0 8px 4px' }}>
            <Input
              placeholder="Please enter item"
              ref={inputRef}
              value={name}
              onChange={onNameChange}
            />
            <Button type="text" icon={<PlusOutlined />} onClick={addItem}>
              Add item
            </Button>
          </Space>
        </>
      )}
      options={items.map(item => ({ label: item, value: item }))}
    />
  );
};

export default App;
```

### 隐藏已选择选项

- demo: `hide-selected`

隐藏下拉列表中已选择的选项。

```tsx
import { Select } from 'antd';
import React, { useState } from 'react';

const OPTIONS = ['Apples', 'Nails', 'Bananas', 'Helicopters'];

const App: React.FC = () => {
  const [selectedItems, setSelectedItems] = useState<string[]>([]);

  const filteredOptions = OPTIONS.filter(o => !selectedItems.includes(o));

  return (
    <Select
      mode="multiple"
      placeholder="Inserted are removed"
      value={selectedItems}
      onChange={setSelectedItems}
      style={{ width: '100%' }}
      options={filteredOptions.map(item => ({
        value: item,
        label: item,
      }))}
    />
  );
};

export default App;
```

### 无边框

- demo: `bordered`

无边框样式。

```tsx
import { Select } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Select
      defaultValue="lucy"
      style={{ width: 120 }}
      bordered={false}
      options={[
        {
          value: 'jack',
          label: 'Jack',
        },
        {
          value: 'lucy',
          label: 'Lucy',
        },
        {
          value: 'Yiminghe',
          label: 'yiminghe',
        },
      ]}
    />
    <Select
      defaultValue="lucy"
      style={{ width: 120 }}
      disabled
      bordered={false}
      options={[
        {
          value: 'lucy',
          label: 'Lucy',
        },
      ]}
    />
  </>
);

export default App;
```

### 自定义选择标签

- demo: `custom-tag-render`

允许自定义选择标签的样式。

```tsx
import { Select, Tag } from 'antd';
import type { CustomTagProps } from 'rc-select/lib/BaseSelect';
import React from 'react';

const options = [{ value: 'gold' }, { value: 'lime' }, { value: 'green' }, { value: 'cyan' }];

const tagRender = (props: CustomTagProps) => {
  const { label, value, closable, onClose } = props;
  const onPreventMouseDown = (event: React.MouseEvent<HTMLSpanElement>) => {
    event.preventDefault();
    event.stopPropagation();
  };
  return (
    <Tag
      color={value}
      onMouseDown={onPreventMouseDown}
      closable={closable}
      onClose={onClose}
      style={{ marginRight: 3 }}
    >
      {label}
    </Tag>
  );
};

const App: React.FC = () => (
  <Select
    mode="multiple"
    showArrow
    tagRender={tagRender}
    defaultValue={['gold', 'cyan']}
    style={{ width: '100%' }}
    options={options}
  />
);

export default App;
```

### 响应式 maxTagCount

- demo: `responsive`

多选下通过响应式布局让选项自动收缩。该功能对性能有所消耗，不推荐在大表单场景下使用。

```tsx
import type { SelectProps } from 'antd';
import { Select, Space } from 'antd';
import React, { useState } from 'react';

interface ItemProps {
  label: string;
  value: string;
}

const options: ItemProps[] = [];

for (let i = 10; i < 36; i++) {
  const value = i.toString(36) + i;
  options.push({
    label: `Long Label: ${value}`,
    value,
  });
}

const App: React.FC = () => {
  const [value, setValue] = useState(['a10', 'c12', 'h17', 'j19', 'k20']);

  const selectProps: SelectProps = {
    mode: 'multiple',
    style: { width: '100%' },
    value,
    options,
    onChange: (newValue: string[]) => {
      setValue(newValue);
    },
    placeholder: 'Select Item...',
    maxTagCount: 'responsive',
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Select {...selectProps} />
      <Select {...selectProps} disabled />
    </Space>
  );
};

export default App;
```

### 大数据

- demo: `big-data`

Select 使用了虚拟滚动技术，因而获得了比 3.0 更好的性能。

```tsx
import type { SelectProps } from 'antd';
import { Divider, Select, Typography } from 'antd';
import React from 'react';

const { Title } = Typography;

const options: SelectProps['options'] = [];
for (let i = 0; i < 100000; i++) {
  const value = `${i.toString(36)}${i}`;
  options.push({
    label: value,
    value,
    disabled: i === 10,
  });
}

const handleChange = (value: string[]) => {
  console.log(`selected ${value}`);
};

const App: React.FC = () => (
  <>
    <Title level={3}>Ant Design 4.0</Title>
    <Title level={4}>{options.length} Items</Title>
    <Select
      mode="multiple"
      style={{ width: '100%' }}
      placeholder="Please select"
      defaultValue={['a10', 'c12']}
      onChange={handleChange}
      options={options}
    />

    <Divider />

    <Title level={3}>Ant Design 3.0</Title>
    <iframe
      title="Ant Design 3.0 Select demo"
      src="https://codesandbox.io/embed/solitary-voice-m3vme?fontsize=14&hidenavigation=1&theme=dark&view=preview"
      style={{ width: '100%', height: 300 }}
    />
  </>
);

export default App;
```

### 自定义状态

- demo: `status`

使用 `status` 为 Select 添加状态，可选 `error` 或者 `warning`。

```tsx
import { Select, Space } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Space direction="vertical" style={{ width: '100%' }}>
    <Select status="error" style={{ width: '100%' }} />
    <Select status="warning" style={{ width: '100%' }} />
  </Space>
);

export default App;
```

```css
#components-select-demo-status .ant-select {
  margin: 0;
}
```

### 弹出位置

- demo: `placement`

可以通过 `placement` 手动指定弹出的位置。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Radio, Select } from 'antd';
import type { SelectCommonPlacement } from 'antd/es/_util/motion';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [placement, SetPlacement] = useState<SelectCommonPlacement>('topLeft');

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
      <Select
        defaultValue="HangZhou"
        style={{ width: 120 }}
        dropdownMatchSelectWidth={false}
        placement={placement}
        options={[
          {
            value: 'HangZhou',
            label: 'HangZhou #310000',
          },
          {
            value: 'NingBo',
            label: 'NingBo #315000',
          },
          {
            value: 'WenZhou',
            label: 'WenZhou #325000',
          },
        ]}
      />
    </>
  );
};

export default App;
```
