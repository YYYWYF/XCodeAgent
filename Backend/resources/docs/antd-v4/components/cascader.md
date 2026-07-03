---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "cascader"
component: "Cascader"
component_code_name: "Cascader"
recommended_import: "import { Cascader } from 'antd';"
subtitle_zh: "级联选择"
type_zh: "数据录入"
demo_count: 16
---

# Cascader 级联选择

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Cascader`
- 推荐导入：`import { Cascader } from 'antd';`
- 组件分类：数据录入
- 简述：级联选择框。

## 示例索引

- 基本（basic）
- 默认值（default-value）
- 可以自定义显示（custom-trigger）
- 移入展开（hover）
- 禁用选项（disabled-option）
- 选择即改变（change-on-select）
- 多选（multiple）
- 自定义回填方式（showCheckedStrategy）
- 大小（size）
- 自定义已选项（custom-render）
- 搜索（search）
- 动态加载选项（lazy）
- 自定义字段名（fields-name）
- 扩展菜单（custom-dropdown）
- 弹出位置（placement）
- 自定义状态（status）

## 使用文档

级联选择框。

### 何时使用

- 需要从一组相关联的数据集合进行选择，例如省市区，公司层级，事物分类等。
- 从一个较大的数据集合中进行选择时，用多级分类进行分隔，方便选择。
- 比起 Select 组件，可以在同一个浮层中完成选择，有较好的体验。

### API

```jsx
<Cascader options={options} onChange={onChange} />
```

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| allowClear | 是否支持清除 | boolean | true |  |
| autoFocus | 自动获取焦点 | boolean | false |  |
| bordered | 是否有边框 | boolean | true |  |
| clearIcon | 自定义的选择框清空图标 | ReactNode | - |  |
| changeOnSelect | （单选时生效）当此项为 true 时，点选每级菜单选项值都会发生变化，具体见上面的演示 | boolean | false |  |
| className | 自定义类名 | string | - |  |
| defaultValue | 默认的选中项 | string\[] \| number\[] | \[] |  |
| disabled | 禁用 | boolean | false |  |
| displayRender | 选择后展示的渲染函数 | (label, selectedOptions) => ReactNode | label => label.join(`/`) | `multiple`: 4.18.0 |
| popupClassName | 自定义浮层类名 | string | - | 4.23.0 |
| dropdownRender | 自定义下拉框内容 | (menus: ReactNode) => ReactNode | - | 4.4.0 |
| expandIcon | 自定义次级菜单展开图标 | ReactNode | - | 4.4.0 |
| expandTrigger | 次级菜单的展开方式，可选 'click' 和 'hover' | string | `click` |  |
| fieldNames | 自定义 options 中 label value children 的字段 | object | { label: `label`, value: `value`, children: `children` } |  |
| getPopupContainer | 菜单渲染父节点。默认渲染到 body 上，如果你遇到菜单滚动定位问题，试试修改为滚动的区域，并相对其定位。示例 | function(triggerNode) | () => document.body |  |
| loadData | 用于动态加载选项，无法与 `showSearch` 一起使用 | (selectedOptions) => void | - |  |
| maxTagCount | 最多显示多少个 tag，响应式模式会对性能产生损耗 | number \| `responsive` | - | 4.17.0 |
| maxTagPlaceholder | 隐藏 tag 时显示的内容 | ReactNode \| function(omittedValues) | - | 4.17.0 |
| notFoundContent | 当下拉列表为空时显示的内容 | string | `Not Found` |  |
| open | 控制浮层显隐 | boolean | - | 4.17.0 |
| options | 可选项数据源 | Option\[] | - |  |
| placeholder | 输入框占位文本 | string | `请选择` |  |
| placement | 浮层预设位置 | `bottomLeft` `bottomRight` `topLeft` `topRight` | `bottomLeft` | 4.17.0 |
| showSearch | 在选择框中显示搜索框 | boolean \| Object | false |  |
| size | 输入框大小 | `large` \| `middle` \| `small` | - |  |
| status | 设置校验状态 | 'error' \| 'warning' | - | 4.19.0 |
| style | 自定义样式 | CSSProperties | - |  |
| suffixIcon | 自定义的选择框后缀图标 | ReactNode | - |  |
| value | 指定选中项 | string\[] \| number\[] | - |  |
| onChange | 选择完成后的回调 | (value, selectedOptions) => void | - |  |
| onDropdownVisibleChange | 显示/隐藏浮层的回调 | (value) => void | - | 4.17.0 |
| multiple | 支持多选节点 | boolean | - | 4.17.0 |
| showCheckedStrategy | 定义选中项回填的方式。`Cascader.SHOW_CHILD`: 只显示选中的子节点。`Cascader.SHOW_PARENT`: 只显示父节点（当父节点下所有子节点都选中时）。 | `Cascader.SHOW_PARENT` \| `Cascader.SHOW_CHILD` | `Cascader.SHOW_PARENT` | 4.20.0 |
| removeIcon | 自定义的多选框清除图标 | ReactNode | - |  |
| searchValue | 设置搜索的值，需要与 `showSearch` 配合使用 | string | - | 4.17.0 |
| onSearch | 监听搜索，返回输入的值 | (search: string) => void | - | 4.17.0 |
| dropdownMenuColumnStyle | 下拉菜单列的样式 | CSSProperties | - |  |

#### showSearch

`showSearch` 为对象时，其中的字段：

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| filter | 接收 `inputValue` `path` 两个参数，当 `path` 符合筛选条件时，应返回 true，反之则返回 false | function(inputValue, path): boolean | - |  |
| limit | 搜索结果展示数量 | number \| false | 50 |  |
| matchInputWidth | 搜索结果列表是否与输入框同宽（效果） | boolean | true |  |
| render | 用于渲染 filter 后的选项 | function(inputValue, path): ReactNode | - |  |
| sort | 用于排序 filter 后的选项 | function(a, b, inputValue) | - |  |

#### Option

```typescript
interface Option {
  value: string | number;
  label?: React.ReactNode;
  disabled?: boolean;
  children?: Option[];
  // 标记是否为叶子节点，设置了 `loadData` 时有效
  // 设为 `false` 时会强制标记为父节点，即使当前节点没有 children，也会显示展开图标
  isLeaf?: boolean;
}
```

### 方法

| 名称    | 描述     | 版本 |
| ------- | -------- | ---- |
| blur()  | 移除焦点 |      |
| focus() | 获取焦点 |      |

> 注意，如果需要获得中国省市区数据，可以参考 china-division。

## 示例代码

### 基本

- demo: `basic`

省市区级联。

```tsx
import { Cascader } from 'antd';
import React from 'react';

interface Option {
  value: string | number;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[]) => {
  console.log(value);
};

const App: React.FC = () => (
  <Cascader options={options} onChange={onChange} placeholder="Please select" />
);

export default App;
```

### 默认值

- demo: `default-value`

默认值通过数组的方式指定。

```tsx
import { Cascader } from 'antd';
import React from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[]) => {
  console.log(value);
};

const App: React.FC = () => (
  <Cascader defaultValue={['zhejiang', 'hangzhou', 'xihu']} options={options} onChange={onChange} />
);

export default App;
```

### 可以自定义显示

- demo: `custom-trigger`

切换按钮和结果分开。

```tsx
import { Cascader } from 'antd';
import React, { useState } from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
      },
    ],
  },
];

const App: React.FC = () => {
  const [text, setText] = useState('Unselect');

  const onChange = (_: string[], selectedOptions: Option[]) => {
    setText(selectedOptions.map(o => o.label).join(', '));
  };

  return (
    <span>
      {text}
      &nbsp;
      <Cascader options={options} onChange={onChange}>
        <a href="#">Change city</a>
      </Cascader>
    </span>
  );
};

export default App;
```

### 移入展开

- demo: `hover`

通过移入展开下级菜单，点击完成选择。

```tsx
import { Cascader } from 'antd';
import React from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[]) => {
  console.log(value);
};

// Just show the latest item.
const displayRender = (labels: string[]) => labels[labels.length - 1];

const App: React.FC = () => (
  <Cascader
    options={options}
    expandTrigger="hover"
    displayRender={displayRender}
    onChange={onChange}
  />
);

export default App;
```

### 禁用选项

- demo: `disabled-option`

通过指定 options 里的 `disabled` 字段。

```tsx
import { Cascader } from 'antd';
import React from 'react';

interface Option {
  value: string;
  label: string;
  disabled?: boolean;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    disabled: true,
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[]) => {
  console.log(value);
};

const App: React.FC = () => <Cascader options={options} onChange={onChange} />;

export default App;
```

### 选择即改变

- demo: `change-on-select`

这种交互允许只选中父级选项。

```tsx
import { Cascader } from 'antd';
import React from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hanzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[]) => {
  console.log(value);
};

const App: React.FC = () => <Cascader options={options} onChange={onChange} changeOnSelect />;

export default App;
```

### 多选

- demo: `multiple`

一次性选择多个选项。

```tsx
import { Cascader } from 'antd';
import React from 'react';

interface Option {
  value: string | number;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    label: 'Light',
    value: 'light',
    children: new Array(20)
      .fill(null)
      .map((_, index) => ({ label: `Number ${index}`, value: index })),
  },
  {
    label: 'Bamboo',
    value: 'bamboo',
    children: [
      {
        label: 'Little',
        value: 'little',
        children: [
          {
            label: 'Toy Fish',
            value: 'fish',
          },
          {
            label: 'Toy Cards',
            value: 'cards',
          },
          {
            label: 'Toy Bird',
            value: 'bird',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[][]) => {
  console.log(value);
};

const App: React.FC = () => (
  <Cascader
    style={{ width: '100%' }}
    options={options}
    onChange={onChange}
    multiple
    maxTagCount="responsive"
  />
);

export default App;
```

### 自定义回填方式

- demo: `showCheckedStrategy`

通过设置 `ShowCheckedStrategy` 选择回填方式。

```tsx
import { Cascader } from 'antd';

const { SHOW_CHILD } = Cascader;

interface Option {
  value: string | number;
  label: string;
  children?: Option[];
}
const options: Option[] = [
  {
    label: 'Light',
    value: 'light',
    children: new Array(20)
      .fill(null)
      .map((_, index) => ({ label: `Number ${index}`, value: index })),
  },
  {
    label: 'Bamboo',
    value: 'bamboo',
    children: [
      {
        label: 'Little',
        value: 'little',
        children: [
          {
            label: 'Toy Fish',
            value: 'fish',
          },
          {
            label: 'Toy Cards',
            value: 'cards',
          },
          {
            label: 'Toy Bird',
            value: 'bird',
          },
        ],
      },
    ],
  },
];

const App: React.FC = () => {
  const onChange = (value: string[][]) => {
    console.log(value);
  };
  return (
    <>
      <Cascader
        style={{ width: '100%' }}
        options={options}
        onChange={onChange}
        multiple
        maxTagCount="responsive"
        showCheckedStrategy={SHOW_CHILD}
        defaultValue={[
          ['bamboo', 'little', 'fish'],
          ['bamboo', 'little', 'cards'],
          ['bamboo', 'little', 'bird'],
        ]}
      />
      <br />
      <br />
      <Cascader
        style={{ width: '100%' }}
        options={options}
        onChange={onChange}
        multiple
        maxTagCount="responsive"
        defaultValue={['bamboo']}
      />
    </>
  );
};

export default App;
```

### 大小

- demo: `size`

不同大小的级联选择器。

```tsx
import { Cascader } from 'antd';
import React from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[]) => {
  console.log(value);
};

const App: React.FC = () => (
  <>
    <Cascader size="large" options={options} onChange={onChange} />
    <br />
    <br />
    <Cascader options={options} onChange={onChange} />
    <br />
    <br />
    <Cascader size="small" options={options} onChange={onChange} />
    <br />
    <br />
  </>
);

export default App;
```

### 自定义已选项

- demo: `custom-render`

例如给最后一项加上邮编链接。

```tsx
import { Cascader } from 'antd';
import type { DefaultOptionType } from 'antd/es/cascader';
import React from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
  code?: number;
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
            code: 752100,
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
            code: 453400,
          },
        ],
      },
    ],
  },
];

const handleAreaClick = (
  e: React.MouseEvent<HTMLAnchorElement>,
  label: string,
  option: DefaultOptionType,
) => {
  e.stopPropagation();
  console.log('clicked', label, option);
};

const displayRender = (labels: string[], selectedOptions: DefaultOptionType[]) =>
  labels.map((label, i) => {
    const option = selectedOptions[i];
    if (i === labels.length - 1) {
      return (
        <span key={option.value}>
          {label} (<a onClick={e => handleAreaClick(e, label, option)}>{option.code}</a>)
        </span>
      );
    }
    return <span key={option.value}>{label} / </span>;
  });

const App: React.FC = () => (
  <Cascader
    options={options}
    defaultValue={['zhejiang', 'hangzhou', 'xihu']}
    displayRender={displayRender}
    style={{ width: '100%' }}
  />
);

export default App;
```

### 搜索

- demo: `search`

可以直接搜索选项并选择。

> `Cascader[showSearch]` 暂不支持服务端搜索，更多信息见 #5547

```tsx
import { Cascader } from 'antd';
import type { DefaultOptionType } from 'antd/es/cascader';
import React from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
  disabled?: boolean;
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
          {
            value: 'xiasha',
            label: 'Xia Sha',
            disabled: true,
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua men',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[], selectedOptions: Option[]) => {
  console.log(value, selectedOptions);
};

const filter = (inputValue: string, path: DefaultOptionType[]) =>
  path.some(
    option => (option.label as string).toLowerCase().indexOf(inputValue.toLowerCase()) > -1,
  );

const App: React.FC = () => (
  <Cascader
    options={options}
    onChange={onChange}
    placeholder="Please select"
    showSearch={{ filter }}
    onSearch={value => console.log(value)}
  />
);

export default App;
```

### 动态加载选项

- demo: `lazy`

使用 `loadData` 实现动态加载选项。

> 注意：`loadData` 与 `showSearch` 无法一起使用。

```tsx
import { Cascader } from 'antd';
import React, { useState } from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
  isLeaf?: boolean;
  loading?: boolean;
}

const optionLists: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    isLeaf: false,
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    isLeaf: false,
  },
];

const App: React.FC = () => {
  const [options, setOptions] = useState<Option[]>(optionLists);

  const onChange = (value: string[], selectedOptions: Option[]) => {
    console.log(value, selectedOptions);
  };

  const loadData = (selectedOptions: Option[]) => {
    const targetOption = selectedOptions[selectedOptions.length - 1];
    targetOption.loading = true;

    // load options lazily
    setTimeout(() => {
      targetOption.loading = false;
      targetOption.children = [
        {
          label: `${targetOption.label} Dynamic 1`,
          value: 'dynamic1',
        },
        {
          label: `${targetOption.label} Dynamic 2`,
          value: 'dynamic2',
        },
      ];
      setOptions([...options]);
    }, 1000);
  };

  return <Cascader options={options} loadData={loadData} onChange={onChange} changeOnSelect />;
};

export default App;
```

### 自定义字段名

- demo: `fields-name`

自定义字段名。

```tsx
import { Cascader } from 'antd';
import React from 'react';

interface Option {
  code: string;
  name: string;
  items?: Option[];
}

const options: Option[] = [
  {
    code: 'zhejiang',
    name: 'Zhejiang',
    items: [
      {
        code: 'hangzhou',
        name: 'Hangzhou',
        items: [
          {
            code: 'xihu',
            name: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    code: 'jiangsu',
    name: 'Jiangsu',
    items: [
      {
        code: 'nanjing',
        name: 'Nanjing',
        items: [
          {
            code: 'zhonghuamen',
            name: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const onChange = (value: string[]) => {
  console.log(value);
};

const App: React.FC = () => (
  <Cascader
    fieldNames={{ label: 'name', value: 'code', children: 'items' }}
    options={options}
    onChange={onChange}
    placeholder="Please select"
  />
);

export default App;
```

### 扩展菜单

- demo: `custom-dropdown`

使用 `dropdownRender` 对下拉菜单进行自由扩展。

```tsx
import { Cascader, Divider } from 'antd';
import React from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const dropdownRender = (menus: React.ReactNode) => (
  <div>
    {menus}
    <Divider style={{ margin: 0 }} />
    <div style={{ padding: 8 }}>The footer is not very short.</div>
  </div>
);

const App: React.FC = () => (
  <Cascader options={options} dropdownRender={dropdownRender} placeholder="Please select" />
);

export default App;
```

### 弹出位置

- demo: `placement`

可以通过 `placement` 手动指定弹出的位置。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Cascader, Radio } from 'antd';
import React, { useState } from 'react';

interface Option {
  value: string;
  label: string;
  children?: Option[];
}

const options: Option[] = [
  {
    value: 'zhejiang',
    label: 'Zhejiang',
    children: [
      {
        value: 'hangzhou',
        label: 'Hangzhou',
        children: [
          {
            value: 'xihu',
            label: 'West Lake',
          },
        ],
      },
    ],
  },
  {
    value: 'jiangsu',
    label: 'Jiangsu',
    children: [
      {
        value: 'nanjing',
        label: 'Nanjing',
        children: [
          {
            value: 'zhonghuamen',
            label: 'Zhong Hua Men',
          },
        ],
      },
    ],
  },
];

const App: React.FC = () => {
  const [placement, SetPlacement] = useState<'bottomLeft' | 'bottomRight' | 'topLeft' | 'topRight'>(
    'topLeft',
  );

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
      <Cascader options={options} placeholder="Please select" placement={placement} />
    </>
  );
};

export default App;
```

### 自定义状态

- demo: `status`

使用 `status` 为 Cascader 添加状态，可选 `error` 或者 `warning`。

```tsx
import { Cascader, Space } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Space direction="vertical">
    <Cascader status="error" placeholder="Error" />
    <Cascader status="warning" multiple placeholder="Warning multiple" />
  </Space>
);

export default App;
```
