---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "breadcrumb"
component: "Breadcrumb"
component_code_name: "Breadcrumb"
recommended_import: "import { Breadcrumb } from 'antd';"
subtitle_zh: "面包屑"
type_zh: "导航"
demo_count: 6
---

# Breadcrumb 面包屑

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Breadcrumb`
- 推荐导入：`import { Breadcrumb } from 'antd';`
- 组件分类：导航
- 简述：显示当前页面在系统层级结构中的位置，并能向上返回。

## 示例索引

- 基本（basic）
- 带有图标的（withIcon）
- react-router V6（react-router）
- 分隔符（separator）
- 带下拉菜单的面包屑（overlay）
- 分隔符（separator-component）

## 使用文档

显示当前页面在系统层级结构中的位置，并能向上返回。

### 何时使用

- 当系统拥有超过两级以上的层级结构时；
- 当需要告知用户『你在哪里』时；
- 当需要向上导航的功能时。

#### 4.24.0 用法升级

```__react
import Alert from '../alert';
ReactDOM.render(<Alert message="在 4.24.0 版本后，我们提供了 <Breadcrumb.Item menu={{ items: [...] }}> 的简写方式，有更好的性能和更方便的数据组织方式，开发者不再需要自行拼接 JSX。同时我们废弃了原先的写法，你还是可以在 4.x 继续使用，但会在控制台看到警告，并会在 5.0 后移除。" />, mountNode);
```

```jsx
// >=4.24.0 可用，推荐的写法 ✅
const items = [
  { label: '菜单项一', key: 'item-1' }, // 菜单项务必填写 key
  { label: '菜单项二', key: 'item-2' },
];
return (
  <Breadcrumb>
    <Breadcrumb.Item menu={{ items }}>Ant Design</Breadcrumb.Item>
  </Breadcrumb>
);

// <4.24.0 可用，>=4.24.0 时不推荐 🙅🏻‍♀️
const menu = (
  <Menu>
    <Menu.Item>菜单项一</Menu.Item>
    <Menu.Item>菜单项二</Menu.Item>
  </Menu>
);
return (
  <Breadcrumb>
    <Breadcrumb.Item overlay={menu}>Ant Design</Breadcrumb.Item>
  </Breadcrumb>
);
```

### API

#### Breadcrumb

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| itemRender | 自定义链接函数，和 react-router 配置使用 | (route, params, routes, paths) => ReactNode | - |  |
| params | 路由的参数 | object | - |  |
| routes | router 的路由栈信息 | [routes\[\]](#routes) | - |  |
| separator | 分隔符自定义 | ReactNode | `/` |  |

#### Breadcrumb.Item

| 参数          | 说明                     | 类型                               | 默认值 | 版本   |
| ------------- | ------------------------ | ---------------------------------- | ------ | ------ |
| className     | 自定义类名               | string                             | -      |        |
| dropdownProps | 弹出下拉菜单的自定义配置 | Dropdown   | -      |        |
| href          | 链接的目的地             | string                             | -      |        |
| menu          | 菜单配置项               | MenuProps | -      | 4.24.0 |
| onClick       | 单击事件                 | (e:MouseEvent) => void             | -      |        |

#### Breadcrumb.Separator

| 参数     | 说明           | 类型      | 默认值 | 版本 |
| -------- | -------------- | --------- | ------ | ---- |
| children | 要显示的分隔符 | ReactNode | `/`    |      |

> 注意：在使用 `Breadcrumb.Separator` 时，其父组件的分隔符必须设置为 `separator=""`，否则会出现父组件默认的分隔符。

#### routes

```ts
interface Route {
  path: string;
  breadcrumbName: string;
  children: Array<{
    path: string;
    breadcrumbName: string;
  }>;
}
```

#### 和 browserHistory 配合

和 react-router 一起使用时，默认生成的 url 路径是带有 `#` 的，如果和 browserHistory 一起使用的话，你可以使用 `itemRender` 属性定义面包屑链接。

```jsx
import { Link } from 'react-router';

const routes = [
  {
    path: 'index',
    breadcrumbName: 'home',
  },
  {
    path: 'first',
    breadcrumbName: 'first',
    children: [
      {
        path: '/general',
        breadcrumbName: 'General',
      },
      {
        path: '/layout',
        breadcrumbName: 'Layout',
      },
      {
        path: '/navigation',
        breadcrumbName: 'Navigation',
      },
    ],
  },
  {
    path: 'second',
    breadcrumbName: 'second',
  },
];

function itemRender(route, params, routes, paths) {
  const last = routes.indexOf(route) === routes.length - 1;
  return last ? (
    <span>{route.breadcrumbName}</span>
  ) : (
    <Link to={paths.join('/')}>{route.breadcrumbName}</Link>
  );
}

return <Breadcrumb itemRender={itemRender} routes={routes} />;
```

## 示例代码

### 基本

- demo: `basic`

最简单的用法。

```tsx
import { Breadcrumb } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Breadcrumb>
    <Breadcrumb.Item>Home</Breadcrumb.Item>
    <Breadcrumb.Item>
      <a href="">Application Center</a>
    </Breadcrumb.Item>
    <Breadcrumb.Item>
      <a href="">Application List</a>
    </Breadcrumb.Item>
    <Breadcrumb.Item>An Application</Breadcrumb.Item>
  </Breadcrumb>
);

export default App;
```

### 带有图标的

- demo: `withIcon`

图标放在文字前面。

```tsx
import { HomeOutlined, UserOutlined } from '@ant-design/icons';
import { Breadcrumb } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Breadcrumb>
    <Breadcrumb.Item href="">
      <HomeOutlined />
    </Breadcrumb.Item>
    <Breadcrumb.Item href="">
      <UserOutlined />
      <span>Application List</span>
    </Breadcrumb.Item>
    <Breadcrumb.Item>Application</Breadcrumb.Item>
  </Breadcrumb>
);

export default App;
```

### react-router V6

- demo: `react-router`

与 `react-router@6+` 结合使用，生成和路由绑定的面包屑。

```tsx
import { Alert, Breadcrumb } from 'antd';
import React from 'react';
import { HashRouter, Link, Route, Routes, useLocation } from 'react-router-dom';

const Apps = () => (
  <ul className="app-list">
    <li>
      <Link to="/apps/1">Application1</Link>：<Link to="/apps/1/detail">Detail</Link>
    </li>
    <li>
      <Link to="/apps/2">Application2</Link>：<Link to="/apps/2/detail">Detail</Link>
    </li>
  </ul>
);

const breadcrumbNameMap: Record<string, string> = {
  '/apps': 'Application List',
  '/apps/1': 'Application1',
  '/apps/2': 'Application2',
  '/apps/1/detail': 'Detail',
  '/apps/2/detail': 'Detail',
};

const Home = () => {
  const location = useLocation();
  const pathSnippets = location.pathname.split('/').filter(i => i);

  const extraBreadcrumbItems = pathSnippets.map((_, index) => {
    const url = `/${pathSnippets.slice(0, index + 1).join('/')}`;
    return (
      <Breadcrumb.Item key={url}>
        <Link to={url}>{breadcrumbNameMap[url]}</Link>
      </Breadcrumb.Item>
    );
  });

  const breadcrumbItems = [
    <Breadcrumb.Item key="home">
      <Link to="/">Home</Link>
    </Breadcrumb.Item>,
  ].concat(extraBreadcrumbItems);

  return (
    <div className="demo">
      <div className="demo-nav">
        <Link to="/">Home</Link>
        <Link to="/apps">Application List</Link>
      </div>
      <Routes>
        <Route path="/apps" element={<Apps />} />
        <Route path="*" element={<span>Home Page</span>} />
      </Routes>
      <Alert style={{ margin: '16px 0' }} message="Click the navigation above to switch:" />
      <Breadcrumb>{breadcrumbItems}</Breadcrumb>
    </div>
  );
};

const App: React.FC = () => (
  <HashRouter>
    <Home />
  </HashRouter>
);

export default App;
```

```css
.demo {
  margin: 16px;
}
.demo-nav {
  height: 30px;
  margin-bottom: 16px;
  line-height: 30px;
  background: #f8f8f8;
}
.demo-nav a {
  padding: 0 8px;
  line-height: 30px;
}
.app-list {
  margin-top: 16px;
}
```

### 分隔符

- demo: `separator`

使用 `separator=">"` 可以自定义分隔符。

```tsx
import { Breadcrumb } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Breadcrumb separator=">">
    <Breadcrumb.Item>Home</Breadcrumb.Item>
    <Breadcrumb.Item href="">Application Center</Breadcrumb.Item>
    <Breadcrumb.Item href="">Application List</Breadcrumb.Item>
    <Breadcrumb.Item>An Application</Breadcrumb.Item>
  </Breadcrumb>
);

export default App;
```

### 带下拉菜单的面包屑

- demo: `overlay`

面包屑支持下拉菜单。

```tsx
import { Breadcrumb } from 'antd';
import React from 'react';

const items = [
  {
    key: '1',
    label: (
      <a target="_blank" rel="noopener noreferrer" href="http://www.alipay.com/">
        General
      </a>
    ),
  },
  {
    key: '2',
    label: (
      <a target="_blank" rel="noopener noreferrer" href="http://www.taobao.com/">
        Layout
      </a>
    ),
  },
  {
    key: '3',
    label: (
      <a target="_blank" rel="noopener noreferrer" href="http://www.tmall.com/">
        Navigation
      </a>
    ),
  },
];

const App: React.FC = () => (
  <Breadcrumb>
    <Breadcrumb.Item>Ant Design</Breadcrumb.Item>
    <Breadcrumb.Item>
      <a href="">Component</a>
    </Breadcrumb.Item>
    <Breadcrumb.Item menu={{ items }}>
      <a href="">General</a>
    </Breadcrumb.Item>
    <Breadcrumb.Item>Button</Breadcrumb.Item>
  </Breadcrumb>
);

export default App;
```

### 分隔符

- demo: `separator-component`

使用 `Breadcrumb.Separator` 可以自定义分隔符。

```tsx
import { Breadcrumb } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Breadcrumb separator="">
    <Breadcrumb.Item>Location</Breadcrumb.Item>
    <Breadcrumb.Separator>:</Breadcrumb.Separator>
    <Breadcrumb.Item href="">Application Center</Breadcrumb.Item>
    <Breadcrumb.Separator />
    <Breadcrumb.Item href="">Application List</Breadcrumb.Item>
    <Breadcrumb.Separator />
    <Breadcrumb.Item>An Application</Breadcrumb.Item>
  </Breadcrumb>
);

export default App;
```
