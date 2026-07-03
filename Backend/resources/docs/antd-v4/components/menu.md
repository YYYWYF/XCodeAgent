---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "menu"
component: "Menu"
component_code_name: "Menu"
recommended_import: "import { Menu } from 'antd';"
subtitle_zh: "导航菜单"
type_zh: "导航"
demo_count: 9
---

# Menu 导航菜单

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Menu`
- 推荐导入：`import { Menu } from 'antd';`
- 组件分类：导航
- 简述：为页面和功能提供导航的菜单列表。

## 示例索引

- 基础用法（废弃的语法糖）（deprecated）
- 顶部导航（horizontal）
- 内嵌菜单（inline）
- 缩起内嵌菜单（inline-collapsed）
- 只展开当前父级菜单（sider-current）
- 垂直菜单（vertical）
- 主题（theme）
- 子菜单主题（submenu-theme）
- 切换菜单类型（switch-mode）

## 使用文档

为页面和功能提供导航的菜单列表。

### 何时使用

导航菜单是一个网站的灵魂，用户依赖导航在各个页面中进行跳转。一般分为顶部导航和侧边导航，顶部导航提供全局性的类目和功能，侧边导航提供多级结构来收纳和排列网站架构。

更多布局和导航的使用可以参考：通用布局。

### 开发者注意事项

- Menu 元素为 `ul`，因而仅支持 `li` 以及 `script-supporting` 子元素。因而你的子节点元素应该都在 `Menu.Item` 内使用。
- Menu 需要计算节点结构，因而其子元素仅支持 `Menu.*` 以及对此进行封装的 HOC 组件。

#### 4.20.0 用法升级

```__react
import Alert from '../alert';
ReactDOM.render(<Alert message="在 4.20.0 版本后，我们提供了 <Menu items={[...]} /> 的简写方式，有更好的性能和更方便的数据组织方式，开发者不再需要自行拼接 JSX。同时我们废弃了原先的写法，你还是可以在 4.x 继续使用，但会在控制台看到警告，并会在 5.0 后移除。" />, mountNode);
```

```jsx
// >=4.20.0 可用，推荐的写法 ✅
const items = [
  { label: '菜单项一', key: 'item-1' }, // 菜单项务必填写 key
  { label: '菜单项二', key: 'item-2' },
  {
    label: '子菜单',
    key: 'submenu',
    children: [{ label: '子菜单项', key: 'submenu-item-1' }],
  },
];
return <Menu items={items} />;

// <4.20.0 可用，>=4.20.0 时不推荐 🙅🏻‍♀️
<Menu>
  <Menu.Item>菜单项一</Menu.Item>
  <Menu.Item>菜单项二</Menu.Item>
  <Menu.SubMenu title="子菜单">
    <Menu.Item>子菜单项</Menu.Item>
  </Menu.SubMenu>
</Menu>;
```

`<4.20.0` 版本的 JSX 演示写法可以参考

### API

#### Menu

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| defaultOpenKeys | 初始展开的 SubMenu 菜单项 key 数组 | string\[] | - |  |
| defaultSelectedKeys | 初始选中的菜单项 key 数组 | string\[] | - |  |
| expandIcon | 自定义展开图标 | ReactNode \| `(props: SubMenuProps & { isSubMenu: boolean }) => ReactNode` | - | 4.9.0 |
| forceSubMenuRender | 在子菜单展示之前就渲染进 DOM | boolean | false |  |
| inlineCollapsed | inline 时菜单是否收起状态 | boolean | - |  |
| inlineIndent | inline 模式的菜单缩进宽度 | number | 24 |  |
| items | 菜单内容 | [ItemType\[\]](#ItemType) | - | 4.20.0 |
| mode | 菜单类型，现在支持垂直、水平、和内嵌模式三种 | `vertical` \| `horizontal` \| `inline` | `vertical` |  |
| multiple | 是否允许多选 | boolean | false |  |
| openKeys | 当前展开的 SubMenu 菜单项 key 数组 | string\[] | - |  |
| overflowedIndicator | 用于自定义 Menu 水平空间不足时的省略收缩的图标 | ReactNode | `` |  |
| selectable | 是否允许选中 | boolean | true |  |
| selectedKeys | 当前选中的菜单项 key 数组 | string\[] | - |  |
| style | 根节点样式 | CSSProperties | - |  |
| subMenuCloseDelay | 用户鼠标离开子菜单后关闭延时，单位：秒 | number | 0.1 |  |
| subMenuOpenDelay | 用户鼠标进入子菜单后开启延时，单位：秒 | number | 0 |  |
| theme | 主题颜色 | `light` \| `dark` | `light` |  |
| triggerSubMenuAction | SubMenu 展开/关闭的触发行为 | `hover` \| `click` | `hover` |  |
| onClick | 点击 MenuItem 调用此函数 | function({ item, key, keyPath, domEvent }) | - |  |
| onDeselect | 取消选中时调用，仅在 multiple 生效 | function({ item, key, keyPath, selectedKeys, domEvent }) | - |  |
| onOpenChange | SubMenu 展开/关闭的回调 | function(openKeys: string\[]) | - |  |
| onSelect | 被选中时调用 | function({ item, key, keyPath, selectedKeys, domEvent }) | -   |  |

> 更多属性查看 rc-menu

#### ItemType

> type ItemType = MenuItemType | SubMenuType | MenuItemGroupType | MenuDividerType;

##### MenuItemType

| 参数     | 说明                     | 类型      | 默认值 | 版本 |
| -------- | ------------------------ | --------- | ------ | ---- |
| danger   | 展示错误状态样式         | boolean   | false  |      |
| disabled | 是否禁用                 | boolean   | false  |      |
| icon     | 菜单图标                 | ReactNode | -      |      |
| key      | item 的唯一标志          | string    | -      |      |
| label    | 菜单项标题               | ReactNode | -      |      |
| title    | 设置收缩时展示的悬浮标题 | string    | -      |      |

> 注意：`icon` 是 `4.2.0` 新增的属性，之前的版本请使用下面的方式定义图标。
>
> ```jsx
> <Menu.Item>
>   <PieChartOutlined />
>   <span>Option 1</span>
> </Menu.Item>
> <Menu.SubMenu
>   title={
>     <>
>       <PieChartOutlined />
>       <span>Option 2</span>
>     </>
>   }
> >
>   ...
> </Menu.SubMenu>
> ```

##### SubMenuType

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| children | 子菜单的菜单项 | [ItemType\[\]](#ItemType) | - |  |
| disabled | 是否禁用 | boolean | false |  |
| icon | 菜单图标 | ReactNode | - |  |
| key | 唯一标志 | string | - |  |
| label | 菜单项标题 | ReactNode | - |  |
| popupClassName | 子菜单样式，`mode="inline"` 时无效 | string | - |  |
| popupOffset | 子菜单偏移量，`mode="inline"` 时无效 | \[number, number] | - |  |
| onTitleClick | 点击子菜单标题 | function({ key, domEvent }) | - |  |
| theme | 设置子菜单的主题，默认从 Menu 上继承 | `light` \| `dark` | - |  |

##### MenuItemGroupType

定义类型为 `group` 时，会作为分组处理:

```ts
const groupItem = {
  type: 'group', // Must have
  label: 'My Group',
  children: [],
};
```

| 参数     | 说明         | 类型                              | 默认值 | 版本 |
| -------- | ------------ | --------------------------------- | ------ | ---- |
| children | 分组的菜单项 | [MenuItemType\[\]](#MenuItemType) | -      |      |
| label    | 分组标题     | ReactNode                         | -      |      |

##### MenuDividerType

菜单项分割线，只用在弹出菜单内，需要定义类型为 `divider`：

```ts
const dividerItem = {
  type: 'divider', // Must have
};
```

| 参数   | 说明     | 类型    | 默认值 | 版本 |
| ------ | -------- | ------- | ------ | ---- |
| dashed | 是否虚线 | boolean | false  |      |

### FAQ

#### 为何 Menu 的子元素会渲染两次？

Menu 通过二次渲染收集嵌套结构信息以支持 HOC 的结构。合并成一个推导结构会使得逻辑变得十分复杂，欢迎 PR 以协助改进该设计。

#### 在 Flex 布局中，Menu 没有按照预期响应式省略菜单？

Menu 初始化时会先全部渲染，然后根据宽度裁剪内容。当处于 Flex 布局中，你需要告知其预期宽度为响应式宽度（在线 Demo）：

```jsx
<div style={{ flex }}>
  <div style={{ ... }}>Some Content</div>
  <Menu style={{ minWidth: 0, flex: "auto" }} />
</div>
```

## 示例代码

### 基础用法（废弃的语法糖）

- demo: `deprecated`

通过语法糖的方式组织菜单目录树，在 `4.20.0` 之后推荐通过 `items` 属性实现。在下个大版本中语法糖用法将被移除从而让 Menu 可以获得更好的性能优化。

```tsx
import { Menu } from 'antd';
import { MailOutlined, AppstoreOutlined, SettingOutlined } from '@ant-design/icons';

const App = () => (
  <Menu mode="horizontal" defaultSelectedKeys={['mail']}>
    <Menu.Item key="mail" icon={<MailOutlined />}>
      Navigation One
    </Menu.Item>
    <Menu.SubMenu key="SubMenu" title="Navigation Two - Submenu" icon={<SettingOutlined />}>
      <Menu.Item key="two" icon={<AppstoreOutlined />}>
        Navigation Two
      </Menu.Item>
      <Menu.Item key="three" icon={<AppstoreOutlined />}>
        Navigation Three
      </Menu.Item>
      <Menu.ItemGroup title="Item Group">
        <Menu.Item key="four" icon={<AppstoreOutlined />}>
          Navigation Four
        </Menu.Item>
        <Menu.Item key="five" icon={<AppstoreOutlined />}>
          Navigation Five
        </Menu.Item>
      </Menu.ItemGroup>
    </Menu.SubMenu>
  </Menu>
);

export default App;
```

### 顶部导航

- demo: `horizontal`

水平的顶部导航菜单。

```tsx
import { AppstoreOutlined, MailOutlined, SettingOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { Menu } from 'antd';
import React, { useState } from 'react';

const items: MenuProps['items'] = [
  {
    label: 'Navigation One',
    key: 'mail',
    icon: <MailOutlined />,
  },
  {
    label: 'Navigation Two',
    key: 'app',
    icon: <AppstoreOutlined />,
    disabled: true,
  },
  {
    label: 'Navigation Three - Submenu',
    key: 'SubMenu',
    icon: <SettingOutlined />,
    children: [
      {
        type: 'group',
        label: 'Item 1',
        children: [
          {
            label: 'Option 1',
            key: 'setting:1',
          },
          {
            label: 'Option 2',
            key: 'setting:2',
          },
        ],
      },
      {
        type: 'group',
        label: 'Item 2',
        children: [
          {
            label: 'Option 3',
            key: 'setting:3',
          },
          {
            label: 'Option 4',
            key: 'setting:4',
          },
        ],
      },
    ],
  },
  {
    label: (
      <a href="https://ant.design" target="_blank" rel="noopener noreferrer">
        Navigation Four - Link
      </a>
    ),
    key: 'alipay',
  },
];

const App: React.FC = () => {
  const [current, setCurrent] = useState('mail');

  const onClick: MenuProps['onClick'] = e => {
    console.log('click ', e);
    setCurrent(e.key);
  };

  return <Menu onClick={onClick} selectedKeys={[current]} mode="horizontal" items={items} />;
};

export default App;
```

### 内嵌菜单

- demo: `inline`

垂直菜单，子菜单内嵌在菜单区域。

```tsx
import { AppstoreOutlined, MailOutlined, SettingOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { Menu } from 'antd';
import React from 'react';

type MenuItem = Required<MenuProps>['items'][number];

function getItem(
  label: React.ReactNode,
  key: React.Key,
  icon?: React.ReactNode,
  children?: MenuItem[],
  type?: 'group',
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
    type,
  } as MenuItem;
}

const items: MenuProps['items'] = [
  getItem('Navigation One', 'sub1', <MailOutlined />, [
    getItem('Item 1', 'g1', null, [getItem('Option 1', '1'), getItem('Option 2', '2')], 'group'),
    getItem('Item 2', 'g2', null, [getItem('Option 3', '3'), getItem('Option 4', '4')], 'group'),
  ]),

  getItem('Navigation Two', 'sub2', <AppstoreOutlined />, [
    getItem('Option 5', '5'),
    getItem('Option 6', '6'),
    getItem('Submenu', 'sub3', null, [getItem('Option 7', '7'), getItem('Option 8', '8')]),
  ]),

  getItem('Navigation Three', 'sub4', <SettingOutlined />, [
    getItem('Option 9', '9'),
    getItem('Option 10', '10'),
    getItem('Option 11', '11'),
    getItem('Option 12', '12'),
  ]),
];

const App: React.FC = () => {
  const onClick: MenuProps['onClick'] = e => {
    console.log('click ', e);
  };

  return (
    <Menu
      onClick={onClick}
      style={{ width: 256 }}
      defaultSelectedKeys={['1']}
      defaultOpenKeys={['sub1']}
      mode="inline"
      items={items}
    />
  );
};

export default App;
```

### 缩起内嵌菜单

- demo: `inline-collapsed`

内嵌菜单可以被缩起/展开。

你可以在 Layout 里查看侧边布局结合的完整示例。

```tsx
import {
  AppstoreOutlined,
  ContainerOutlined,
  DesktopOutlined,
  MailOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PieChartOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { Button, Menu } from 'antd';
import React, { useState } from 'react';

type MenuItem = Required<MenuProps>['items'][number];

function getItem(
  label: React.ReactNode,
  key: React.Key,
  icon?: React.ReactNode,
  children?: MenuItem[],
  type?: 'group',
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
    type,
  } as MenuItem;
}

const items: MenuItem[] = [
  getItem('Option 1', '1', <PieChartOutlined />),
  getItem('Option 2', '2', <DesktopOutlined />),
  getItem('Option 3', '3', <ContainerOutlined />),

  getItem('Navigation One', 'sub1', <MailOutlined />, [
    getItem('Option 5', '5'),
    getItem('Option 6', '6'),
    getItem('Option 7', '7'),
    getItem('Option 8', '8'),
  ]),

  getItem('Navigation Two', 'sub2', <AppstoreOutlined />, [
    getItem('Option 9', '9'),
    getItem('Option 10', '10'),

    getItem('Submenu', 'sub3', null, [getItem('Option 11', '11'), getItem('Option 12', '12')]),
  ]),
];

const App: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);

  const toggleCollapsed = () => {
    setCollapsed(!collapsed);
  };

  return (
    <div style={{ width: 256 }}>
      <Button type="primary" onClick={toggleCollapsed} style={{ marginBottom: 16 }}>
        {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
      </Button>
      <Menu
        defaultSelectedKeys={['1']}
        defaultOpenKeys={['sub1']}
        mode="inline"
        theme="dark"
        inlineCollapsed={collapsed}
        items={items}
      />
    </div>
  );
};

export default App;
```

### 只展开当前父级菜单

- demo: `sider-current`

点击菜单，收起其他展开的所有菜单，保持菜单聚焦简洁。

```tsx
import { AppstoreOutlined, MailOutlined, SettingOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { Menu } from 'antd';
import React, { useState } from 'react';

type MenuItem = Required<MenuProps>['items'][number];

function getItem(
  label: React.ReactNode,
  key: React.Key,
  icon?: React.ReactNode,
  children?: MenuItem[],
  type?: 'group',
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
    type,
  } as MenuItem;
}

const items: MenuItem[] = [
  getItem('Navigation One', 'sub1', <MailOutlined />, [
    getItem('Option 1', '1'),
    getItem('Option 2', '2'),
    getItem('Option 3', '3'),
    getItem('Option 4', '4'),
  ]),
  getItem('Navigation Two', 'sub2', <AppstoreOutlined />, [
    getItem('Option 5', '5'),
    getItem('Option 6', '6'),
    getItem('Submenu', 'sub3', null, [getItem('Option 7', '7'), getItem('Option 8', '8')]),
  ]),
  getItem('Navigation Three', 'sub4', <SettingOutlined />, [
    getItem('Option 9', '9'),
    getItem('Option 10', '10'),
    getItem('Option 11', '11'),
    getItem('Option 12', '12'),
  ]),
];

// submenu keys of first level
const rootSubmenuKeys = ['sub1', 'sub2', 'sub4'];

const App: React.FC = () => {
  const [openKeys, setOpenKeys] = useState(['sub1']);

  const onOpenChange: MenuProps['onOpenChange'] = keys => {
    const latestOpenKey = keys.find(key => openKeys.indexOf(key) === -1);
    if (rootSubmenuKeys.indexOf(latestOpenKey!) === -1) {
      setOpenKeys(keys);
    } else {
      setOpenKeys(latestOpenKey ? [latestOpenKey] : []);
    }
  };

  return (
    <Menu
      mode="inline"
      openKeys={openKeys}
      onOpenChange={onOpenChange}
      style={{ width: 256 }}
      items={items}
    />
  );
};

export default App;
```

### 垂直菜单

- demo: `vertical`

子菜单是弹出的形式。

```tsx
import { AppstoreOutlined, MailOutlined, SettingOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { Menu } from 'antd';
import React from 'react';

type MenuItem = Required<MenuProps>['items'][number];

function getItem(
  label: React.ReactNode,
  key?: React.Key | null,
  icon?: React.ReactNode,
  children?: MenuItem[],
  type?: 'group',
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
    type,
  } as MenuItem;
}

const items: MenuItem[] = [
  getItem('Navigation One', 'sub1', <MailOutlined />, [
    getItem('Item 1', null, null, [getItem('Option 1', '1'), getItem('Option 2', '2')], 'group'),
    getItem('Item 2', null, null, [getItem('Option 3', '3'), getItem('Option 4', '4')], 'group'),
  ]),

  getItem('Navigation Two', 'sub2', <AppstoreOutlined />, [
    getItem('Option 5', '5'),
    getItem('Option 6', '6'),
    getItem('Submenu', 'sub3', null, [getItem('Option 7', '7'), getItem('Option 8', '8')]),
  ]),

  getItem('Navigation Three', 'sub4', <SettingOutlined />, [
    getItem('Option 9', '9'),
    getItem('Option 10', '10'),
    getItem('Option 11', '11'),
    getItem('Option 12', '12'),
  ]),
];

const onClick: MenuProps['onClick'] = e => {
  console.log('click', e);
};

const App: React.FC = () => (
  <Menu onClick={onClick} style={{ width: 256 }} mode="vertical" items={items} />
);

export default App;
```

### 主题

- demo: `theme`

内建了两套主题 `light` 和 `dark`，默认 `light`。

```tsx
import { AppstoreOutlined, MailOutlined, SettingOutlined } from '@ant-design/icons';
import type { MenuProps, MenuTheme } from 'antd';
import { Menu, Switch } from 'antd';
import React, { useState } from 'react';

type MenuItem = Required<MenuProps>['items'][number];

function getItem(
  label: React.ReactNode,
  key?: React.Key | null,
  icon?: React.ReactNode,
  children?: MenuItem[],
  type?: 'group',
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
    type,
  } as MenuItem;
}

const items: MenuItem[] = [
  getItem('Navigation One', 'sub1', <MailOutlined />, [
    getItem('Option 1', '1'),
    getItem('Option 2', '2'),
    getItem('Option 3', '3'),
    getItem('Option 4', '4'),
  ]),

  getItem('Navigation Two', 'sub2', <AppstoreOutlined />, [
    getItem('Option 5', '5'),
    getItem('Option 6', '6'),
    getItem('Submenu', 'sub3', null, [getItem('Option 7', '7'), getItem('Option 8', '8')]),
  ]),

  getItem('Navigation Three', 'sub4', <SettingOutlined />, [
    getItem('Option 9', '9'),
    getItem('Option 10', '10'),
    getItem('Option 11', '11'),
    getItem('Option 12', '12'),
  ]),
];

const App: React.FC = () => {
  const [theme, setTheme] = useState<MenuTheme>('dark');
  const [current, setCurrent] = useState('1');

  const changeTheme = (value: boolean) => {
    setTheme(value ? 'dark' : 'light');
  };

  const onClick: MenuProps['onClick'] = e => {
    console.log('click ', e);
    setCurrent(e.key);
  };

  return (
    <>
      <Switch
        checked={theme === 'dark'}
        onChange={changeTheme}
        checkedChildren="Dark"
        unCheckedChildren="Light"
      />
      <br />
      <br />
      <Menu
        theme={theme}
        onClick={onClick}
        style={{ width: 256 }}
        defaultOpenKeys={['sub1']}
        selectedKeys={[current]}
        mode="inline"
        items={items}
      />
    </>
  );
};

export default App;
```

### 子菜单主题

- demo: `submenu-theme`

你可以通过 `theme` 属性来设置 SubMenu 的主题从而达到不同目录树下不同主题色的效果。该例子默认为根目录深色，子目录浅色效果。

```tsx
import { MailOutlined } from '@ant-design/icons';
import type { MenuProps, MenuTheme } from 'antd';
import { Menu, Switch } from 'antd';
import React, { useState } from 'react';

type MenuItem = Required<MenuProps>['items'][number];

function getItem(
  label: React.ReactNode,
  key?: React.Key | null,
  icon?: React.ReactNode,
  children?: MenuItem[],
  theme?: 'light' | 'dark',
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
    theme,
  } as MenuItem;
}

const App: React.FC = () => {
  const [theme, setTheme] = useState<MenuTheme>('light');
  const [current, setCurrent] = useState('1');

  const changeTheme = (value: boolean) => {
    setTheme(value ? 'dark' : 'light');
  };

  const onClick: MenuProps['onClick'] = e => {
    setCurrent(e.key);
  };

  const items: MenuItem[] = [
    getItem(
      'Navigation One',
      'sub1',
      <MailOutlined />,
      [getItem('Option 1', '1'), getItem('Option 2', '2'), getItem('Option 3', '3')],
      theme,
    ),
    getItem('Option 5', '5'),
    getItem('Option 6', '6'),
  ];

  return (
    <>
      <Switch
        checked={theme === 'dark'}
        onChange={changeTheme}
        checkedChildren="Dark"
        unCheckedChildren="Light"
      />
      <br />
      <br />
      <Menu
        onClick={onClick}
        style={{ width: 256 }}
        defaultOpenKeys={['sub1']}
        selectedKeys={[current]}
        mode="vertical"
        theme="dark"
        items={items}
      />
    </>
  );
};

export default App;
```

### 切换菜单类型

- demo: `switch-mode`

展示动态切换模式。

```tsx
import {
  AppstoreOutlined,
  CalendarOutlined,
  LinkOutlined,
  MailOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { Divider, Menu, Switch } from 'antd';
import type { MenuProps, MenuTheme } from 'antd/es/menu';
import React, { useState } from 'react';

type MenuItem = Required<MenuProps>['items'][number];

function getItem(
  label: React.ReactNode,
  key?: React.Key | null,
  icon?: React.ReactNode,
  children?: MenuItem[],
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
  } as MenuItem;
}

const items: MenuItem[] = [
  getItem('Navigation One', '1', <MailOutlined />),
  getItem('Navigation Two', '2', <CalendarOutlined />),
  getItem('Navigation Two', 'sub1', <AppstoreOutlined />, [
    getItem('Option 3', '3'),
    getItem('Option 4', '4'),
    getItem('Submenu', 'sub1-2', null, [getItem('Option 5', '5'), getItem('Option 6', '6')]),
  ]),
  getItem('Navigation Three', 'sub2', <SettingOutlined />, [
    getItem('Option 7', '7'),
    getItem('Option 8', '8'),
    getItem('Option 9', '9'),
    getItem('Option 10', '10'),
  ]),
  getItem(
    <a href="https://ant.design" target="_blank" rel="noopener noreferrer">
      Ant Design
    </a>,
    'link',
    <LinkOutlined />,
  ),
];

const App: React.FC = () => {
  const [mode, setMode] = useState<'vertical' | 'inline'>('inline');
  const [theme, setTheme] = useState<MenuTheme>('light');

  const changeMode = (value: boolean) => {
    setMode(value ? 'vertical' : 'inline');
  };

  const changeTheme = (value: boolean) => {
    setTheme(value ? 'dark' : 'light');
  };

  return (
    <>
      <Switch onChange={changeMode} /> Change Mode
      <Divider type="vertical" />
      <Switch onChange={changeTheme} /> Change Style
      <br />
      <br />
      <Menu
        style={{ width: 256 }}
        defaultSelectedKeys={['1']}
        defaultOpenKeys={['sub1']}
        mode={mode}
        theme={theme}
        items={items}
      />
    </>
  );
};

export default App;
```
