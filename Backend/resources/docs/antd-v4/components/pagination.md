---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "pagination"
component: "Pagination"
component_code_name: "Pagination"
recommended_import: "import { Pagination } from 'antd';"
subtitle_zh: "分页"
type_zh: "导航"
demo_count: 10
---

# Pagination 分页

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Pagination`
- 推荐导入：`import { Pagination } from 'antd';`
- 组件分类：导航
- 简述：采用分页的形式分隔长列表，每次只加载一个页面。

## 示例索引

- 基本（basic）
- 更多（more）
- 改变（changer）
- 跳转（jump）
- 迷你（mini）
- 简洁（simple）
- 受控（controlled）
- 总数（total）
- 全部展示（all）
- 上一步和下一步（itemRender）

## 使用文档

采用分页的形式分隔长列表，每次只加载一个页面。

### 何时使用

- 当加载/渲染所有数据将花费很多时间时；
- 可切换页码浏览数据。

### API

```jsx
<Pagination onChange={onChange} total={50} />
```

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| current | 当前页数 | number | - |  |
| defaultCurrent | 默认的当前页数 | number | 1 |  |
| defaultPageSize | 默认的每页条数 | number | 10 |  |
| disabled | 禁用分页 | boolean | - |  |
| hideOnSinglePage | 只有一页时是否隐藏分页器 | boolean | false |  |
| itemRender | 用于自定义页码的结构，可用于优化 SEO | (page, type: 'page' \| 'prev' \| 'next', originalElement) => React.ReactNode | - |  |
| pageSize | 每页条数 | number | - |  |
| pageSizeOptions | 指定每页可以显示多少条 | string\[] | \[`10`, `20`, `50`, `100`] |  |
| responsive | 当 size 未指定时，根据屏幕宽度自动调整尺寸 | boolean | - |  |
| showLessItems | 是否显示较少页面内容 | boolean | false |  |
| showQuickJumper | 是否可以快速跳转至某页 | boolean \| { goButton: ReactNode } | false |  |
| showSizeChanger | 是否展示 `pageSize` 切换器，当 `total` 大于 50 时默认为 true | boolean | - |  |
| showTitle | 是否显示原生 tooltip 页码提示 | boolean | true |  |
| showTotal | 用于显示数据总量和当前数据顺序 | function(total, range) | - |  |
| simple | 当添加该属性时，显示为简单分页 | boolean | - |  |
| size | 当为 `small` 时，是小尺寸分页 | `default` \| `small` | `default` |  |
| total | 数据总数 | number | 0 |  |
| onChange | 页码或 `pageSize` 改变的回调，参数是改变后的页码及每页条数 | function(page, pageSize) | - |  |
| onShowSizeChange | pageSize 变化的回调 | function(current, size) | - |  |

## 示例代码

### 基本

- demo: `basic`

基础分页。

```tsx
import { Pagination } from 'antd';
import React from 'react';

const App: React.FC = () => <Pagination defaultCurrent={1} total={50} />;

export default App;
```

### 更多

- demo: `more`

更多分页。

```tsx
import { Pagination } from 'antd';
import React from 'react';

const App: React.FC = () => <Pagination defaultCurrent={6} total={500} />;

export default App;
```

### 改变

- demo: `changer`

改变每页显示条目数。

```tsx
import type { PaginationProps } from 'antd';
import { Pagination } from 'antd';
import React from 'react';

const onShowSizeChange: PaginationProps['onShowSizeChange'] = (current, pageSize) => {
  console.log(current, pageSize);
};

const App: React.FC = () => (
  <>
    <Pagination
      showSizeChanger
      onShowSizeChange={onShowSizeChange}
      defaultCurrent={3}
      total={500}
    />
    <br />
    <Pagination
      showSizeChanger
      onShowSizeChange={onShowSizeChange}
      defaultCurrent={3}
      total={500}
      disabled
    />
  </>
);

export default App;
```

### 跳转

- demo: `jump`

快速跳转到某一页。

```tsx
import type { PaginationProps } from 'antd';
import { Pagination } from 'antd';
import React from 'react';

const onChange: PaginationProps['onChange'] = pageNumber => {
  console.log('Page: ', pageNumber);
};

const App: React.FC = () => (
  <>
    <Pagination showQuickJumper defaultCurrent={2} total={500} onChange={onChange} />
    <br />
    <Pagination showQuickJumper defaultCurrent={2} total={500} onChange={onChange} disabled />
  </>
);

export default App;
```

### 迷你

- demo: `mini`

迷你版本。

```tsx
import type { PaginationProps } from 'antd';
import { Pagination } from 'antd';
import React from 'react';

const showTotal: PaginationProps['showTotal'] = total => `Total ${total} items`;

const App: React.FC = () => (
  <>
    <Pagination size="small" total={50} />
    <Pagination size="small" total={50} showSizeChanger showQuickJumper />
    <Pagination size="small" total={50} showTotal={showTotal} />
    <Pagination
      size="small"
      total={50}
      disabled
      showTotal={showTotal}
      showSizeChanger
      showQuickJumper
    />
  </>
);

export default App;
```

### 简洁

- demo: `simple`

简单的翻页。

```tsx
import { Pagination } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Pagination simple defaultCurrent={2} total={50} />
    <br />
    <Pagination disabled simple defaultCurrent={2} total={50} />
  </>
);

export default App;
```

### 受控

- demo: `controlled`

受控制的页码。

```tsx
import type { PaginationProps } from 'antd';
import { Pagination } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [current, setCurrent] = useState(3);

  const onChange: PaginationProps['onChange'] = page => {
    console.log(page);
    setCurrent(page);
  };

  return <Pagination current={current} onChange={onChange} total={50} />;
};

export default App;
```

### 总数

- demo: `total`

通过设置 `showTotal` 展示总共有多少数据。

```tsx
import { Pagination } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <>
    <Pagination
      total={85}
      showTotal={total => `Total ${total} items`}
      defaultPageSize={20}
      defaultCurrent={1}
    />
    <br />
    <Pagination
      total={85}
      showTotal={(total, range) => `${range[0]}-${range[1]} of ${total} items`}
      defaultPageSize={20}
      defaultCurrent={1}
    />
  </>
);

export default App;
```

### 全部展示

- demo: `all`

展示所有配置选项。

```tsx
import { Pagination } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Pagination
    total={85}
    showSizeChanger
    showQuickJumper
    showTotal={total => `Total ${total} items`}
  />
);

export default App;
```

### 上一步和下一步

- demo: `itemRender`

修改上一步和下一步为文字链接。

```tsx
import type { PaginationProps } from 'antd';
import { Pagination } from 'antd';
import React from 'react';

const itemRender: PaginationProps['itemRender'] = (_, type, originalElement) => {
  if (type === 'prev') {
    return <a>Previous</a>;
  }
  if (type === 'next') {
    return <a>Next</a>;
  }
  return originalElement;
};

const App: React.FC = () => <Pagination total={500} itemRender={itemRender} />;

export default App;
```
