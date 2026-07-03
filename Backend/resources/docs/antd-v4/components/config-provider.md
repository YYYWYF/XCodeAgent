---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "config-provider"
component: "ConfigProvider"
component_code_name: "ConfigProvider"
recommended_import: "import { ConfigProvider } from 'antd';"
subtitle_zh: "全局化配置"
type_zh: "其他"
demo_count: 4
---

# ConfigProvider 全局化配置

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`ConfigProvider`
- 推荐导入：`import { ConfigProvider } from 'antd';`
- 组件分类：其他
- 简述：为组件提供统一的全局化配置。

## 示例索引

- 国际化（locale）
- 方向（direction）
- 组件尺寸（size）
- 全局样式（theme）

## 使用文档

为组件提供统一的全局化配置。

### 使用

ConfigProvider 使用 React 的 context 特性，只需在应用外围包裹一次即可全局生效。

```jsx
import { ConfigProvider } from 'antd';

// ...

export default () => (
  <ConfigProvider direction="rtl">
    <App />
  </ConfigProvider>
);
```

#### Content Security Policy

部分组件为了支持波纹效果，使用了动态样式。如果开启了 Content Security Policy (CSP)，你可以通过 `csp` 属性来进行配置：

```jsx
<ConfigProvider csp={{ nonce: 'YourNonceCode' }}>
  <Button>My Button</Button>
</ConfigProvider>
```

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| autoInsertSpaceInButton | 设置为 `false` 时，移除按钮中 2 个汉字之间的空格 | boolean | true |  |
| componentDisabled | 设置 antd 组件禁用状态 | boolean | - | 4.21.0 |
| componentSize | 设置 antd 组件大小 | `small` \| `middle` \| `large` | - |  |
| csp | 设置 Content Security Policy 配置 | { nonce: string } | - |  |
| direction | 设置文本展示方向。 示例 | `ltr` \| `rtl` | `ltr` |  |
| dropdownMatchSelectWidth | 下拉菜单和选择器同宽。默认将设置 `min-width`，当值小于选择框宽度时会被忽略。`false` 时会关闭虚拟滚动 | boolean \| number | - | 4.3.0 |
| form | 设置 Form 组件的通用属性 | { validateMessages?: ValidateMessages, requiredMark?: boolean \| `optional`, colon?: boolean} | - | requiredMark: 4.8.0; colon: 4.18.0 |
| getPopupContainer | 弹出框（Select, Tooltip, Menu 等等）渲染父节点，默认渲染到 body 上。 | function(triggerNode) | () => document.body |  |
| getTargetContainer | 配置 Affix、Anchor 滚动监听容器。 | () => HTMLElement | () => window | 4.2.0 |
| iconPrefixCls | 设置图标统一样式前缀。注意：需要配合 `less` 变量 @iconfont-css-prefix 使用 | string | `anticon` | 4.11.0 |
| input | 设置 Input 组件的通用属性 | { autoComplete?: string } | - | 4.2.0 |
| locale | 语言包配置，语言包可到 antd/es/locale 目录下寻找 | object | - |  |
| pageHeader | 统一设置 PageHeader 的 ghost，参考 PageHeader | { ghost: boolean } | true |  |
| prefixCls | 设置统一样式前缀。注意：需要配合 `less` 变量 @ant-prefix 使用 | string | `ant` |  |
| renderEmpty | 自定义组件空状态。参考 空状态 | function(componentName: string): ReactNode | - |  |
| space | 设置 Space 的 `size`，参考 Space | { size: `small` \| `middle` \| `large` \| `number` } | - | 4.1.0 |
| virtual | 设置 `false` 时关闭虚拟滚动 | boolean | - | 4.3.0 |

#### ConfigProvider.config() `4.13.0+`

设置 `Modal`、`Message`、`Notification` rootPrefixCls。

```jsx
ConfigProvider.config({
  prefixCls: 'ant', // 4.13.0+
  iconPrefixCls: 'anticon', // 4.17.0+
});
```

### FAQ

##### 如何增加一个新的语言包？

参考《增加语言包》。

##### 为什么时间类组件的国际化 locale 设置不生效？

参考 FAQ 为什么时间类组件的国际化 locale 设置不生效？。

##### 配置 `getPopupContainer` 导致 Modal 报错？

相关 issue：

当如下全局设置 `getPopupContainer` 为触发节点的 parentNode 时，由于 Modal 的用法不存在 `triggerNode`，这样会导致 `triggerNode is undefined` 的报错，需要增加一个判断条件。

```diff
 <ConfigProvider
-  getPopupContainer={triggerNode => triggerNode.parentNode}
+  getPopupContainer={node => {
+    if (node) {
+      return node.parentNode;
+    }
+    return document.body;
+  }}
 >
   <App />
 </ConfigProvider>
```

## 示例代码

### 国际化

- demo: `locale`

此处列出 Ant Design 中需要国际化支持的组件，你可以在演示里切换语言。

```tsx
import type { RadioChangeEvent } from 'antd';
import {
  Button,
  Calendar,
  ConfigProvider,
  DatePicker,
  Modal,
  Pagination,
  Popconfirm,
  Radio,
  Select,
  Table,
  TimePicker,
  Transfer,
} from 'antd';
import enUS from 'antd/es/locale/en_US';
import zhCN from 'antd/es/locale/zh_CN';
import moment from 'moment';
import 'moment/locale/zh-cn';
import React, { useState } from 'react';

moment.locale('en');

const { Option } = Select;
const { RangePicker } = DatePicker;

const columns = [
  {
    title: 'Name',
    dataIndex: 'name',
    filters: [
      {
        text: 'filter1',
        value: 'filter1',
      },
    ],
  },
  {
    title: 'Age',
    dataIndex: 'age',
  },
];

const Page = () => {
  const [open, setOpen] = useState(false);

  const showModal = () => {
    setOpen(true);
  };

  const hideModal = () => {
    setOpen(false);
  };

  const info = () => {
    Modal.info({
      title: 'some info',
      content: 'some info',
    });
  };

  const confirm = () => {
    Modal.confirm({
      title: 'some info',
      content: 'some info',
    });
  };

  return (
    <div className="locale-components">
      <div className="example">
        <Pagination defaultCurrent={1} total={50} showSizeChanger />
      </div>
      <div className="example">
        <Select showSearch style={{ width: 200 }}>
          <Option value="jack">jack</Option>
          <Option value="lucy">lucy</Option>
        </Select>
        <DatePicker />
        <TimePicker />
        <RangePicker style={{ width: 200 }} />
      </div>
      <div className="example">
        <Button type="primary" onClick={showModal}>
          Show Modal
        </Button>
        <Button onClick={info}>Show info</Button>
        <Button onClick={confirm}>Show confirm</Button>
        <Popconfirm title="Question?">
          <a href="#">Click to confirm</a>
        </Popconfirm>
      </div>
      <div className="example">
        <Transfer dataSource={[]} showSearch targetKeys={[]} />
      </div>
      <div className="site-config-provider-calendar-wrapper">
        <Calendar fullscreen={false} value={moment()} />
      </div>
      <div className="example">
        <Table dataSource={[]} columns={columns} />
      </div>
      <Modal title="Locale Modal" open={open} onCancel={hideModal}>
        <p>Locale Modal</p>
      </Modal>
    </div>
  );
};

const App: React.FC = () => {
  const [locale, setLocal] = useState(enUS);

  const changeLocale = (e: RadioChangeEvent) => {
    const localeValue = e.target.value;
    setLocal(localeValue);
    if (!localeValue) {
      moment.locale('en');
    } else {
      moment.locale('zh-cn');
    }
  };

  return (
    <div>
      <div className="change-locale">
        <span style={{ marginRight: 16 }}>Change locale of components: </span>
        <Radio.Group value={locale} onChange={changeLocale}>
          <Radio.Button key="en" value={enUS}>
            English
          </Radio.Button>
          <Radio.Button key="cn" value={zhCN}>
            中文
          </Radio.Button>
        </Radio.Group>
      </div>
      <ConfigProvider locale={locale}>
        <Page
          key={locale ? locale.locale : 'en' /* Have to refresh for production environment */}
        />
      </ConfigProvider>
    </div>
  );
};

export default App;
```

```css
.site-config-provider-calendar-wrapper {
  width: 319px;
  border: 1px solid #d9d9d9;
  border-radius: 2px;
}

.locale-components {
  padding-top: 16px;
  border-top: 1px solid #d9d9d9;
}

.code-box-demo .example {
  margin: 16px 0;
}

.code-box-demo .example > * {
  margin-right: 8px;
}

.change-locale {
  margin-bottom: 16px;
}
```

### 方向

- demo: `direction`

这里列出了支持 `rtl` 方向的组件，您可以在演示中切换方向。

```tsx
import {
  DownloadOutlined,
  LeftOutlined,
  MinusOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined as SearchIcon,
  SmileOutlined,
} from '@ant-design/icons';
import type { RadioChangeEvent } from 'antd';
import {
  Badge,
  Button,
  Cascader,
  Col,
  ConfigProvider,
  Divider,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Radio,
  Rate,
  Row,
  Select,
  Steps,
  Switch,
  Tree,
  TreeSelect,
} from 'antd';
import type { DirectionType } from 'antd/es/config-provider';
import React, { useState } from 'react';

const InputGroup = Input.Group;
const ButtonGroup = Button.Group;
const { Option } = Select;
const { TreeNode } = Tree;
const { Search } = Input;

const cascaderOptions = [
  {
    value: 'tehran',
    label: 'تهران',
    children: [
      {
        value: 'tehran-c',
        label: 'تهران',
        children: [
          {
            value: 'saadat-abad',
            label: 'سعادت آیاد',
          },
        ],
      },
    ],
  },
  {
    value: 'ardabil',
    label: 'اردبیل',
    children: [
      {
        value: 'ardabil-c',
        label: 'اردبیل',
        children: [
          {
            value: 'primadar',
            label: 'پیرمادر',
          },
        ],
      },
    ],
  },
  {
    value: 'gilan',
    label: 'گیلان',
    children: [
      {
        value: 'rasht',
        label: 'رشت',
        children: [
          {
            value: 'district-3',
            label: 'منطقه ۳',
          },
        ],
      },
    ],
  },
];
type Placement = 'bottomLeft' | 'bottomRight' | 'topLeft' | 'topRight';

const Page: React.FC<{ popupPlacement: Placement }> = ({ popupPlacement }) => {
  const [currentStep, setCurrentStep] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [badgeCount, setBadgeCount] = useState(5);
  const [showBadge, setShowBadge] = useState(true);

  const selectBefore = (
    <Select defaultValue="Http://" style={{ width: 90 }}>
      <Option value="Http://">Http://</Option>
      <Option value="Https://">Https://</Option>
    </Select>
  );

  const selectAfter = (
    <Select defaultValue=".com" style={{ width: 80 }}>
      <Option value=".com">.com</Option>
      <Option value=".jp">.jp</Option>
      <Option value=".cn">.cn</Option>
      <Option value=".org">.org</Option>
    </Select>
  );

  // ==== Cascader ====
  const cascaderFilter = (inputValue: string, path: { label: string }[]) =>
    path.some(option => option.label.toLowerCase().indexOf(inputValue.toLowerCase()) > -1);

  const onCascaderChange = (value: any) => {
    console.log(value);
  };
  // ==== End Cascader ====

  // ==== Modal ====
  const showModal = () => {
    setModalOpen(true);
  };

  const handleOk = (e: React.MouseEvent<HTMLElement>) => {
    console.log(e);
    setModalOpen(false);
  };

  const handleCancel = (e: React.MouseEvent<HTMLElement>) => {
    console.log(e);
    setModalOpen(false);
  };

  // ==== End Modal ====

  const onStepsChange = (newCurrentStep: number) => {
    console.log('onChange:', newCurrentStep);
    setCurrentStep(newCurrentStep);
  };

  // ==== Badge ====

  const increaseBadge = () => {
    setBadgeCount(badgeCount + 1);
  };

  const declineBadge = () => {
    let newBadgeCount = badgeCount - 1;
    if (newBadgeCount < 0) {
      newBadgeCount = 0;
    }
    setBadgeCount(newBadgeCount);
  };

  const onChangeBadge = (checked: boolean) => {
    setShowBadge(checked);
  };
  // ==== End Badge ====

  return (
    <div className="direction-components">
      <Row>
        <Col span={24}>
          <Divider orientation="left">Cascader example</Divider>
          <Cascader
            suffixIcon={<SearchIcon />}
            options={cascaderOptions}
            onChange={onCascaderChange}
            placeholder="یک مورد انتخاب کنید"
            popupPlacement={popupPlacement}
          />
          &nbsp;&nbsp;&nbsp;&nbsp; With search:
          <Cascader
            suffixIcon={<SmileOutlined />}
            options={cascaderOptions}
            onChange={onCascaderChange}
            placeholder="Select an item"
            popupPlacement={popupPlacement}
            showSearch={{ filter: cascaderFilter }}
          />
        </Col>
      </Row>
      <br />
      <Row>
        <Col span={12}>
          <Divider orientation="left">Switch example</Divider>
          &nbsp;&nbsp;
          <Switch defaultChecked />
          &nbsp;&nbsp;
          <Switch loading defaultChecked />
          &nbsp;&nbsp;
          <Switch size="small" loading />
        </Col>
        <Col span={12}>
          <Divider orientation="left">Radio Group example</Divider>

          <Radio.Group defaultValue="c" buttonStyle="solid">
            <Radio.Button value="a">تهران</Radio.Button>
            <Radio.Button value="b" disabled>
              اصفهان
            </Radio.Button>
            <Radio.Button value="c">فارس</Radio.Button>
            <Radio.Button value="d">خوزستان</Radio.Button>
          </Radio.Group>
        </Col>
      </Row>
      <br />
      <Row>
        <Col span={12}>
          <Divider orientation="left">Button example</Divider>
          <div className="button-demo">
            <Button type="primary" icon={<DownloadOutlined />} />
            <Button type="primary" shape="circle" icon={<DownloadOutlined />} />
            <Button type="primary" shape="round" icon={<DownloadOutlined />} />
            <Button type="primary" shape="round" icon={<DownloadOutlined />}>
              Download
            </Button>
            <Button type="primary" icon={<DownloadOutlined />}>
              Download
            </Button>
            <br />
            <Button.Group>
              <Button type="primary">
                <LeftOutlined />
                Backward
              </Button>
              <Button type="primary">
                Forward
                <RightOutlined />
              </Button>
            </Button.Group>
            <Button type="primary" loading>
              Loading
            </Button>
            <Button type="primary" size="small" loading>
              Loading
            </Button>
          </div>
        </Col>
        <Col span={12}>
          <Divider orientation="left">Tree example</Divider>
          <Tree
            showLine
            checkable
            defaultExpandedKeys={['0-0-0', '0-0-1']}
            defaultSelectedKeys={['0-0-0', '0-0-1']}
            defaultCheckedKeys={['0-0-0', '0-0-1']}
          >
            <TreeNode title="parent 1" key="0-0">
              <TreeNode title="parent 1-0" key="0-0-0" disabled>
                <TreeNode title="leaf" key="0-0-0-0" disableCheckbox />
                <TreeNode title="leaf" key="0-0-0-1" />
              </TreeNode>
              <TreeNode title="parent 1-1" key="0-0-1">
                <TreeNode title={<span style={{ color: '#1890ff' }}>sss</span>} key="0-0-1-0" />
              </TreeNode>
            </TreeNode>
          </Tree>
        </Col>
      </Row>
      <br />
      <Row>
        <Col span={24}>
          <Divider orientation="left">Input (Input Group) example</Divider>
          <InputGroup size="large">
            <Row gutter={8}>
              <Col span={5}>
                <Input defaultValue="0571" />
              </Col>
              <Col span={8}>
                <Input defaultValue="26888888" />
              </Col>
            </Row>
          </InputGroup>
          <br />
          <InputGroup compact>
            <Input style={{ width: '20%' }} defaultValue="0571" />
            <Input style={{ width: '30%' }} defaultValue="26888888" />
          </InputGroup>
          <br />
          <InputGroup compact>
            <Select defaultValue="Option1">
              <Option value="Option1">Option1</Option>
              <Option value="Option2">Option2</Option>
            </Select>
            <Input style={{ width: '50%' }} defaultValue="input content" />
            <InputNumber />
          </InputGroup>
          <br />
          <Search placeholder="input search text" enterButton="Search" size="large" />
          <br />
          <br />
          <div style={{ marginBottom: 16 }}>
            <Input addonBefore={selectBefore} addonAfter={selectAfter} defaultValue="mysite" />
          </div>
          <br />
          <Row>
            <Col span={12}>
              <Divider orientation="left">Select example</Divider>
              <Select mode="multiple" defaultValue="مورچه" style={{ width: 120 }}>
                <Option value="jack">Jack</Option>
                <Option value="مورچه">مورچه</Option>
                <Option value="disabled" disabled>
                  Disabled
                </Option>
                <Option value="Yiminghe">yiminghe</Option>
              </Select>
              <Select defaultValue="مورچه" style={{ width: 120 }} disabled>
                <Option value="مورچه">مورچه</Option>
              </Select>
              <Select defaultValue="مورچه" style={{ width: 120 }} loading>
                <Option value="مورچه">مورچه</Option>
              </Select>
              <Select
                showSearch
                style={{ width: 200 }}
                placeholder="Select a person"
                optionFilterProp="children"
                filterOption={(input, option) =>
                  option?.props.children.toLowerCase().indexOf(input.toLowerCase()) >= 0
                }
              >
                <Option value="jack">Jack</Option>
                <Option value="سعید">سعید</Option>
                <Option value="tom">Tom</Option>
              </Select>
            </Col>
            <Col span={12}>
              <Divider orientation="left">TreeSelect example</Divider>
              <div>
                <TreeSelect
                  showSearch
                  style={{ width: '100%' }}
                  dropdownStyle={{ maxHeight: 400, overflow: 'auto' }}
                  placeholder="Please select"
                  allowClear
                  treeDefaultExpandAll
                >
                  <TreeNode title="parent 1" key="0-1">
                    <TreeNode title="parent 1-0" key="0-1-1">
                      <TreeNode title="my leaf" key="random" />
                      <TreeNode title="your leaf" key="random1" />
                    </TreeNode>
                    <TreeNode title="parent 1-1" key="random2">
                      <TreeNode title={<b style={{ color: '#08c' }}>sss</b>} key="random3" />
                    </TreeNode>
                  </TreeNode>
                </TreeSelect>
              </div>
            </Col>
          </Row>
          <br />
          <Row>
            <Col span={24}>
              <Divider orientation="left">Modal example</Divider>
              <div>
                <Button type="primary" onClick={showModal}>
                  Open Modal
                </Button>
                <Modal title="پنچره ساده" open={modalOpen} onOk={handleOk} onCancel={handleCancel}>
                  <p>نگاشته‌های خود را اینجا قراردهید</p>
                  <p>نگاشته‌های خود را اینجا قراردهید</p>
                  <p>نگاشته‌های خود را اینجا قراردهید</p>
                </Modal>
              </div>
            </Col>
          </Row>
          <br />
          <Row>
            <Col span={24}>
              <Divider orientation="left">Steps example</Divider>
              <div>
                <Steps
                  progressDot
                  current={currentStep}
                  items={[
                    {
                      title: 'Finished',
                      description: 'This is a description.',
                    },
                    {
                      title: 'In Progress',
                      description: 'This is a description.',
                    },
                    {
                      title: 'Waiting',
                      description: 'This is a description.',
                    },
                  ]}
                />
                <br />
                <Steps
                  current={currentStep}
                  onChange={onStepsChange}
                  items={[
                    {
                      title: 'Step 1',
                      description: 'This is a description.',
                    },
                    {
                      title: 'Step 2',
                      description: 'This is a description.',
                    },
                    {
                      title: 'Step 3',
                      description: 'This is a description.',
                    },
                  ]}
                />
              </div>
            </Col>
          </Row>
          <br />
          <Row>
            <Col span={12}>
              <Divider orientation="left">Rate example</Divider>
              <div>
                <Rate defaultValue={2.5} />
                <br />
                <strong>* Note:</strong> Half star not implemented in RTL direction, it will be
                supported after <a href="https://github.com/react-component/rate">rc-rate</a>{' '}
                implement rtl support.
              </div>
            </Col>
            <Col span={12}>
              <Divider orientation="left">Badge example</Divider>
              <div>
                <div>
                  <Badge count={badgeCount}>
                    <a href="#" className="head-example" />
                  </Badge>
                  <ButtonGroup>
                    <Button onClick={declineBadge}>
                      <MinusOutlined />
                    </Button>
                    <Button onClick={increaseBadge}>
                      <PlusOutlined />
                    </Button>
                  </ButtonGroup>
                </div>
                <div style={{ marginTop: 10 }}>
                  <Badge dot={showBadge}>
                    <a href="#" className="head-example" />
                  </Badge>
                  <Switch onChange={onChangeBadge} checked={showBadge} />
                </div>
              </div>
            </Col>
          </Row>
        </Col>
      </Row>

      <br />
      <br />
      <Row>
        <Col span={24}>
          <Divider orientation="left">Pagination example</Divider>
          <Pagination showSizeChanger defaultCurrent={3} total={500} />
        </Col>
      </Row>
      <br />
      <Row>
        <Col span={24}>
          <Divider orientation="left">Grid System example</Divider>
          <div className="grid-demo">
            <div className="code-box-demo">
              <p>
                <strong>* Note:</strong> Every calculation in RTL grid system is from right side
                (offset, push, etc.)
              </p>
              <Row>
                <Col span={8}>col-8</Col>
                <Col span={8} offset={8}>
                  col-8
                </Col>
              </Row>
              <Row>
                <Col span={6} offset={6}>
                  col-6 col-offset-6
                </Col>
                <Col span={6} offset={6}>
                  col-6 col-offset-6
                </Col>
              </Row>
              <Row>
                <Col span={12} offset={6}>
                  col-12 col-offset-6
                </Col>
              </Row>
              <Row>
                <Col span={18} push={6}>
                  col-18 col-push-6
                </Col>
                <Col span={6} pull={18}>
                  col-6 col-pull-18
                </Col>
              </Row>
            </div>
          </div>
        </Col>
      </Row>
    </div>
  );
};

const App: React.FC = () => {
  const [direction, setDirection] = useState<DirectionType>('ltr');
  const [popupPlacement, setPopupPlacement] = useState<Placement>('bottomLeft');

  const changeDirection = (e: RadioChangeEvent) => {
    const directionValue = e.target.value;
    setDirection(directionValue);
    if (directionValue === 'rtl') {
      setPopupPlacement('bottomRight');
    } else {
      setPopupPlacement('bottomLeft');
    }
  };

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <span style={{ marginRight: 16 }}>Change direction of components: </span>
        <Radio.Group defaultValue="ltr" onChange={changeDirection}>
          <Radio.Button key="ltr" value="ltr">
            LTR
          </Radio.Button>
          <Radio.Button key="rtl" value="rtl">
            RTL
          </Radio.Button>
        </Radio.Group>
      </div>
      <ConfigProvider direction={direction}>
        <Page popupPlacement={popupPlacement} />
      </ConfigProvider>
    </>
  );
};

export default App;
```

```css
.button-demo .ant-btn,
.button-demo .ant-btn-group {
  margin-right: 8px;
  margin-bottom: 12px;
}
.button-demo .ant-btn-group > .ant-btn,
.button-demo .ant-btn-group > span > .ant-btn {
  margin-right: 0;
  margin-left: 0;
}

.head-example {
  display: inline-block;
  width: 42px;
  height: 42px;
  vertical-align: middle;
  background: #eee;
  border-radius: 4px;
}

.ant-badge:not(.ant-badge-not-a-wrapper) {
  margin-right: 20px;
}
.ant-badge-rtl:not(.ant-badge-not-a-wrapper) {
  margin-right: 0;
  margin-left: 20px;
}
```

### 组件尺寸

- demo: `size`

修改默认组件尺寸。

```tsx
import {
  Button,
  Card,
  ConfigProvider,
  DatePicker,
  Divider,
  Input,
  Radio,
  Select,
  Table,
  Tabs,
} from 'antd';
import type { SizeType } from 'antd/es/config-provider/SizeContext';
import React, { useState } from 'react';

const { TabPane } = Tabs;

const App: React.FC = () => {
  const [componentSize, setComponentSize] = useState<SizeType>('small');

  return (
    <div>
      <Radio.Group
        value={componentSize}
        onChange={e => {
          setComponentSize(e.target.value);
        }}
      >
        <Radio.Button value="small">Small</Radio.Button>
        <Radio.Button value="middle">Middle</Radio.Button>
        <Radio.Button value="large">Large</Radio.Button>
      </Radio.Group>
      <Divider />
      <ConfigProvider componentSize={componentSize}>
        <div className="example">
          <Input />
        </div>
        <div className="example">
          <Tabs defaultActiveKey="1">
            <TabPane tab="Tab 1" key="1">
              Content of Tab Pane 1
            </TabPane>
            <TabPane tab="Tab 2" key="2">
              Content of Tab Pane 2
            </TabPane>
            <TabPane tab="Tab 3" key="3">
              Content of Tab Pane 3
            </TabPane>
          </Tabs>
        </div>
        <div className="example">
          <Input.Search allowClear />
        </div>
        <div className="example">
          <Input.TextArea allowClear />
        </div>
        <div className="example">
          <Select defaultValue="demo" options={[{ value: 'demo' }]} />
        </div>
        <div className="example">
          <DatePicker />
        </div>
        <div className="example">
          <DatePicker.RangePicker />
        </div>
        <div className="example">
          <Button>Button</Button>
        </div>
        <div className="example">
          <Card title="Card">
            <Table
              columns={[
                { title: 'Name', dataIndex: 'name' },
                { title: 'Age', dataIndex: 'age' },
              ]}
              dataSource={[
                {
                  key: '1',
                  name: 'John Brown',
                  age: 32,
                },
                {
                  key: '2',
                  name: 'Jim Green',
                  age: 42,
                },
                {
                  key: '3',
                  name: 'Joe Black',
                  age: 32,
                },
              ]}
            />
          </Card>
        </div>
      </ConfigProvider>
    </div>
  );
};

export default App;
```

### 全局样式

- demo: `theme`

通过 css variable 修改全局主题色（你可以切换到组件页面查看更详细的样式展示），不支持 IE。自动生成的变量可能会根据设计调整，请勿直接依赖。详细配置请点击查看。

```tsx
import {
  ClockCircleOutlined,
  DownOutlined,
  MailOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import type { SpaceProps, TreeSelectProps } from 'antd';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  ConfigProvider,
  DatePicker,
  Divider,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Mentions,
  Menu,
  Pagination,
  Progress,
  Radio,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Steps,
  Switch,
  Table,
  Tabs,
  Tag,
  Timeline,
  TimePicker,
  Transfer,
  Tree,
  TreeSelect,
  Typography,
} from 'antd';
import React, { useState } from 'react';
import { SketchPicker } from 'react-color';

const SplitSpace: React.FC<SpaceProps> = props => (
  <Space split={<Divider type="vertical" />} size={4} {...props} />
);

const menuItems = [
  {
    key: 'mail',
    icon: <MailOutlined />,
    label: 'Mail',
  },
  {
    key: 'SubMenu',
    icon: <SettingOutlined />,
    label: 'Submenu',
    children: [
      {
        type: 'group',
        label: 'Item 1',
        children: [
          {
            key: 'setting:1',
            label: 'Option 1',
          },
          {
            key: 'setting:2',
            label: 'Option 2',
          },
        ],
      },
    ],
  },
];

const inputProps = {
  style: { width: 128 },
};

const selectProps = {
  ...inputProps,
  options: [
    { value: 'light', label: 'Light' },
    { value: 'bamboo', label: 'Bamboo' },
    { value: 'little', label: 'Little' },
  ],
};

const treeData = [
  {
    value: 'little',
    key: 'little',
    label: 'Little',
    title: 'Little',
    children: [
      { value: 'light', key: 'light', label: 'Light', title: 'Light' },
      { value: 'bamboo', key: 'bamboo', label: 'Bamboo', title: 'Bamboo' },
    ],
  },
];

const treeSelectProps: TreeSelectProps = {
  ...inputProps,
  treeCheckable: true,
  maxTagCount: 'responsive',
  treeData,
};

const carTabListNoTitle = [
  {
    key: 'article',
    tab: 'article',
  },
  {
    key: 'app',
    tab: 'app',
  },
  {
    key: 'project',
    tab: 'project',
  },
];

const MyTransfer = () => {
  const mockData = [];
  for (let i = 0; i < 20; i++) {
    mockData.push({
      key: i.toString(),
      title: `content${i + 1}`,
      description: `description of content${i + 1}`,
    });
  }

  return (
    <Transfer
      dataSource={mockData}
      targetKeys={['18']}
      selectedKeys={['3']}
      render={item => item.title}
    />
  );
};

const App: React.FC = () => {
  const [color, setColor] = useState({
    primaryColor: '#1890ff',
    errorColor: '#ff4d4f',
    warningColor: '#faad14',
    successColor: '#52c41a',
    infoColor: '#1890ff',
  });

  const onColorChange = (nextColor: Partial<typeof color>) => {
    const mergedNextColor = {
      ...color,
      ...nextColor,
    };
    setColor(mergedNextColor);
    ConfigProvider.config({
      theme: mergedNextColor,
    });
  };

  return (
    <Row gutter={16} wrap={false}>
      <Col flex="none">
        <Space direction="vertical" align="center">
          {/* Primary Color */}
          <SketchPicker
            presetColors={['#1890ff', '#25b864', '#ff6f00']}
            color={color.primaryColor}
            onChange={({ hex }) => {
              onColorChange({
                primaryColor: hex,
              });
            }}
          />

          <span style={{ color: 'var(--ant-primary-color)' }}>var(`--ant-primary-color`)</span>

          {/* Error Color */}
          <SketchPicker
            presetColors={['#ff4d4f']}
            color={color.errorColor}
            onChange={({ hex }) => {
              onColorChange({
                errorColor: hex,
              });
            }}
          />

          <span style={{ color: 'var(--ant-error-color)' }}>var(`--ant-error-color`)</span>

          {/* Warning Color */}
          <SketchPicker
            presetColors={['#faad14']}
            color={color.warningColor}
            onChange={({ hex }) => {
              onColorChange({
                warningColor: hex,
              });
            }}
          />

          <span style={{ color: 'var(--ant-warning-color)' }}>var(`--ant-warning-color`)</span>

          {/* Success Color */}
          <SketchPicker
            presetColors={['#52c41a']}
            color={color.successColor}
            onChange={({ hex }) => {
              onColorChange({
                successColor: hex,
              });
            }}
          />

          <span style={{ color: 'var(--ant-success-color)' }}>var(`--ant-success-color`)</span>

          {/* Info Color */}
          <SketchPicker
            presetColors={['#1890ff']}
            color={color.infoColor}
            onChange={({ hex }) => {
              onColorChange({
                infoColor: hex,
              });
            }}
          />

          <span style={{ color: 'var(--ant-info-color)' }}>var(`--ant-info-color`)</span>
        </Space>
      </Col>

      <Col flex="auto">
        <Space direction="vertical" split={<Divider />} style={{ width: '100%' }} size={0}>
          {/* Primary Button */}
          <SplitSpace>
            <Button type="primary">Primary</Button>
            <Button>Default</Button>
            <Button type="dashed">Dashed</Button>
            <Button type="text">Text</Button>
            <Button type="link">Link</Button>
          </SplitSpace>

          {/* Danger Button */}
          <SplitSpace>
            <Button danger type="primary">
              Primary
            </Button>
            <Button danger>Default</Button>
            <Button danger type="dashed">
              Dashed
            </Button>
            <Button danger type="text">
              Text
            </Button>
            <Button danger type="link">
              Link
            </Button>
          </SplitSpace>

          {/* Ghost Button */}
          <SplitSpace style={{ background: 'rgb(190, 200, 200)' }}>
            <Button type="primary" ghost>
              Primary
            </Button>
            <Button ghost>Default</Button>
            <Button type="dashed" ghost>
              Dashed
            </Button>
            <Button type="primary" ghost danger>
              Primary
            </Button>
            <Button ghost danger>
              Default
            </Button>
            <Button type="dashed" ghost danger>
              Dashed
            </Button>
          </SplitSpace>

          {/* Typography */}
          <SplitSpace>
            <Typography.Text type="success">Text (success)</Typography.Text>
            <Typography.Text type="warning">Text(warning)</Typography.Text>
            <Typography.Text type="danger">Text(danger)</Typography.Text>
            <Typography.Link href="https://ant.design" target="_blank">
              Link
            </Typography.Link>
            <Typography.Text copyable>Text</Typography.Text>

            {/* Dropdown */}
            <Dropdown
              menu={{
                items: [
                  {
                    key: '1',
                    label: '1st menu item',
                  },
                  {
                    key: '2',
                    label: 'a danger item',
                    danger: true,
                  },
                ],
              }}
            >
              <a onClick={e => e.preventDefault()}>
                <Space>
                  Hover me
                  <DownOutlined />
                </Space>
              </a>
            </Dropdown>

            {/* Spin */}
            <Spin />
          </SplitSpace>

          {/* Menu - horizontal */}
          <Row gutter={16}>
            <Col span={12}>
              <Menu mode="horizontal" defaultSelectedKeys={['mail']} items={menuItems} />
            </Col>
            <Col span={12}>
              <Menu
                mode="horizontal"
                theme="dark"
                defaultSelectedKeys={['mail']}
                items={menuItems}
              />
            </Col>
          </Row>

          {/* Menu - vertical */}
          <Row gutter={16}>
            <Col span={12}>
              <Menu mode="inline" defaultSelectedKeys={['mail']} items={menuItems} />
            </Col>
            <Col span={12}>
              <Menu mode="vertical" theme="dark" defaultSelectedKeys={['mail']} items={menuItems} />
            </Col>
          </Row>

          {/* Pagination */}
          <Pagination showQuickJumper defaultCurrent={2} total={500} />

          {/* Steps */}
          <Steps
            current={1}
            percent={60}
            items={[
              {
                title: 'Finished',
                description: 'This is a description.',
              },
              {
                title: 'In Progress',
                subTitle: 'Left 00:00:08',
                description: 'This is a description.',
              },
              {
                title: 'Waiting',
                description: 'This is a description.',
              },
            ]}
          />

          {/* Steps - dot */}
          <Steps
            current={2}
            status="error"
            progressDot
            items={[
              {
                title: 'Finished',
                description: 'You can hover on the dot.',
              },
              {
                title: 'In Progress',
                description: 'You can hover on the dot.',
              },
              {
                title: 'Error',
                description: 'You can hover on the dot.',
              },
              {
                title: 'Waiting',
                description: 'You can hover on the dot.',
              },
            ]}
          />

          {/* Form - Input */}
          <Form>
            <SplitSpace>
              <Form.Item>
                <Input {...inputProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="success">
                <Input {...inputProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="warning">
                <Input {...inputProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="error">
                <Input {...inputProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="validating">
                <Input {...inputProps} />
              </Form.Item>
            </SplitSpace>
          </Form>

          {/* Form - Select */}
          <Form>
            <SplitSpace>
              <Form.Item>
                <Select {...selectProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="success">
                <Select {...selectProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="warning">
                <Select {...selectProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="error">
                <Select {...selectProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="validating">
                <Select {...selectProps} />
              </Form.Item>
            </SplitSpace>
          </Form>

          {/* Form - TreeSelect */}
          <Form>
            <SplitSpace>
              <Form.Item>
                <TreeSelect {...treeSelectProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="success">
                <TreeSelect {...treeSelectProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="warning">
                <TreeSelect {...treeSelectProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="error">
                <TreeSelect {...treeSelectProps} />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="validating">
                <TreeSelect {...treeSelectProps} />
              </Form.Item>
            </SplitSpace>
          </Form>

          {/* Form - InputNumber */}
          <Form>
            <SplitSpace>
              <Form.Item>
                <InputNumber />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="success">
                <InputNumber />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="warning">
                <InputNumber />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="error">
                <InputNumber />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="validating">
                <InputNumber />
              </Form.Item>
            </SplitSpace>
          </Form>

          {/* Form - DatePicker */}
          <Form>
            <SplitSpace>
              <Form.Item>
                <DatePicker />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="success">
                <DatePicker />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="warning">
                <DatePicker />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="error">
                <DatePicker />
              </Form.Item>
              <Form.Item hasFeedback validateStatus="validating">
                <DatePicker />
              </Form.Item>
            </SplitSpace>
          </Form>

          <SplitSpace>
            <Checkbox>Checkbox</Checkbox>

            <Radio.Group defaultValue="bamboo">
              <Radio value="bamboo">Bamboo</Radio>
              <Radio value="light">Light</Radio>
              <Radio value="little">Little</Radio>
            </Radio.Group>

            <Mentions placeholder="Mention by @">
              <Mentions.Option value="afc163">afc163</Mentions.Option>
              <Mentions.Option value="zombieJ">zombieJ</Mentions.Option>
              <Mentions.Option value="yesmeck">yesmeck</Mentions.Option>
            </Mentions>

            <Slider defaultValue={30} style={{ width: 100 }} />

            <Switch defaultChecked />
          </SplitSpace>

          <SplitSpace>
            <DatePicker.RangePicker />
            <TimePicker.RangePicker />
          </SplitSpace>

          <Row gutter={16}>
            <Col span={8}>
              {/* Card */}
              <Card
                style={{ width: '100%' }}
                tabList={carTabListNoTitle}
                tabBarExtraContent={<a href="#">More</a>}
              />
            </Col>
            <Col span={8}>
              {/* Table */}
              <Table
                size="small"
                bordered
                rowSelection={{}}
                columns={[
                  {
                    title: 'Key',
                    dataIndex: 'key',
                    filters: [
                      {
                        text: 'Little',
                        value: 'little',
                      },
                    ],
                    sorter: (a, b) => a.key.length - b.key.length,
                  },
                ]}
                dataSource={[
                  {
                    key: 'Bamboo',
                  },
                  {
                    key: 'Light',
                  },
                  {
                    key: 'Little',
                  },
                ]}
              />
            </Col>
            <Col span={8}>
              {/* Table */}
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
            </Col>
          </Row>

          <SplitSpace>
            <Tag color="success">success</Tag>
            <Tag color="processing">processing</Tag>
            <Tag color="error">error</Tag>
            <Tag color="warning">warning</Tag>
            <Tag color="default">default</Tag>
            <Tag.CheckableTag checked>CheckableTag</Tag.CheckableTag>
          </SplitSpace>

          <Row gutter={16}>
            <Col span={16}>
              <Timeline mode="alternate">
                <Timeline.Item>Create a services site 2015-09-01</Timeline.Item>
                <Timeline.Item color="gray">
                  Solve initial network problems 2015-09-01
                </Timeline.Item>
                <Timeline.Item dot={<ClockCircleOutlined style={{ fontSize: '16px' }} />}>
                  Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium
                  doloremque laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore
                  veritatis et quasi architecto beatae vitae dicta sunt explicabo.
                </Timeline.Item>
              </Timeline>
            </Col>

            <Col span={8}>
              <Tree treeData={treeData} height={200} defaultExpandAll checkable />
            </Col>
          </Row>

          {/* Alert */}
          <Row gutter={16}>
            <Col span={6}>
              <Alert showIcon message="Success Text" type="success" />
            </Col>
            <Col span={6}>
              <Alert showIcon message="Info Text" type="info" />
            </Col>
            <Col span={6}>
              <Alert showIcon message="Warning Text" type="warning" />
            </Col>
            <Col span={6}>
              <Alert showIcon message="Error Text" type="error" />
            </Col>
          </Row>

          {/* Progress */}
          <Row gutter={16}>
            <Col flex="auto">
              <Progress percent={30} />
              <Progress percent={70} status="exception" />
              <Progress percent={100} />
            </Col>
            <Col flex="none">
              <Progress type="circle" percent={75} />
              <Progress type="circle" percent={70} status="exception" />
              <Progress type="circle" percent={100} />
            </Col>
          </Row>

          <MyTransfer />
        </Space>
      </Col>
    </Row>
  );
};

export default App;
```
