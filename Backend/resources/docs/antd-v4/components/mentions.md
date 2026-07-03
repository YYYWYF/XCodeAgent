---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "mentions"
component: "Mentions"
component_code_name: "Mentions"
recommended_import: "import { Mentions } from 'antd';"
subtitle_zh: "提及"
type_zh: "数据录入"
demo_count: 8
---

# Mentions 提及

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Mentions`
- 推荐导入：`import { Mentions } from 'antd';`
- 组件分类：数据录入
- 简述：提及组件。

## 示例索引

- 基本使用（basic）
- 异步加载（async）
- 配合 Form 使用（form）
- 自定义触发字符（prefix）
- 无效或只读（readonly）
- 向上展开（placement）
- 自动大小（autoSize）
- 自定义状态（status）

## 使用文档

提及组件。

### 何时使用

用于在输入中提及某人或某事，常用于发布、聊天或评论功能。

#### 4.24.4 用法升级

```__react
import Alert from '../alert';
ReactDOM.render(<Alert message="在 4.24.4 版本后，我们提供了 <Mentions options={[...]} /> 的简写方式，有更好的性能和更方便的数据组织方式，开发者不再需要自行拼接 JSX。同时我们废弃了原先的写法，你还是可以在 4.x 继续使用，但会在控制台看到警告，并会在 5.0 后移除。" />, mountNode);
```

```jsx
// >=4.24.4 可用，推荐的写法 ✅
const options = [{ value: 'sample', label: 'sample' }];
return <Mentions options={options} />;

// <4.24.4 可用，>=4.24.4 时不推荐 🙅🏻‍♀️
<Mentions onChange={onChange}>
  <Mentions.Option value="sample">Sample</Mentions.Option>
</Mentions>;
```

### API

#### Mentions

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| autoFocus | 自动获得焦点 | boolean | false |  |
| autoSize | 自适应内容高度，可设置为 true \| false 或对象：{ minRows: 2, maxRows: 6 } | boolean \| object | false |  |
| defaultValue | 默认值 | string | - |  |
| filterOption | 自定义过滤逻辑 | false \| (input: string, option: OptionProps) => boolean | - |  |
| getPopupContainer | 指定建议框挂载的 HTML 节点 | () => HTMLElement | - |  |
| notFoundContent | 当下拉列表为空时显示的内容 | ReactNode | `Not Found` |  |
| placement | 弹出层展示位置 | `top` \| `bottom` | `bottom` |  |
| prefix | 设置触发关键字 | string \| string\[] | `@` |  |
| split | 设置选中项前后分隔符 | string | ` ` |  |
| status | 设置校验状态 | 'error' \| 'warning' | - | 4.19.0 |
| validateSearch | 自定义触发验证逻辑 | (text: string, props: MentionsProps) => void | - |  |
| value | 设置值 | string | - |  |
| onBlur | 失去焦点时触发 | () => void | - |  |
| onChange | 值改变时触发 | (text: string) => void | - |  |
| onFocus | 获得焦点时触发 | () => void | - |  |
| onResize | resize 回调 | function({ width, height }) | - |  |
| onSearch | 搜索时触发 | (text: string, prefix: string) => void | - |  |
| onSelect | 选择选项时触发 | (option: OptionProps, prefix: string) => void | - |  |
| options | 选项配置 | Options | [] | 4.24.4 |

#### Mentions 方法

| 名称    | 描述     |
| ------- | -------- |
| blur()  | 移除焦点 |
| focus() | 获取焦点 |

#### Option

| 参数      | 说明           | 类型                | 默认值 |
| --------- | -------------- | ------------------- | ------ |
| value     | 选择时填充的值 | string              | -      |
| label     | 选项的标题     | React.ReactNode     | -      |
| key       | 选项的 key 值  | string              | -      |
| disabled  | 是否可选       | boolean             | -      |
| className | css 类名       | string              | -      |
| style     | 选项样式       | React.CSSProperties | -      |

## 示例代码

### 基本使用

- demo: `basic`

基本使用。

```tsx
import React from 'react';
import { Mentions } from 'antd';
import type { MentionsOptionProps } from 'antd/es/mentions';

const onChange = (value: string) => {
  console.log('Change:', value);
};

const onSelect = (option: MentionsOptionProps) => {
  console.log('select', option);
};

const App: React.FC = () => (
  <Mentions
    style={{ width: '100%' }}
    onChange={onChange}
    onSelect={onSelect}
    defaultValue="@afc163"
    options={[
      {
        value: 'afc163',
        label: 'afc163',
      },
      {
        value: 'zombieJ',
        label: 'zombieJ',
      },
      {
        value: 'yesmeck',
        label: 'yesmeck',
      },
    ]}
  />
);

export default App;
```

### 异步加载

- demo: `async`

匹配内容列表为异步返回时。

```tsx
import React, { useCallback, useRef, useState } from 'react';
import { Mentions } from 'antd';
import debounce from 'lodash/debounce';

const App: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [users, setUsers] = useState<{ login: string; avatar_url: string }[]>([]);
  const ref = useRef<string>();

  const loadGithubUsers = (key: string) => {
    if (!key) {
      setUsers([]);
      return;
    }

    fetch(`https://api.github.com/search/users?q=${key}`)
      .then(res => res.json())
      .then(({ items = [] }) => {
        if (ref.current !== key) return;

        setLoading(false);
        setUsers(items.slice(0, 10));
      });
  };

  const debounceLoadGithubUsers = useCallback(debounce(loadGithubUsers, 800), []);

  const onSearch = (search: string) => {
    console.log('Search:', search);
    ref.current = search;
    setLoading(!!search);
    setUsers([]);

    debounceLoadGithubUsers(search);
  };

  return (
    <Mentions
      style={{ width: '100%' }}
      loading={loading}
      onSearch={onSearch}
      options={users.map(({ login, avatar_url: avatar }) => ({
        key: login,
        value: login,
        className: 'antd-demo-dynamic-option',
        label: (
          <>
            <img src={avatar} alt={login} />
            <span>{login}</span>
          </>
        ),
      }))}
    />
  );
};

export default App;
```

### 配合 Form 使用

- demo: `form`

受控模式，例如配合 Form 使用。

```tsx
import React from 'react';
import { Button, Form, Mentions } from 'antd';

const { getMentions } = Mentions;

const App: React.FC = () => {
  const [form] = Form.useForm();

  const onReset = () => {
    form.resetFields();
  };

  const onFinish = async () => {
    try {
      const values = await form.validateFields();
      console.log('Submit:', values);
    } catch (errInfo) {
      console.log('Error:', errInfo);
    }
  };

  const checkMention = async (_: any, value: string) => {
    const mentions = getMentions(value);

    if (mentions.length < 2) {
      throw new Error('More than one must be selected!');
    }
  };

  return (
    <Form form={form} layout="horizontal" onFinish={onFinish}>
      <Form.Item
        name="coders"
        label="Top coders"
        labelCol={{ span: 6 }}
        wrapperCol={{ span: 16 }}
        rules={[{ validator: checkMention }]}
      >
        <Mentions
          rows={1}
          options={[
            {
              value: 'afc163',
              label: 'afc163',
            },
            {
              value: 'zombieJ',
              label: 'zombieJ',
            },
            {
              value: 'yesmeck',
              label: 'yesmeck',
            },
          ]}
        />
      </Form.Item>
      <Form.Item
        name="bio"
        label="Bio"
        labelCol={{ span: 6 }}
        wrapperCol={{ span: 16 }}
        rules={[{ required: true }]}
      >
        <Mentions
          rows={3}
          placeholder="You can use @ to ref user here"
          options={[
            {
              value: 'afc163',
              label: 'afc163',
            },
            {
              value: 'zombieJ',
              label: 'zombieJ',
            },
            {
              value: 'yesmeck',
              label: 'yesmeck',
            },
          ]}
        />
      </Form.Item>
      <Form.Item wrapperCol={{ span: 14, offset: 6 }}>
        <Button htmlType="submit" type="primary">
          Submit
        </Button>
        &nbsp;&nbsp;&nbsp;
        <Button htmlType="button" onClick={onReset}>
          Reset
        </Button>
      </Form.Item>
    </Form>
  );
};

export default App;
```

### 自定义触发字符

- demo: `prefix`

通过 `prefix` 属性自定义触发字符。默认为 `@`, 可以定义为数组。

```tsx
import React, { useState } from 'react';
import { Mentions } from 'antd';

const MOCK_DATA = {
  '@': ['afc163', 'zombiej', 'yesmeck'],
  '#': ['1.0', '2.0', '3.0'],
};

type PrefixType = keyof typeof MOCK_DATA;

const App: React.FC = () => {
  const [prefix, setPrefix] = useState<PrefixType>('@');

  const onSearch = (_: string, newPrefix: PrefixType) => {
    setPrefix(newPrefix);
  };

  return (
    <Mentions
      style={{ width: '100%' }}
      placeholder="input @ to mention people, # to mention tag"
      prefix={['@', '#']}
      onSearch={onSearch}
      options={(MOCK_DATA[prefix] || []).map(value => ({
        key: value,
        value,
        label: value,
      }))}
    />
  );
};

export default App;
```

### 无效或只读

- demo: `readonly`

通过 `disabled` 属性设置是否生效。通过 `readOnly` 属性设置是否只读。

```tsx
import React from 'react';
import { Mentions } from 'antd';

const options = ['afc163', 'zombiej', 'yesmeck'].map(value => ({
  value,
  key: value,
  label: value,
}));

const App: React.FC = () => (
  <>
    <div style={{ marginBottom: 10 }}>
      <Mentions
        style={{ width: '100%' }}
        placeholder="this is disabled Mentions"
        disabled
        options={options}
      />
    </div>
    <Mentions
      style={{ width: '100%' }}
      placeholder="this is readOnly Mentions"
      readOnly
      options={options}
    />
  </>
);

export default App;
```

### 向上展开

- demo: `placement`

向上展开建议。

```tsx
import React from 'react';
import { Mentions } from 'antd';

const App: React.FC = () => (
  <Mentions
    style={{ width: '100%' }}
    placement="top"
    options={[
      {
        value: 'afc163',
        label: 'afc163',
      },
      {
        value: 'zombieJ',
        label: 'zombieJ',
      },
      {
        value: 'yesmeck',
        label: 'yesmeck',
      },
    ]}
  />
);

export default App;
```

### 自动大小

- demo: `autoSize`

自适应内容高度。

```tsx
import React from 'react';
import { Mentions } from 'antd';

const App: React.FC = () => (
  <Mentions
    autoSize
    style={{ width: '100%' }}
    options={[
      {
        value: 'afc163',
        label: 'afc163',
      },
      {
        value: 'zombieJ',
        label: 'zombieJ',
      },
      {
        value: 'yesmeck',
        label: 'yesmeck',
      },
    ]}
  />
);

export default App;
```

### 自定义状态

- demo: `status`

使用 `status` 为 Mentions 添加状态。可选 `error` 或者 `warning`。

```tsx
import { Mentions, Space } from 'antd';
import type { OptionProps } from 'antd/es/mentions';
import React from 'react';

const { Option } = Mentions;

const onChange = (value: string) => {
  console.log('Change:', value);
};

const onSelect = (option: OptionProps) => {
  console.log('select', option);
};

const App: React.FC = () => {
  const options = (
    <>
      <Option value="afc163">afc163</Option>
      <Option value="zombieJ">zombieJ</Option>
      <Option value="yesmeck">yesmeck</Option>
    </>
  );

  return (
    <Space direction="vertical">
      <Mentions onChange={onChange} onSelect={onSelect} defaultValue="@afc163" status="error">
        {options}
      </Mentions>
      <Mentions onChange={onChange} onSelect={onSelect} defaultValue="@afc163" status="warning">
        {options}
      </Mentions>
    </Space>
  );
};

export default App;
```
