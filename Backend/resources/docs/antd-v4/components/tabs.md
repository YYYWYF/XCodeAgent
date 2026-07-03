---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "tabs"
component: "Tabs"
component_code_name: "Tabs"
recommended_import: "import { Tabs } from 'antd';"
subtitle_zh: "标签页"
type_zh: "数据展示"
demo_count: 15
---

# Tabs 标签页

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Tabs`
- 推荐导入：`import { Tabs } from 'antd';`
- 组件分类：数据展示
- 简述：选项卡切换组件。

## 示例索引

- 基础用法（废弃的语法糖）（deprecated）
- 基本（basic）
- 禁用（disabled）
- 居中（centered）
- 图标（icon）
- 滑动（slide）
- 附加内容（extra）
- 大小（size）
- 位置（position）
- 卡片式页签（card）
- 新增和关闭页签（editable-card）
- 卡片式页签容器（card-top）
- 自定义新增页签触发器（custom-add-trigger）
- 自定义页签头（custom-tab-bar）
- 可拖拽标签（custom-tab-bar-node）

## 使用文档

选项卡切换组件。

### 何时使用

提供平级的区域将大块内容进行收纳和展现，保持界面整洁。

Ant Design 依次提供了三级选项卡，分别用于不同的场景。

- 卡片式的页签，提供可关闭的样式，常用于容器顶部。
- 既可用于容器顶部，也可用于容器内部，是最通用的 Tabs。
- Radio.Button 可作为更次级的页签来使用。

#### 4.23.0 用法升级

```__react
import Alert from '../alert';
ReactDOM.render(<Alert message="在 4.23.0 版本后，我们提供了 <Tabs items={[...]} /> 的简写方式，有更好的性能和更方便的数据组织方式，开发者不再需要自行拼接 JSX。同时我们废弃了原先的写法，你还是可以在 4.x 继续使用，但会在控制台看到警告，并会在 5.0 后移除。" />, mountNode);
```

```jsx
// >=4.23.0 可用，推荐的写法 ✅
const items = [
  { label: '项目 1', key: 'item-1', children: '内容 1' }, // 务必填写 key
  { label: '项目 2', key: 'item-2', children: '内容 2' },
];
return <Tabs items={items} />;

// <4.23.0 可用，>=4.23.0 时不推荐 🙅🏻‍♀️
<Tabs>
  <Tabs.TabPane tab="项目 1" key="item-1">
    内容 1
  </Tabs.TabPane>
  <Tabs.TabPane tab="项目 2" key="item-2">
    内容 2
  </Tabs.TabPane>
</Tabs>;
```

### API

#### Tabs

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| activeKey | 当前激活 tab 面板的 key | string | - |  |
| addIcon | 自定义添加按钮 | ReactNode | - | 4.4.0 |
| animated | 是否使用动画切换 Tabs, 仅生效于 `tabPosition="top"` | boolean\| { inkBar: boolean, tabPane: boolean } | { inkBar: true, tabPane: false } |  |
| centered | 标签居中展示 | boolean | false | 4.4.0 |
| defaultActiveKey | 初始化选中面板的 key，如果没有设置 activeKey | string | `第一个面板` |  |
| hideAdd | 是否隐藏加号图标，在 `type="editable-card"` 时有效 | boolean | false |  |
| items | 配置选项卡内容 | TabItemType | [] | 4.23.0 |
| moreIcon | 自定义折叠 icon | ReactNode | &lt;EllipsisOutlined /> | 4.14.0 |
| popupClassName | 更多菜单的 `className` | string | - | 4.21.0 |
| renderTabBar | 替换 TabBar，用于二次封装标签头 | (props: DefaultTabBarProps, DefaultTabBar: React.ComponentClass) => React.ReactElement | - |  |
| size | 大小，提供 `large` `middle` 和 `small` 三种大小 | string | `middle` |  |
| tabBarExtraContent | tab bar 上额外的元素 | ReactNode \| {left?: ReactNode, right?: ReactNode} | - | object: 4.6.0 |
| tabBarGutter | tabs 之间的间隙 | number | - |  |
| tabBarStyle | tab bar 的样式对象 | CSSProperties | - |  |
| tabPosition | 页签位置，可选值有 `top` `right` `bottom` `left` | string | `top` |  |
| destroyInactiveTabPane | 被隐藏时是否销毁 DOM 结构 | boolean | false |  |
| type | 页签的基本样式，可选 `line`、`card` `editable-card` 类型 | string | `line` |  |
| onChange | 切换面板的回调 | function(activeKey) {} | - |  |
| onEdit | 新增和删除页签的回调，在 `type="editable-card"` 时有效 | (action === 'add' ? event : targetKey, action): void | - |  |
| onTabClick | tab 被点击的回调 | function(key: string, event: MouseEvent) | - |  |
| onTabScroll | tab 滚动时触发 | function({ direction: `left` \| `right` \| `top` \| `bottom` }) | - | 4.3.0 |

> 更多属性查看 rc-tabs tabs

#### TabItemType

| 参数        | 说明                                            | 类型      | 默认值 |
| ----------- | ----------------------------------------------- | --------- | ------ |
| closeIcon   | 自定义关闭图标，`在 type="editable-card"`时有效 | ReactNode | -      |
| disabled    | 禁用某一项                                      | boolean   | false  |
| forceRender | 被隐藏时是否渲染 DOM 结构                       | boolean   | false  |
| key         | 对应 activeKey                                  | string    | -      |
| label       | 选项卡头显示文字                                | ReactNode | -      |
| children    | 选项卡头显示内容                                | ReactNode | -      |

## 示例代码

### 基础用法（废弃的语法糖）

- demo: `deprecated`

默认选中第一项。

```tsx
import { Tabs } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Tabs defaultActiveKey="1">
    <Tabs.TabPane tab="Tab 1" key="1">
      Content of Tab Pane 1
    </Tabs.TabPane>
    <Tabs.TabPane tab="Tab 2" key="2">
      Content of Tab Pane 2
    </Tabs.TabPane>
    <Tabs.TabPane tab="Tab 3" key="3">
      Content of Tab Pane 3
    </Tabs.TabPane>
  </Tabs>
);

export default App;
```

### 基本

- demo: `basic`

默认选中第一项。

```tsx
import { Tabs } from 'antd';
import React from 'react';

const onChange = (key: string) => {
  console.log(key);
};

const App: React.FC = () => (
  <Tabs
    defaultActiveKey="1"
    onChange={onChange}
    items={[
      {
        label: `Tab 1`,
        key: '1',
        children: `Content of Tab Pane 1`,
      },
      {
        label: `Tab 2`,
        key: '2',
        children: `Content of Tab Pane 2`,
      },
      {
        label: `Tab 3`,
        key: '3',
        children: `Content of Tab Pane 3`,
      },
    ]}
  />
);

export default App;
```

### 禁用

- demo: `disabled`

禁用某一项。

```tsx
import { Tabs } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Tabs
    defaultActiveKey="1"
    items={[
      {
        label: 'Tab 1',
        key: '1',
        children: 'Tab 1',
      },
      {
        label: 'Tab 2',
        key: '2',
        children: 'Tab 2',
        disabled: true,
      },
      {
        label: 'Tab 3',
        key: '3',
        children: 'Tab 3',
      },
    ]}
  />
);

export default App;
```

### 居中

- demo: `centered`

标签居中展示。

```tsx
import { Tabs } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Tabs
    defaultActiveKey="1"
    centered
    items={new Array(3).fill(null).map((_, i) => {
      const id = String(i + 1);
      return {
        label: `Tab ${id}`,
        key: id,
        children: `Content of Tab Pane ${id}`,
      };
    })}
  />
);

export default App;
```

### 图标

- demo: `icon`

有图标的标签。

```tsx
import { AndroidOutlined, AppleOutlined } from '@ant-design/icons';
import { Tabs } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Tabs
    defaultActiveKey="2"
    items={[AppleOutlined, AndroidOutlined].map((Icon, i) => {
      const id = String(i + 1);

      return {
        label: (
          <span>
            <Icon />
            Tab {id}
          </span>
        ),
        key: id,
        children: `Tab ${id}`,
      };
    })}
  />
);

export default App;
```

### 滑动

- demo: `slide`

可以左右、上下滑动，容纳更多标签。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Radio, Tabs } from 'antd';
import React, { useState } from 'react';

type TabPosition = 'left' | 'right' | 'top' | 'bottom';

const App: React.FC = () => {
  const [mode, setMode] = useState<TabPosition>('top');

  const handleModeChange = (e: RadioChangeEvent) => {
    setMode(e.target.value);
  };

  return (
    <div>
      <Radio.Group onChange={handleModeChange} value={mode} style={{ marginBottom: 8 }}>
        <Radio.Button value="top">Horizontal</Radio.Button>
        <Radio.Button value="left">Vertical</Radio.Button>
      </Radio.Group>
      <Tabs
        defaultActiveKey="1"
        tabPosition={mode}
        style={{ height: 220 }}
        items={new Array(30).fill(null).map((_, i) => {
          const id = String(i);
          return {
            label: `Tab-${id}`,
            key: id,
            disabled: i === 28,
            children: `Content of tab ${id}`,
          };
        })}
      />
    </div>
  );
};

export default App;
```

### 附加内容

- demo: `extra`

可以在页签两边添加附加操作。

```tsx
import { Button, Checkbox, Divider, Tabs } from 'antd';
import React, { useMemo, useState } from 'react';

const CheckboxGroup = Checkbox.Group;

const operations = <Button>Extra Action</Button>;

const OperationsSlot: Record<PositionType, React.ReactNode> = {
  left: <Button className="tabs-extra-demo-button">Left Extra Action</Button>,
  right: <Button>Right Extra Action</Button>,
};

const options = ['left', 'right'];

type PositionType = 'left' | 'right';

const items = new Array(3).fill(null).map((_, i) => {
  const id = String(i + 1);
  return {
    label: `Tab ${id}`,
    key: id,
    children: `Content of tab ${id}`,
  };
});

const App: React.FC = () => {
  const [position, setPosition] = useState<PositionType[]>(['left', 'right']);

  const slot = useMemo(() => {
    if (position.length === 0) return null;

    return position.reduce(
      (acc, direction) => ({ ...acc, [direction]: OperationsSlot[direction] }),
      {},
    );
  }, [position]);

  return (
    <>
      <Tabs tabBarExtraContent={operations} items={items} />
      <br />
      <br />
      <br />
      <div>You can also specify its direction or both side</div>
      <Divider />
      <CheckboxGroup
        options={options}
        value={position}
        onChange={value => {
          setPosition(value as PositionType[]);
        }}
      />
      <br />
      <br />
      <Tabs tabBarExtraContent={slot} items={items} />
    </>
  );
};

export default App;
```

```css
.tabs-extra-demo-button {
  margin-right: 16px;
}

.ant-row-rtl .tabs-extra-demo-button {
  margin-right: 0;
  margin-left: 16px;
}
```

### 大小

- demo: `size`

大号页签用在页头区域，小号用在弹出框等较狭窄的容器内。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Radio, Tabs } from 'antd';
import type { SizeType } from 'antd/es/config-provider/SizeContext';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [size, setSize] = useState<SizeType>('small');

  const onChange = (e: RadioChangeEvent) => {
    setSize(e.target.value);
  };

  return (
    <div>
      <Radio.Group value={size} onChange={onChange} style={{ marginBottom: 16 }}>
        <Radio.Button value="small">Small</Radio.Button>
        <Radio.Button value="middle">Middle</Radio.Button>
        <Radio.Button value="large">Large</Radio.Button>
      </Radio.Group>
      <Tabs
        defaultActiveKey="1"
        size={size}
        style={{ marginBottom: 32 }}
        items={new Array(3).fill(null).map((_, i) => {
          const id = String(i + 1);
          return {
            label: `Tab ${id}`,
            key: id,
            children: `Content of tab ${id}`,
          };
        })}
      />
      <Tabs
        defaultActiveKey="1"
        type="card"
        size={size}
        items={new Array(3).fill(null).map((_, i) => {
          const id = String(i + 1);
          return {
            label: `Card Tab ${id}`,
            key: id,
            children: `Content of card tab ${id}`,
          };
        })}
      />
    </div>
  );
};

export default App;
```

### 位置

- demo: `position`

有四个位置，`tabPosition="left|right|top|bottom"`。在移动端下，`left|right` 会自动切换成 `top`。

```tsx
import type { RadioChangeEvent } from 'antd';
import { Radio, Space, Tabs } from 'antd';
import React, { useState } from 'react';

type TabPosition = 'left' | 'right' | 'top' | 'bottom';

const App: React.FC = () => {
  const [tabPosition, setTabPosition] = useState<TabPosition>('left');

  const changeTabPosition = (e: RadioChangeEvent) => {
    setTabPosition(e.target.value);
  };

  return (
    <>
      <Space style={{ marginBottom: 24 }}>
        Tab position:
        <Radio.Group value={tabPosition} onChange={changeTabPosition}>
          <Radio.Button value="top">top</Radio.Button>
          <Radio.Button value="bottom">bottom</Radio.Button>
          <Radio.Button value="left">left</Radio.Button>
          <Radio.Button value="right">right</Radio.Button>
        </Radio.Group>
      </Space>
      <Tabs
        tabPosition={tabPosition}
        items={new Array(3).fill(null).map((_, i) => {
          const id = String(i + 1);
          return {
            label: `Tab ${id}`,
            key: id,
            children: `Content of Tab ${id}`,
          };
        })}
      />
    </>
  );
};

export default App;
```

### 卡片式页签

- demo: `card`

另一种样式的页签，不提供对应的垂直样式。

```tsx
import { Tabs } from 'antd';
import React from 'react';

const onChange = (key: string) => {
  console.log(key);
};

const App: React.FC = () => (
  <Tabs
    onChange={onChange}
    type="card"
    items={new Array(3).fill(null).map((_, i) => {
      const id = String(i + 1);
      return {
        label: `Tab ${id}`,
        key: id,
        children: `Content of Tab Pane ${id}`,
      };
    })}
  />
);

export default App;
```

### 新增和关闭页签

- demo: `editable-card`

只有卡片样式的页签支持新增和关闭选项。使用 `closable={false}` 禁止关闭。

```tsx
import { Tabs } from 'antd';
import React, { useRef, useState } from 'react';

const initialItems = [
  { label: 'Tab 1', children: 'Content of Tab 1', key: '1' },
  { label: 'Tab 2', children: 'Content of Tab 2', key: '2' },
  {
    label: 'Tab 3',
    children: 'Content of Tab 3',
    key: '3',
    closable: false,
  },
];

const App: React.FC = () => {
  const [activeKey, setActiveKey] = useState(initialItems[0].key);
  const [items, setItems] = useState(initialItems);
  const newTabIndex = useRef(0);

  const onChange = (newActiveKey: string) => {
    setActiveKey(newActiveKey);
  };

  const add = () => {
    const newActiveKey = `newTab${newTabIndex.current++}`;
    const newPanes = [...items];
    newPanes.push({ label: 'New Tab', children: 'Content of new Tab', key: newActiveKey });
    setItems(newPanes);
    setActiveKey(newActiveKey);
  };

  const remove = (targetKey: string) => {
    let newActiveKey = activeKey;
    let lastIndex = -1;
    items.forEach((item, i) => {
      if (item.key === targetKey) {
        lastIndex = i - 1;
      }
    });
    const newPanes = items.filter(item => item.key !== targetKey);
    if (newPanes.length && newActiveKey === targetKey) {
      if (lastIndex >= 0) {
        newActiveKey = newPanes[lastIndex].key;
      } else {
        newActiveKey = newPanes[0].key;
      }
    }
    setItems(newPanes);
    setActiveKey(newActiveKey);
  };

  const onEdit = (targetKey: string, action: 'add' | 'remove') => {
    if (action === 'add') {
      add();
    } else {
      remove(targetKey);
    }
  };

  return (
    <Tabs
      type="editable-card"
      onChange={onChange}
      activeKey={activeKey}
      onEdit={onEdit}
      items={items}
    />
  );
};

export default App;
```

### 卡片式页签容器

- demo: `card-top`

用于容器顶部，需要一点额外的样式覆盖。

```tsx
import { Tabs } from 'antd';
import React from 'react';

const items = new Array(3).fill(null).map((_, i) => {
  const id = String(i + 1);
  return {
    label: `Tab Title ${id}`,
    key: id,
    children: (
      <>
        <p>Content of Tab Pane {id}</p>
        <p>Content of Tab Pane {id}</p>
        <p>Content of Tab Pane {id}</p>
      </>
    ),
  };
});

const App: React.FC = () => (
  <div className="card-container">
    <Tabs type="card" items={items} />
  </div>
);

export default App;
```

```css
.card-container p {
  margin: 0;
}
.card-container > .ant-tabs-card .ant-tabs-content {
  height: 120px;
  margin-top: -16px;
}
.card-container > .ant-tabs-card .ant-tabs-content > .ant-tabs-tabpane {
  padding: 16px;
  background: #fff;
}
.card-container > .ant-tabs-card > .ant-tabs-nav::before {
  display: none;
}
.card-container > .ant-tabs-card .ant-tabs-tab,
[data-theme='compact'] .card-container > .ant-tabs-card .ant-tabs-tab {
  background: transparent;
  border-color: transparent;
}
.card-container > .ant-tabs-card .ant-tabs-tab-active,
[data-theme='compact'] .card-container > .ant-tabs-card .ant-tabs-tab-active {
  background: #fff;
  border-color: #fff;
}
#components-tabs-demo-card-top .code-box-demo {
  padding: 24px;
  overflow: hidden;
  background: #f5f5f5;
}
[data-theme='compact'] .card-container > .ant-tabs-card .ant-tabs-content {
  height: 120px;
  margin-top: -8px;
}
[data-theme='dark'] .card-container > .ant-tabs-card .ant-tabs-tab {
  background: transparent;
  border-color: transparent;
}
[data-theme='dark'] #components-tabs-demo-card-top .code-box-demo {
  background: #000;
}
[data-theme='dark'] .card-container > .ant-tabs-card .ant-tabs-content > .ant-tabs-tabpane {
  background: #141414;
}
[data-theme='dark'] .card-container > .ant-tabs-card .ant-tabs-tab-active {
  background: #141414;
  border-color: #141414;
}
```

### 自定义新增页签触发器

- demo: `custom-add-trigger`

隐藏默认的页签增加图标，给自定义触发器绑定事件。

```tsx
import { Button, Tabs } from 'antd';
import React, { useRef, useState } from 'react';

const defaultPanes = new Array(2).fill(null).map((_, index) => {
  const id = String(index + 1);
  return { label: `Tab ${id}`, children: `Content of Tab Pane ${index + 1}`, key: id };
});

const App: React.FC = () => {
  const [activeKey, setActiveKey] = useState(defaultPanes[0].key);
  const [items, setItems] = useState(defaultPanes);
  const newTabIndex = useRef(0);

  const onChange = (key: string) => {
    setActiveKey(key);
  };

  const add = () => {
    const newActiveKey = `newTab${newTabIndex.current++}`;
    setItems([...items, { label: 'New Tab', children: 'New Tab Pane', key: newActiveKey }]);
    setActiveKey(newActiveKey);
  };

  const remove = (targetKey: string) => {
    const targetIndex = items.findIndex(pane => pane.key === targetKey);
    const newPanes = items.filter(pane => pane.key !== targetKey);
    if (newPanes.length && targetKey === activeKey) {
      const { key } = newPanes[targetIndex === newPanes.length ? targetIndex - 1 : targetIndex];
      setActiveKey(key);
    }
    setItems(newPanes);
  };

  const onEdit = (targetKey: string, action: 'add' | 'remove') => {
    if (action === 'add') {
      add();
    } else {
      remove(targetKey);
    }
  };

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Button onClick={add}>ADD</Button>
      </div>
      <Tabs
        hideAdd
        onChange={onChange}
        activeKey={activeKey}
        type="editable-card"
        onEdit={onEdit}
        items={items}
      />
    </div>
  );
};

export default App;
```

### 自定义页签头

- demo: `custom-tab-bar`

使用 react-sticky 组件实现吸顶效果。

```tsx
import type { TabsProps } from 'antd';
import { Tabs } from 'antd';
import React from 'react';
import { Sticky, StickyContainer } from 'react-sticky';

const renderTabBar: TabsProps['renderTabBar'] = (props, DefaultTabBar) => (
  <Sticky bottomOffset={80}>
    {({ style }) => (
      <DefaultTabBar {...props} className="site-custom-tab-bar" style={{ ...style }} />
    )}
  </Sticky>
);

const items = new Array(3).fill(null).map((_, i) => {
  const id = String(i + 1);
  return {
    label: `Tab ${id}`,
    key: id,
    children: `Content of Tab Pane ${id}`,
    style: i === 0 ? { height: 200 } : undefined,
  };
});

const App: React.FC = () => (
  <StickyContainer>
    <Tabs defaultActiveKey="1" renderTabBar={renderTabBar} items={items} />
  </StickyContainer>
);

export default App;
```

```css
.site-custom-tab-bar {
  z-index: 1;
  background: #fff;
}
```

### 可拖拽标签

- demo: `custom-tab-bar-node`

使用 `react-dnd@15+` 实现标签可拖拽。

```tsx
import type { TabsProps } from 'antd';
import { Tabs } from 'antd';
import React, { useRef, useState } from 'react';
import { DndProvider, useDrag, useDrop } from 'react-dnd';
import { HTML5Backend } from 'react-dnd-html5-backend';

const type = 'DraggableTabNode';
interface DraggableTabPaneProps extends React.HTMLAttributes<HTMLDivElement> {
  index: React.Key;
  moveNode: (dragIndex: React.Key, hoverIndex: React.Key) => void;
}

const DraggableTabNode = ({ index, children, moveNode }: DraggableTabPaneProps) => {
  const ref = useRef<HTMLDivElement>(null);
  const [{ isOver, dropClassName }, drop] = useDrop({
    accept: type,
    collect: monitor => {
      const { index: dragIndex } = monitor.getItem() || {};
      if (dragIndex === index) {
        return {};
      }
      return {
        isOver: monitor.isOver(),
        dropClassName: 'dropping',
      };
    },
    drop: (item: { index: React.Key }) => {
      moveNode(item.index, index);
    },
  });
  const [, drag] = useDrag({
    type,
    item: { index },
    collect: monitor => ({
      isDragging: monitor.isDragging(),
    }),
  });
  drop(drag(ref));

  return (
    <div ref={ref} style={{ marginRight: 24 }} className={isOver ? dropClassName : ''}>
      {children}
    </div>
  );
};

const DraggableTabs: React.FC<TabsProps> = props => {
  const { items = [] } = props;
  const [order, setOrder] = useState<React.Key[]>([]);

  const moveTabNode = (dragKey: React.Key, hoverKey: React.Key) => {
    const newOrder = order.slice();

    items.forEach(item => {
      if (item.key && newOrder.indexOf(item.key) === -1) {
        newOrder.push(item.key);
      }
    });

    const dragIndex = newOrder.indexOf(dragKey);
    const hoverIndex = newOrder.indexOf(hoverKey);

    newOrder.splice(dragIndex, 1);
    newOrder.splice(hoverIndex, 0, dragKey);

    setOrder(newOrder);
  };

  const renderTabBar: TabsProps['renderTabBar'] = (tabBarProps, DefaultTabBar) => (
    <DefaultTabBar {...tabBarProps}>
      {node => (
        <DraggableTabNode key={node.key} index={node.key!} moveNode={moveTabNode}>
          {node}
        </DraggableTabNode>
      )}
    </DefaultTabBar>
  );

  const orderItems = [...items].sort((a, b) => {
    const orderA = order.indexOf(a.key!);
    const orderB = order.indexOf(b.key!);

    if (orderA !== -1 && orderB !== -1) {
      return orderA - orderB;
    }
    if (orderA !== -1) {
      return -1;
    }
    if (orderB !== -1) {
      return 1;
    }

    const ia = items.indexOf(a);
    const ib = items.indexOf(b);

    return ia - ib;
  });

  return (
    <DndProvider backend={HTML5Backend}>
      <Tabs renderTabBar={renderTabBar} {...props} items={orderItems} />
    </DndProvider>
  );
};

const App: React.FC = () => (
  <DraggableTabs
    items={new Array(3).fill(null).map((_, i) => {
      const id = String(i + 1);
      return {
        label: `tab ${id}`,
        key: id,
        children: `Content of Tab Pane ${id}`,
      };
    })}
  />
);

export default App;
```

```css
.dropping {
  background: #fefefe;
  transition: all 0.3s;
}
```
