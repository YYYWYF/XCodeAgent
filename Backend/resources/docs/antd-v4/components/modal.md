---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "modal"
component: "Modal"
component_code_name: "Modal"
recommended_import: "import { Modal } from 'antd';"
subtitle_zh: "对话框"
type_zh: "反馈"
demo_count: 13
---

# Modal 对话框

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Modal`
- 推荐导入：`import { Modal } from 'antd';`
- 组件分类：反馈
- 简述：模态对话框。

## 示例索引

- 基本（basic）
- 异步关闭（async）
- 自定义页脚（footer）
- 确认对话框（confirm）
- 信息提示（info）
- 国际化（locale）
- 手动更新和移除（manual）
- 自定义位置（position）
- 销毁确认对话框（confirm-router）
- 自定义页脚按钮属性（button-props）
- 使用 hooks 获得上下文（hooks）
- 自定义渲染对话框（modal-render）
- 自定义模态的宽度（width）

## 使用文档

模态对话框。

### 何时使用

需要用户处理事务，又不希望跳转页面以致打断工作流程时，可以使用 `Modal` 在当前页面正中打开一个浮层，承载相应的操作。

另外当需要一个简洁的确认框询问用户时，可以使用 `Modal.confirm()` 等语法糖方法。

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| afterClose | Modal 完全关闭后的回调 | function | - |  |
| bodyStyle | Modal body 样式 | CSSProperties |  |  |
| cancelButtonProps | cancel 按钮 props | ButtonProps | - |  |
| cancelText | 取消按钮文字 | ReactNode | `取消` |  |
| centered | 垂直居中展示 Modal | boolean | false |  |
| closable | 是否显示右上角的关闭按钮 | boolean | true |  |
| closeIcon | 自定义关闭图标 | ReactNode | &lt;CloseOutlined /> |  |
| confirmLoading | 确定按钮 loading | boolean | false |  |
| destroyOnClose | 关闭时销毁 Modal 里的子元素 | boolean | false |  |
| focusTriggerAfterClose | 对话框关闭后是否需要聚焦触发元素 | boolean | true | 4.9.0 |
| footer | 底部内容，当不需要默认底部按钮时，可以设为 `footer={null}` | ReactNode | (确定取消按钮) |  |
| forceRender | 强制渲染 Modal | boolean | false |  |
| getContainer | 指定 Modal 挂载的节点，但依旧为全局展示，`false` 为挂载在当前位置。为符合 React 18 concurrent 标准，从 `4.20.x` 起 `getContainer` 需要始终返回唯一 dom 节点 | HTMLElement \| () => HTMLElement \| Selectors \| false | document.body |  |
| keyboard | 是否支持键盘 esc 关闭 | boolean | true |  |
| mask | 是否展示遮罩 | boolean | true |  |
| maskClosable | 点击蒙层是否允许关闭 | boolean | true |  |
| maskStyle | 遮罩样式 | CSSProperties |  |  |
| modalRender | 自定义渲染对话框 | (node: ReactNode) => ReactNode | - | 4.7.0 |
| okButtonProps | ok 按钮 props | ButtonProps | - |  |
| okText | 确认按钮文字 | ReactNode | `确定` |  |
| okType | 确认按钮类型 | string | `primary` |  |
| style | 可用于设置浮层的样式，调整浮层位置等 | CSSProperties | - |  |
| title | 标题 | ReactNode | - |  |
| open | 对话框是否可见，小于 4.23.0 使用 `visible`（为什么?） | boolean | - | 4.23.0 |
| width | 宽度 | string \| number | 520 |  |
| wrapClassName | 对话框外层容器的类名 | string | - |  |
| zIndex | 设置 Modal 的 `z-index` | number | 1000 |  |
| onCancel | 点击遮罩层或右上角叉或取消按钮的回调 | function(e) | - |  |
| onOk | 点击确定回调 | function(e) | - |  |

##### 注意

- `` 默认关闭后状态不会自动清空, 如果希望每次打开都是新内容，请设置 `destroyOnClose`。
- `` 和 Form 一起配合使用时，设置 `destroyOnClose` 也不会在 Modal 关闭时销毁表单字段数据，需要设置 ``。
- `Modal.method()` RTL 模式仅支持 hooks 用法。

#### Modal.method()

包括：

- `Modal.info`
- `Modal.success`
- `Modal.error`
- `Modal.warning`
- `Modal.confirm`

以上均为一个函数，参数为 object，具体属性如下：

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| afterClose | Modal 完全关闭后的回调 | function | - | 4.9.0 |
| autoFocusButton | 指定自动获得焦点的按钮 | null \| `ok` \| `cancel` | `ok` |  |
| bodyStyle | Modal body 样式 | CSSProperties |  | 4.8.0 |
| cancelButtonProps | cancel 按钮 props | ButtonProps | - |  |
| cancelText | 设置 Modal.confirm 取消按钮文字 | string | `取消` |  |
| centered | 垂直居中展示 Modal | boolean | false |  |
| className | 容器类名 | string | - |  |
| closable | 是否显示右上角的关闭按钮 | boolean | false | 4.9.0 |
| closeIcon | 自定义关闭图标 | ReactNode | undefined | 4.9.0 |
| content | 内容 | ReactNode | - |  |
| getContainer | 指定 Modal 挂载的 HTML 节点, false 为挂载在当前 dom | HTMLElement \| () => HTMLElement \| Selectors \| false | document.body |  |
| icon | 自定义图标 | ReactNode | &lt;QuestionCircle /> |  |
| keyboard | 是否支持键盘 esc 关闭 | boolean | true |  |
| mask | 是否展示遮罩 | boolean | true |  |
| maskClosable | 点击蒙层是否允许关闭 | boolean | false |  |
| maskStyle | 遮罩样式 | object | {} |  |
| okButtonProps | ok 按钮 props | ButtonProps | - |  |
| okText | 确认按钮文字 | string | `确定` |  |
| okType | 确认按钮类型 | string | `primary` |  |
| style | 可用于设置浮层的样式，调整浮层位置等 | CSSProperties | - |  |
| title | 标题 | ReactNode | - |  |
| width | 宽度 | string \| number | 416 |  |
| wrapClassName | 对话框外层容器的类名 | string | - | 4.18.0 |
| zIndex | 设置 Modal 的 `z-index` | number | 1000 |  |
| onCancel | 取消回调，参数为关闭函数，返回 promise 时 resolve 后自动关闭 | function(close) | - |  |
| onOk | 点击确定回调，参数为关闭函数，返回 promise 时 resolve 后自动关闭 | function(close) | - |  |

以上函数调用后，会返回一个引用，可以通过该引用更新和关闭弹窗。

```jsx
const modal = Modal.info();

modal.update({
  title: '修改的标题',
  content: '修改的内容',
});

// 在 4.8.0 或更高版本中，可以通过传入函数的方式更新弹窗
modal.update(prevConfig => ({
  ...prevConfig,
  title: `${prevConfig.title}（新）`,
}));

modal.destroy();
```

- `Modal.destroyAll`

使用 `Modal.destroyAll()` 可以销毁弹出的确认窗（即上述的 `Modal.info`、`Modal.success`、`Modal.error`、`Modal.warning`、`Modal.confirm`）。通常用于路由监听当中，处理路由前进、后退不能销毁确认对话框的问题，而不用各处去使用实例的返回值进行关闭（`modal.destroy()` 适用于主动关闭，而不是路由这样被动关闭）

```jsx
import { browserHistory } from 'react-router';

// router change
browserHistory.listen(() => {
  Modal.destroyAll();
});
```

#### Modal.useModal()

当你需要使用 Context 时，可以通过 `Modal.useModal` 创建一个 `contextHolder` 插入子节点中。通过 hooks 创建的临时 Modal 将会得到 `contextHolder` 所在位置的所有上下文。创建的 `modal` 对象拥有与 `Modal.method`>) 相同的创建通知方法。

```jsx
const [modal, contextHolder] = Modal.useModal();

React.useEffect(() => {
  modal.confirm({
    // ...
  });
}, []);

return <div>{contextHolder}</div>;
```

### FAQ

#### 为什么 Modal 关闭时，内容不会更新？

Modal 在关闭时会将内容进行 memo 从而避免关闭过程中的内容跳跃。也因此如果你在配合使用 Form 有关闭时重置 `initialValues` 的操作，请通过在 effect 中调用 `resetFields` 来重置。

#### 为什么 Modal 方法不能获取 context、redux、的内容和 ConfigProvider `locale/prefixCls` 配置？

直接调用 Modal 方法，antd 会通过 `ReactDOM.render` 动态创建新的 React 实体。其 context 与当前代码所在 context 并不相同，因而无法获取 context 信息。

当你需要 context 信息（例如 ConfigProvider 配置的内容）时，可以通过 `Modal.useModal` 方法会返回 `modal` 实体以及 `contextHolder` 节点。将其插入到你需要获取 context 位置即可：

```tsx
const [modal, contextHolder] = Modal.useModal();

return (
  <Context1.Provider value="Ant">
    {/* contextHolder 在 Context1 内，它可以获得 Context1 的 context */}
    {contextHolder}
    <Context2.Provider value="Design">
      {/* contextHolder 在 Context2 外，因而不会获得 Context2 的 context */}
    </Context2.Provider>
  </Context1.Provider>
);
```

**异同：**通过 hooks 创建的 `contextHolder` 必须插入到子元素节点中才会生效，当你不需要上下文信息时请直接调用。

#### 如何关闭 Modal 动画？

你可以通过 `transitionName=""` 和 `maskTransitionName=""` 去除动画 CSS，但是需要注意的是。该方法为内部方法，我们不保证下个大版本重构时该属性会被保留。

#### 静态方法如何设置 prefixCls ？

你可以通过 `ConfigProvider.config`-4.13.0+>) 进行设置。

## 示例代码

### 基本

- demo: `basic`

第一个对话框。

```tsx
import { Button, Modal } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState(false);

  const showModal = () => {
    setIsModalOpen(true);
  };

  const handleOk = () => {
    setIsModalOpen(false);
  };

  const handleCancel = () => {
    setIsModalOpen(false);
  };

  return (
    <>
      <Button type="primary" onClick={showModal}>
        Open Modal
      </Button>
      <Modal title="Basic Modal" open={isModalOpen} onOk={handleOk} onCancel={handleCancel}>
        <p>Some contents...</p>
        <p>Some contents...</p>
        <p>Some contents...</p>
      </Modal>
    </>
  );
};

export default App;
```

### 异步关闭

- demo: `async`

点击确定后异步关闭对话框，例如提交表单。

```tsx
import { Button, Modal } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [modalText, setModalText] = useState('Content of the modal');

  const showModal = () => {
    setOpen(true);
  };

  const handleOk = () => {
    setModalText('The modal will be closed after two seconds');
    setConfirmLoading(true);
    setTimeout(() => {
      setOpen(false);
      setConfirmLoading(false);
    }, 2000);
  };

  const handleCancel = () => {
    console.log('Clicked cancel button');
    setOpen(false);
  };

  return (
    <>
      <Button type="primary" onClick={showModal}>
        Open Modal with async logic
      </Button>
      <Modal
        title="Title"
        open={open}
        onOk={handleOk}
        confirmLoading={confirmLoading}
        onCancel={handleCancel}
      >
        <p>{modalText}</p>
      </Modal>
    </>
  );
};

export default App;
```

### 自定义页脚

- demo: `footer`

更复杂的例子，自定义了页脚的按钮，点击提交后进入 loading 状态，完成后关闭。

不需要默认确定取消按钮时，你可以把 `footer` 设为 `null`。

```tsx
import { Button, Modal } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  const showModal = () => {
    setOpen(true);
  };

  const handleOk = () => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      setOpen(false);
    }, 3000);
  };

  const handleCancel = () => {
    setOpen(false);
  };

  return (
    <>
      <Button type="primary" onClick={showModal}>
        Open Modal with customized footer
      </Button>
      <Modal
        open={open}
        title="Title"
        onOk={handleOk}
        onCancel={handleCancel}
        footer={[
          <Button key="back" onClick={handleCancel}>
            Return
          </Button>,
          <Button key="submit" type="primary" loading={loading} onClick={handleOk}>
            Submit
          </Button>,
          <Button
            key="link"
            href="https://google.com"
            type="primary"
            loading={loading}
            onClick={handleOk}
          >
            Search on Google
          </Button>,
        ]}
      >
        <p>Some contents...</p>
        <p>Some contents...</p>
        <p>Some contents...</p>
        <p>Some contents...</p>
        <p>Some contents...</p>
      </Modal>
    </>
  );
};

export default App;
```

### 确认对话框

- demo: `confirm`

使用 `confirm()` 可以快捷地弹出确认框。onCancel/onOk 返回 promise 可以延迟关闭。

```tsx
import { ExclamationCircleOutlined } from '@ant-design/icons';
import { Button, Modal, Space } from 'antd';
import React from 'react';

const { confirm } = Modal;

const showConfirm = () => {
  confirm({
    title: 'Do you Want to delete these items?',
    icon: <ExclamationCircleOutlined />,
    content: 'Some descriptions',
    onOk() {
      console.log('OK');
    },
    onCancel() {
      console.log('Cancel');
    },
  });
};

const showPromiseConfirm = () => {
  confirm({
    title: 'Do you want to delete these items?',
    icon: <ExclamationCircleOutlined />,
    content: 'When clicked the OK button, this dialog will be closed after 1 second',
    onOk() {
      return new Promise((resolve, reject) => {
        setTimeout(Math.random() > 0.5 ? resolve : reject, 1000);
      }).catch(() => console.log('Oops errors!'));
    },
    onCancel() {},
  });
};

const showDeleteConfirm = () => {
  confirm({
    title: 'Are you sure delete this task?',
    icon: <ExclamationCircleOutlined />,
    content: 'Some descriptions',
    okText: 'Yes',
    okType: 'danger',
    cancelText: 'No',
    onOk() {
      console.log('OK');
    },
    onCancel() {
      console.log('Cancel');
    },
  });
};

const showPropsConfirm = () => {
  confirm({
    title: 'Are you sure delete this task?',
    icon: <ExclamationCircleOutlined />,
    content: 'Some descriptions',
    okText: 'Yes',
    okType: 'danger',
    okButtonProps: {
      disabled: true,
    },
    cancelText: 'No',
    onOk() {
      console.log('OK');
    },
    onCancel() {
      console.log('Cancel');
    },
  });
};

const App: React.FC = () => (
  <Space wrap>
    <Button onClick={showConfirm}>Confirm</Button>
    <Button onClick={showPromiseConfirm}>With promise</Button>
    <Button onClick={showDeleteConfirm} type="dashed">
      Delete
    </Button>
    <Button onClick={showPropsConfirm} type="dashed">
      With extra props
    </Button>
  </Space>
);

export default App;
```

### 信息提示

- demo: `info`

各种类型的信息提示，只提供一个按钮用于关闭。

```tsx
import { Button, Modal, Space } from 'antd';
import React from 'react';

const info = () => {
  Modal.info({
    title: 'This is a notification message',
    content: (
      <div>
        <p>some messages...some messages...</p>
        <p>some messages...some messages...</p>
      </div>
    ),
    onOk() {},
  });
};

const success = () => {
  Modal.success({
    content: 'some messages...some messages...',
  });
};

const error = () => {
  Modal.error({
    title: 'This is an error message',
    content: 'some messages...some messages...',
  });
};

const warning = () => {
  Modal.warning({
    title: 'This is a warning message',
    content: 'some messages...some messages...',
  });
};

const App: React.FC = () => (
  <Space wrap>
    <Button onClick={info}>Info</Button>
    <Button onClick={success}>Success</Button>
    <Button onClick={error}>Error</Button>
    <Button onClick={warning}>Warning</Button>
  </Space>
);

export default App;
```

### 国际化

- demo: `locale`

设置 `okText` 与 `cancelText` 以自定义按钮文字。

```tsx
import { ExclamationCircleOutlined } from '@ant-design/icons';
import { Button, Modal, Space } from 'antd';
import React, { useState } from 'react';

const LocalizedModal = () => {
  const [open, setOpen] = useState(false);

  const showModal = () => {
    setOpen(true);
  };

  const hideModal = () => {
    setOpen(false);
  };

  return (
    <>
      <Button type="primary" onClick={showModal}>
        Modal
      </Button>
      <Modal
        title="Modal"
        open={open}
        onOk={hideModal}
        onCancel={hideModal}
        okText="确认"
        cancelText="取消"
      >
        <p>Bla bla ...</p>
        <p>Bla bla ...</p>
        <p>Bla bla ...</p>
      </Modal>
    </>
  );
};

const confirm = () => {
  Modal.confirm({
    title: 'Confirm',
    icon: <ExclamationCircleOutlined />,
    content: 'Bla bla ...',
    okText: '确认',
    cancelText: '取消',
  });
};

const App: React.FC = () => (
  <Space>
    <LocalizedModal />
    <Button onClick={confirm}>Confirm</Button>
  </Space>
);

export default App;
```

### 手动更新和移除

- demo: `manual`

手动更新和关闭 `Modal.method` 方式创建的对话框。

```tsx
import { Button, Modal } from 'antd';
import React from 'react';

const countDown = () => {
  let secondsToGo = 5;

  const modal = Modal.success({
    title: 'This is a notification message',
    content: `This modal will be destroyed after ${secondsToGo} second.`,
  });

  const timer = setInterval(() => {
    secondsToGo -= 1;
    modal.update({
      content: `This modal will be destroyed after ${secondsToGo} second.`,
    });
  }, 1000);

  setTimeout(() => {
    clearInterval(timer);
    modal.destroy();
  }, secondsToGo * 1000);
};

const App: React.FC = () => <Button onClick={countDown}>Open modal to close in 5s</Button>;

export default App;
```

### 自定义位置

- demo: `position`

使用 `centered` 或类似 `style.top` 的样式来设置对话框位置。

```tsx
import { Button, Modal } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [modal1Open, setModal1Open] = useState(false);
  const [modal2Open, setModal2Open] = useState(false);

  return (
    <>
      <Button type="primary" onClick={() => setModal1Open(true)}>
        Display a modal dialog at 20px to Top
      </Button>
      <Modal
        title="20px to Top"
        style={{ top: 20 }}
        visible={modal1Open}
        onOk={() => setModal1Open(false)}
        onCancel={() => setModal1Open(false)}
      >
        <p>some contents...</p>
        <p>some contents...</p>
        <p>some contents...</p>
      </Modal>
      <br />
      <br />
      <Button type="primary" onClick={() => setModal2Open(true)}>
        Vertically centered modal dialog
      </Button>
      <Modal
        title="Vertically centered modal dialog"
        centered
        visible={modal2Open}
        onOk={() => setModal2Open(false)}
        onCancel={() => setModal2Open(false)}
      >
        <p>some contents...</p>
        <p>some contents...</p>
        <p>some contents...</p>
      </Modal>
    </>
  );
};

export default App;
```

### 销毁确认对话框

- demo: `confirm-router`

使用 `Modal.destroyAll()` 可以销毁弹出的确认窗。通常用于路由监听当中，处理路由前进、后退不能销毁确认对话框的问题。

```tsx
import { ExclamationCircleOutlined } from '@ant-design/icons';
import { Button, Modal } from 'antd';
import React from 'react';

const { confirm } = Modal;

const destroyAll = () => {
  Modal.destroyAll();
};

const showConfirm = () => {
  for (let i = 0; i < 3; i += 1) {
    setTimeout(() => {
      confirm({
        icon: <ExclamationCircleOutlined />,
        content: <Button onClick={destroyAll}>Click to destroy all</Button>,
        onOk() {
          console.log('OK');
        },
        onCancel() {
          console.log('Cancel');
        },
      });
    }, i * 500);
  }
};

const App: React.FC = () => <Button onClick={showConfirm}>Confirm</Button>;

export default App;
```

### 自定义页脚按钮属性

- demo: `button-props`

传入 `okButtonProps` 和 `cancelButtonProps` 可分别自定义确定按钮和取消按钮的 props。

```tsx
import { Button, Modal } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [open, setOpen] = useState(false);

  const showModal = () => {
    setOpen(true);
  };

  const handleOk = (e: React.MouseEvent<HTMLElement>) => {
    console.log(e);
    setOpen(false);
  };

  const handleCancel = (e: React.MouseEvent<HTMLElement>) => {
    console.log(e);
    setOpen(false);
  };

  return (
    <>
      <Button type="primary" onClick={showModal}>
        Open Modal with customized button props
      </Button>
      <Modal
        title="Basic Modal"
        open={open}
        onOk={handleOk}
        onCancel={handleCancel}
        okButtonProps={{ disabled: true }}
        cancelButtonProps={{ disabled: true }}
      >
        <p>Some contents...</p>
        <p>Some contents...</p>
        <p>Some contents...</p>
      </Modal>
    </>
  );
};

export default App;
```

### 使用 hooks 获得上下文

- demo: `hooks`

通过 `Modal.useModal` 创建支持读取 context 的 `contextHolder`。

```tsx
import { Button, Modal, Space } from 'antd';
import React, { createContext } from 'react';

const ReachableContext = createContext<string | null>(null);
const UnreachableContext = createContext<string | null>(null);

const config = {
  title: 'Use Hook!',
  content: (
    <>
      <ReachableContext.Consumer>{name => `Reachable: ${name}!`}</ReachableContext.Consumer>
      <br />
      <UnreachableContext.Consumer>{name => `Unreachable: ${name}!`}</UnreachableContext.Consumer>
    </>
  ),
};

const App: React.FC = () => {
  const [modal, contextHolder] = Modal.useModal();

  return (
    <ReachableContext.Provider value="Light">
      <Space>
        <Button
          onClick={() => {
            modal.confirm(config);
          }}
        >
          Confirm
        </Button>
        <Button
          onClick={() => {
            modal.warning(config);
          }}
        >
          Warning
        </Button>
        <Button
          onClick={() => {
            modal.info(config);
          }}
        >
          Info
        </Button>
        <Button
          onClick={() => {
            modal.error(config);
          }}
        >
          Error
        </Button>
      </Space>
      {/* `contextHolder` should always be placed under the context you want to access */}
      {contextHolder}

      {/* Can not access this context since `contextHolder` is not in it */}
      <UnreachableContext.Provider value="Bamboo" />
    </ReachableContext.Provider>
  );
};

export default App;
```

### 自定义渲染对话框

- demo: `modal-render`

自定义渲染对话框, 可通过 `react-draggable` 来实现拖拽。

```tsx
import { Button, Modal } from 'antd';
import React, { useRef, useState } from 'react';
import type { DraggableData, DraggableEvent } from 'react-draggable';
import Draggable from 'react-draggable';

const App: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [disabled, setDisabled] = useState(true);
  const [bounds, setBounds] = useState({ left: 0, top: 0, bottom: 0, right: 0 });
  const draggleRef = useRef<HTMLDivElement>(null);

  const showModal = () => {
    setOpen(true);
  };

  const handleOk = (e: React.MouseEvent<HTMLElement>) => {
    console.log(e);
    setOpen(false);
  };

  const handleCancel = (e: React.MouseEvent<HTMLElement>) => {
    console.log(e);
    setOpen(false);
  };

  const onStart = (_event: DraggableEvent, uiData: DraggableData) => {
    const { clientWidth, clientHeight } = window.document.documentElement;
    const targetRect = draggleRef.current?.getBoundingClientRect();
    if (!targetRect) {
      return;
    }
    setBounds({
      left: -targetRect.left + uiData.x,
      right: clientWidth - (targetRect.right - uiData.x),
      top: -targetRect.top + uiData.y,
      bottom: clientHeight - (targetRect.bottom - uiData.y),
    });
  };

  return (
    <>
      <Button onClick={showModal}>Open Draggable Modal</Button>
      <Modal
        title={
          <div
            style={{
              width: '100%',
              cursor: 'move',
            }}
            onMouseOver={() => {
              if (disabled) {
                setDisabled(false);
              }
            }}
            onMouseOut={() => {
              setDisabled(true);
            }}
            // fix eslintjsx-a11y/mouse-events-have-key-events
            // https://github.com/jsx-eslint/eslint-plugin-jsx-a11y/blob/master/docs/rules/mouse-events-have-key-events.md
            onFocus={() => {}}
            onBlur={() => {}}
            // end
          >
            Draggable Modal
          </div>
        }
        open={open}
        onOk={handleOk}
        onCancel={handleCancel}
        modalRender={modal => (
          <Draggable
            disabled={disabled}
            bounds={bounds}
            onStart={(event, uiData) => onStart(event, uiData)}
          >
            <div ref={draggleRef}>{modal}</div>
          </Draggable>
        )}
      >
        <p>
          Just don&apos;t learn physics at school and your life will be full of magic and miracles.
        </p>
        <br />
        <p>Day before yesterday I saw a rabbit, and yesterday a deer, and today, you.</p>
      </Modal>
    </>
  );
};

export default App;
```

### 自定义模态的宽度

- demo: `width`

使用 `width` 来设置模态对话框的宽度。

```tsx
import { Button, Modal } from 'antd';
import React, { useState } from 'react';

const App: React.FC = () => {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button type="primary" onClick={() => setOpen(true)}>
        Open Modal of 1000px width
      </Button>
      <Modal
        title="Modal 1000px width"
        centered
        open={open}
        onOk={() => setOpen(false)}
        onCancel={() => setOpen(false)}
        width={1000}
      >
        <p>some contents...</p>
        <p>some contents...</p>
        <p>some contents...</p>
      </Modal>
    </>
  );
};

export default App;
```
