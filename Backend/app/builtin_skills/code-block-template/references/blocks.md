# 区块详解

## 2.1 ProTable 表格

### 核心能力

ProTable 在 antd `Table` 基础上封装了三个关键能力：

| 能力 | 说明 | 对应 Props |
|------|------|-----------|
| 查询表单 | 根据 columns 的 valueType/valueEnum 自动生成 | search 配置 |
| 服务端分页 | request 函数自动接管 loading / dataSource / pagination | request |
| 工具栏 | 右上角标准操作按钮区域 | toolBarRender |

### 决策树：何时用 ProTable 内置 search，何时自定义筛选表单？

```
需要表格上方有筛选表单？
├─ 布局要求不高 → 用 ProTable 内置 search
│   search={{ labelLayout: 'default', defaultCollapsed: false }}
│
└─ 需要自定义横排布局 → 自定义 ProForm + Row/Col
    search={false}
    + 外部 <ProForm submitter={false}> + Row/Col
```

**自定义筛选表单的标准写法**：

```tsx
// 外部 ProForm，submitter={false}，手动控制查询/重置
<ProForm formRef={formRef} layout="horizontal" submitter={false}>
  <Row gutter={[16, 16]}>
    <Col span={6}>
      <ProFormText name="field1" label="字段1" placeholder="请输入" />
    </Col>
    {/* …更多字段… */}
  </Row>
  <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
    <Button type="primary" onClick={async () => {
      await formRef.current?.validateFields();
      tableRef.current?.reload();
    }}>查询</Button>
    <Button onClick={() => {
      formRef.current?.resetFields();
      tableRef.current?.reload();
    }}>重置</Button>
  </div>
</ProForm>
```

关键点：
- 查询按钮用 `onClick` 手动调用 `reload()`，而非依赖 `htmlType="submit"`
- ProTable 的 `request` 中通过 `formRef.current?.getFieldsValue()` 获取筛选值
- `submitter={false}` 关闭 ProForm 内置按钮，避免与自定义按钮冲突

### 分页配置

```tsx
pagination={{
  defaultPageSize: 10,                           // 用 defaultPageSize 而非 pageSize
  pageSizeOptions: [10, 20, 50, 100],
  showSizeChanger: true,
  showQuickJumper: true,
}}
```

**注意**：必须使用 `defaultPageSize` 而非 `pageSize`。`pageSize` 是受控属性，会导致用户切换每页条数后又被重置。

### request 函数的正确写法

```tsx
request={async (params) => {
  const formValues = formRef.current?.getFieldsValue() || {};
  const { current, pageSize } = params;  // ProTable 自动注入的分页参数
  const res = await fetchList({
    page: current || 1,
    pageSize: pageSize || 10,
    ...formValues,                       // 合并筛选条件
  });
  return {
    data: res.data,     // 当前页数据
    success: true,      // 是否成功
    total: res.total,   // 总条数（用于分页计算）
  };
}}
```

### rowSelection 与批量操作

```tsx
const [mgmtSelectedRowKeys, setMgmtSelectedRowKeys] = useState<React.Key[]>([]);

<ProTable
  rowSelection={{
    selectedRowKeys: mgmtSelectedRowKeys,
    onChange: (keys) => setMgmtSelectedRowKeys(keys),
  }}
  tableAlertRender={({ selectedRowKeys: selKeys }) =>
    selKeys.length > 0 ? (
      <Space>
        <span>已选择 {selKeys.length} 项</span>
        <a onClick={() => setMgmtSelectedRowKeys([])}>取消选择</a>
      </Space>
    ) : false
  }
/>
```

### 示例代码

```tsx
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components';
import { Button, Popconfirm, message } from 'antd';
import { useRef } from 'react';
import { cx } from '../utils';
import { deleteUser, listUsers } from '../api/userApi';
import type { User, UserQuery } from '../typings';
import './UserTable.less';

type Props = {
  onEdit: (user: User) => void;
  onCreate: () => void;
};

export default function UserTable({ onEdit, onCreate }: Props) {
  const actionRef = useRef<ActionType>();

  const columns: ProColumns<User>[] = [
    { title: '用户名', dataIndex: 'username', valueType: 'text', formItemProps: { rules: [{ required: false }] } },
    { title: '姓名', dataIndex: 'name', valueType: 'text' },
    {
      title: '状态',
      dataIndex: 'status',
      valueType: 'select',
      valueEnum: {
        active: { text: '启用', status: 'Success' },
        disabled: { text: '禁用', status: 'Error' },
      },
    },
    { title: '创建时间', dataIndex: 'createdAt', valueType: 'dateTime', hideInForm: true },
    {
      title: '操作',
      valueType: 'option',
      width: 140,
      render: (_, record) => [
        <a key="edit" onClick={() => onEdit(record)}>
          <EditOutlined /> 编辑
        </a>,
        <Popconfirm
          key="delete"
          title="确认删除该用户?"
          onConfirm={async () => {
            await deleteUser(record.id);
            message.success('已删除');
            actionRef.current?.reload();
          }}
        >
          <a className={cx('danger-link')}>
            <DeleteOutlined /> 删除
          </a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <ProTable<User, UserQuery>
      headerTitle="用户列表"
      actionRef={actionRef}
      rowKey="id"
      columns={columns}
      search={{ labelLayout: 'default', defaultCollapsed: false }}
      request={async (params) => {
        const { current, pageSize, ...rest } = params;
        const res = await listUsers({ page: current ?? 1, pageSize: pageSize ?? 10, ...rest });
        return { data: res.list, success: true, total: res.total };
      }}
      pagination={{ defaultPageSize: 10, showSizeChanger: true }}
      toolBarRender={() => [
        <Button key="refresh" icon={<ReloadOutlined />} onClick={() => actionRef.current?.reload()}>刷新</Button>,
        <Button key="create" type="primary" icon={<PlusOutlined />} onClick={onCreate}>新增</Button>,
      ]}
    />
  );
}
```

---

## 2.2 ProForm 表单体系

### 四种表单形态对比

| 特性 | ProForm | ModalForm | DrawerForm | StepsForm |
|------|---------|-----------|------------|-----------|
| 承载容器 | 常驻页面 | 弹窗表单 | 抽屉表单 | 多步表单 |
| 打开方式 | 始终可见 | 按钮触发 | 按钮触发 | 按钮触发 |
| 数据回显 | initialValues | initialValues + destroyOnClose | initialValues + destroyOnClose | stepForm 各自的 initialValues |
| 提交后行为 | onFinish 回调 | 自动关闭 + onFinish | 自动关闭 + onFinish | 重置到第1步 + onFinish |

### 决策树

```
用户要填的字段有多少？
├─ 多个表单且有明确阶段 → StepsForm（分步引导）
├─ 字段非常多（15+）→ DrawerForm（纵向空间大）
├─ 表格的行操作 → ModalForm（首选）
└─ 始终可见、作为页面主体的配置表单 → ProForm（常驻页面）
```

### 常用字段组件速查

| 组件 | 用途 | value 类型 | 特殊属性 |
|------|------|-----------|---------|
| ProFormText | 文本输入 | string | pattern 正则校验 |
| ProFormText.Password | 密码输入 | string | 自动遮盖 |
| ProFormTextArea | 多行文本 | string | autoSize, maxLength, showCount |
| ProFormDigit | 数字输入 | number | min/max/precision |
| ProFormSelect | 下拉选择 | string/string[] | request 异步加载，mode="multiple" 多选 |
| ProFormRadio.Group | 单选组 | string | radioType="button" 按钮样式 |
| ProFormCheckbox.Group | 复选组 | string[] | - |
| ProFormSwitch | 开关 | boolean | checkedChildren/unCheckedChildren |
| ProFormDatePicker | 日期 | string(Moment) | fieldProps.format |
| ProFormDateRangePicker | 日期范围 | [string, string] | - |
| ProFormDateTimePicker | 日期时间 | string(Moment) | fieldProps.format |
| ProFormUploadDragger | 拖拽上传 | UploadFile[] | maxCount, beforeUpload |
| ProFormDependency | 字段联动 | - | name 依赖字段，render 返回条件组件 |
| ProFormItem | 自定义容器 | any | 包裹非 ProForm 控件 |

### 上传字段 label 布局注意

当上传控件用 `listType="picture-card"` 时，label 往往较长，若沿用普通字段的 `labelCol` 比例会被方块遮挡。处理方式：

1. **给上传字段单独设更宽的 `labelCol`**：
   ```tsx
   <ProFormItem name="idCardFront" label="身份证-人像面" labelCol={{ span: 10 }} wrapperCol={{ span: 14 }}>
     <Upload listType="picture-card" maxCount={1} beforeUpload={() => false}>
       <UploadButton />
     </Upload>
   </ProFormItem>
   ```
2. **label 置顶**：用 `layout="vertical"`，**必须给所在 Col 设足够 span**，否则 label 被压成竖排：
   ```tsx
   <Col span={8}>
     <ProFormItem name="idCardFront" label="身份证-人像面" layout="vertical" valuePropName="fileList">
       <Upload listType="picture-card" maxCount={1} beforeUpload={() => false}>
         <UploadButton />
       </Upload>
     </ProFormItem>
   </Col>
   ```

### ModalForm 关键注意事项

1. **destroyOnClose**：必须设为 `true`
2. **key 强制重建**：编辑回显不同行数据时，用 `key={record?.id}` 确保 `initialValues` 生效
   ```tsx
   <ModalForm
     key={modal.record?.id || 'empty'}
     initialValues={modal.type === 'edit' ? modal.record || {} : {}}
     modalProps={{ destroyOnClose: true, width: 720 }}
   >
   ```
3. **submitter 自定义**：
   ```tsx
   submitter={{
     searchConfig: { submitText: '确定', resetText: '取消' },
     submitButtonProps: { danger: true },  // 危险操作设红色
   }}
   ```
4. **visible 受控**：统一管理多个弹窗时，使用 `visible` + `onVisibleChange` 受控模式

### ProFormDependency 联动

```tsx
<ProFormDependency name={['enable']}>
  {({ enable }) =>
    enable ? (
      <ProFormDateTimePicker name="effectTime" label="生效时间" rules={[{ required: true }]} />
    ) : null
  }
</ProFormDependency>
```

### 示例代码一：基本字段

演示 ProFormText / Password / Digit / Radio / Switch 五种组件的用法。

```tsx
import {
  ProForm, ProFormDigit, ProFormRadio, ProFormSwitch, ProFormText,
} from '@ant-design/pro-components';
import { message } from 'antd';
import { createUser, updateUser } from '../api/userApi';
import type { User } from '../typings';

type Props = { editing?: User; onSuccess: () => void };

export default function UserForm({ editing, onSuccess }: Props) {
  return (
    <ProForm
      layout="horizontal"
      labelCol={{ span: 6 }}
      wrapperCol={{ span: 16 }}
      initialValues={editing ?? { gender: 'male', age: 18, notify: true }}
      onFinish={async (values) => {
        if (editing) { await updateUser(editing.id, values); message.success('已更新'); }
        else { await createUser(values); message.success('已创建'); }
        onSuccess(); return true;
      }}
      submitter={{ searchConfig: { submitText: editing ? '保存' : '创建', resetText: '重置' } }}
    >
      <ProFormText name="username" label="用户名" placeholder="请输入登录用户名"
        rules={[{ required: true }, { pattern: /^[a-zA-Z0-9_]{3,20}$/, message: '仅支持字母数字下划线,3-20 位' }]} />
      <ProFormText.Password name="password" label="密码"
        rules={[{ required: true }, { min: 8, message: '至少 8 位' }]} />
      <ProFormDigit name="age" label="年龄" min={0} max={150} fieldProps={{ precision: 0 }} />
      <ProFormRadio.Group name="gender" label="性别"
        options={[{ label: '男', value: 'male' }, { label: '女', value: 'female' }]} />
      <ProFormSwitch name="notify" label="接收通知"
        fieldProps={{ checkedChildren: '开', unCheckedChildren: '关' }} />
    </ProForm>
  );
}
```

### 示例代码二：日期/上传/自定义容器

演示 ProFormDateTimePicker / DateTimeRangePicker / TimePicker / UploadDragger / ProFormItem。

```tsx
import {
  DrawerForm, ProFormDateTimePicker, ProFormDateTimeRangePicker,
  ProFormItem, ProFormText, ProFormTimePicker, ProFormUploadDragger,
} from '@ant-design/pro-components';
import { InboxOutlined } from '@ant-design/icons';
import { Slider, message, Upload } from 'antd';
import { updateUser } from '../api/userApi';
import type { User } from '../typings';

type Props = { editing?: User; visible: boolean; onVisibleChange: (v: boolean) => void; onSuccess: () => void };

export default function EditUserDrawer({ editing, visible, onVisibleChange, onSuccess }: Props) {
  return (
    <DrawerForm<User>
      title="编辑用户" width={520} open={visible} onOpenChange={onVisibleChange}
      initialValues={editing} drawerProps={{ destroyOnClose: true }}
      onFinish={async (values) => { if (editing) await updateUser(editing.id, values); return true; }}
    >
      <ProFormText name="username" label="用户名" rules={[{ required: true }]} disabled={!!editing} />
      <ProFormDateTimePicker name="startTime" label="开始时间"
        fieldProps={{ style: { width: '100%' }, format: 'YYYY-MM-DD HH:mm' }} />
      <ProFormDateTimeRangePicker name="activeRange" label="生效区间" fieldProps={{ style: { width: '100%' } }} />
      <ProFormTimePicker name="remindTime" label="每日提醒" fieldProps={{ format: 'HH:mm', style: { width: '100%' } }} />
      <ProFormUploadDragger name="avatar" label="头像"
        title="点击或拖拽文件到此区域上传" description="单个文件不超过 5MB"
        fieldProps={{ name: 'file', listType: 'picture-card', maxCount: 1,
          beforeUpload: (file) => {
            if (file.size > 5 * 1024 * 1024) { message.error('文件不能超过 5MB'); return Upload.LIST_IGNORE; }
            return true;
          },
        }} action="/api/upload" />
      <ProFormItem name="score" label="评分" rules={[{ required: true, message: '请设置评分' }]}>
        <Slider min={0} max={100} marks={{ 0: '0', 60: '及格', 100: '100' }} />
      </ProFormItem>
    </DrawerForm>
  );
}
```

### 示例代码三：分步表单

```tsx
import {
  StepsForm, ProFormText, ProFormSelect, ProFormSwitch,
  ProFormDateTimePicker, ProFormDependency,
} from '@ant-design/pro-components';
import { message } from 'antd';
import { createUser } from '../api/userApi';

type Props = { onSuccess: () => void };

export default function CreateUserSteps({ onSuccess }: Props) {
  return (
    <StepsForm stepsProps={{ size: 'default' }}
      onFinish={async (values) => { await createUser(values); message.success('已创建'); onSuccess(); return true; }}
    >
      <StepsForm.StepForm name="base" title="基本信息"
        onFinish={async (values) => {
          if (values.username === 'admin') { message.error('用户名已存在'); return false; }
          return true;
        }}
      >
        <ProFormText name="username" label="用户名" rules={[{ required: true }]} />
        <ProFormText name="name" label="姓名" rules={[{ required: true }]} />
        <ProFormText name="phone" label="手机号"
          rules={[{ required: true }, { pattern: /^1\d{10}$/, message: '请输入正确的手机号' }]} />
      </StepsForm.StepForm>

      <StepsForm.StepForm name="account" title="账户配置">
        <ProFormSelect name="deptId" label="部门"
          request={async () => [{ label: '研发部', value: 'rd' }, { label: '运营部', value: 'ops' }]}
          rules={[{ required: true }]} />
        <ProFormSelect name="role" label="角色"
          options={[{ label: '管理员', value: 'admin' }, { label: '普通用户', value: 'user' }]}
          rules={[{ required: true }]} />
        <ProFormSwitch name="enabled" label="是否启用"
          fieldProps={{ checkedChildren: '启用', unCheckedChildren: '禁用' }} />
        <ProFormDependency name={['enabled']}>
          {({ enabled }) => enabled ? <ProFormDateTimePicker name="effectTime" label="生效时间"
            fieldProps={{ style: { width: '100%' } }} rules={[{ required: true }]} /> : null}
        </ProFormDependency>
      </StepsForm.StepForm>

      <StepsForm.StepForm name="confirm" title="确认信息">
        <div style={{ padding: '8px 0', color: '#666' }}>请确认以上信息无误，点击"提交"完成创建。</div>
      </StepsForm.StepForm>
    </StepsForm>
  );
}
```

### 提交按钮位置与对齐

ProForm 的提交/重置按钮默认渲染在 `wrapperCol` 区域内且**默认左对齐**。弹窗表单按钮默认右对齐。独立表单页若需按钮靠右，用 `submitter.render`：

```tsx
<ProForm
  onFinish={async (values) => { /* ... */ return true; }}
  submitter={{
    searchConfig: { submitText: '提交', resetText: '重置' },
    render: (_, dom) => (
      <div style={{ display: 'flex', justifyContent: 'flex-end', width: '100%' }}>
        {dom}
      </div>
    ),
  }}
>
```

---

## 2.3 ProList 列表

### 与 ProTable 的选择

```
数据展示用什么？
├─ 数据是结构化表格、列多、需要排序/筛选 → ProTable
└─ 每条记录信息量大、需要图文混排、卡片式展示 → ProList
```

### metas 配置

```tsx
<ProList
  metas={{
    title: { dataIndex: 'title' },
    description: { dataIndex: 'desc' },
    subTitle: { dataIndex: 'dept' },
    avatar: { dataIndex: 'avatar' },
    actions: { render: () => [...] },
  }}
/>
```

### 示例代码

```tsx
import React, { useState } from 'react';
import { PageContainer, ProList } from '@ant-design/pro-components';
import { Button, Tag } from 'antd';
import { ReloadOutlined, SwapOutlined } from '@ant-design/icons';

type NoticeItem = {
    id: number; title: string; department: string; date: string; tag?: 'recommend' | 'new';
};

const initialData: NoticeItem[] = [
    { id: 1, title: '社区举办健身挑战赛，鼓励居民运动', department: '新闻部', date: '2024-11-19', tag: 'recommend' },
    { id: 2, title: '新咖啡馆开业，提供独特饮品和休闲空间', department: '新闻部', date: '2024-11-19', tag: 'new' },
    { id: 3, title: '社区图书馆开放，丰富居民文化生活', department: '新闻部', date: '2024-11-19', tag: 'new' },
    { id: 4, title: '公园举办春季花卉展，吸引市民赏花', department: '新闻部', date: '2024-11-19' },
    { id: 5, title: '环保组织发起旧物回收活动，倡导绿色生活', department: '新闻部', date: '2024-11-19' },
    { id: 6, title: '城市举办美食节，展示各地美食文化', department: '新闻部', date: '2024-11-19' },
];

const NoticeList: React.FC = () => {
    const [data, setData] = useState(initialData);

    return (
        <PageContainer header={{
            title: '通知公告',
            extra: [
                <Button key="refresh" type="text" icon={<ReloadOutlined />} onClick={() => setData([...initialData])}>刷新</Button>,
                <Button key="change" type="text" icon={<SwapOutlined />} onClick={() => setData([...data].sort(() => Math.random() - 0.5))}>换一换</Button>,
            ],
        }}>
            <ProList<NoticeItem>
                rowKey="id" dataSource={data}
                pagination={{ pageSize: 5, showSizeChanger: false }}
                metas={{
                    title: {
                        dataIndex: 'title',
                        render: (_, item) => (
                            <span style={{ display: 'flex', alignItems: 'center' }}>
                                {item.tag === 'recommend' && <Tag color="red" style={{ marginRight: 8 }}>荐</Tag>}
                                {item.tag === 'new' && <Tag color="gold" style={{ marginRight: 8 }}>新</Tag>}
                                <span style={{ fontWeight: 500, fontSize: 16 }}>{item.title}</span>
                            </span>
                        ),
                    },
                    description: {
                        render: (_, item) => (
                            <span style={{ color: '#999', fontSize: 14, marginLeft: 40 }}>
                                {item.department} {item.date}
                            </span>
                        ),
                    },
                }}
                split={false}
            />
        </PageContainer>
    );
};

export default NoticeList;
```

---

## 2.4 Row/Col 栅格布局

### 核心参数

- `Row gutter={[水平间距, 垂直间距]}` - 控制列间距
- `Col span={占24等分的份数}` - 宽度 = span / 24 × 100%
- `Col offset={左侧偏移份数}` - 居中对齐

### 常见布局模式

```
span=24 × 1 → 占满整行（标题/长文本/顶部 banner）
span=12 × 2 → 两列均分（弹窗表单）
span=8  × 3 → 三列均分（筛选条件）
span=6  × 4 → 四列均分（筛选条件、卡片）
span=12 + 12 → 左右两栏
span=16 + 8  → 左宽右窄（主内容 + 侧边栏）
```

### 示例：三行三列

```tsx
import React from 'react';
import { Row, Col } from 'antd';

const ColorGrid: React.FC = () => {
    const colors = ['#FF6B6B','#4ECDC4','#45B7D1','#FFA07A','#98D8C8','#F7DC6F','#BB8FCE','#F8A5C2','#74B9FF'];
    return (
        <Row gutter={[16, 16]} style={{ padding: 24 }}>
            {colors.map((color, index) => (
                <Col key={index} span={8}>
                    <div style={{ height: 120, backgroundColor: color, borderRadius: 8 }} />
                </Col>
            ))}
        </Row>
    );
};

export default ColorGrid;
```

### 示例：一拖二布局

```tsx
import React from 'react';
import { Row, Col } from 'antd';

const OneTwoLayout: React.FC = () => (
  <Row gutter={[16, 16]} style={{ padding: 24 }}>
    <Col span={24}>
      <div style={{ height: 150, backgroundColor: '#4A90D9', borderRadius: 8 }} />
    </Col>
    <Col span={12}>
      <div style={{ height: 120, backgroundColor: '#50C878', borderRadius: 8 }} />
    </Col>
    <Col span={12}>
      <div style={{ height: 120, backgroundColor: '#FF6B6B', borderRadius: 8 }} />
    </Col>
  </Row>
);

export default OneTwoLayout;
```

---

## 2.5 ProCard 卡片

### 三种主要用法

1. **分组容器/内容分块**（最常用）：表单、信息按模块分组，每块一个 ProCard
2. **标签页容器**（`ProCard tabs`）：用 `tabs={{ list: [{ key, label, children }] }}` 实现
3. **统计/概览卡片**：Dashboard KPI 指标展示

### 何时用 ProCard

凡要做"卡片式"展示或分组，**必须用 ProCard**。**禁止**用：
- ❌ `<div className="xxx-card">` 手写卡片
- ❌ antd 的 `<Card>` 组件

判断依据（命中任一即用 ProCard）：
- 指标块、信息分块（统计/概览场景）
- 表单按模块分组（最常见）
- 多标签页切换容器

### 示例：表单分组（最常用）

```tsx
import { ProCard, ProForm, ProFormText, ProFormSelect } from '@ant-design/pro-components';

const EntrustForm = () => (
  <ProForm onFinish={async (v) => { /* 提交 */ return true; }}>
    <ProCard title="信托投资基本信息" headerBordered style={{ marginBottom: 16 }}>
      <ProFormSelect name="contractMethod" label="签约方式" options={[...]} />
      <ProFormSelect name="investmentType" label="信托投资类型" options={[...]} />
    </ProCard>

    <ProCard title="委托人基本信息" headerBordered style={{ marginBottom: 16 }}>
      <ProFormText name="clientName" label="委托人姓名" />
      <ProFormSelect name="idType" label="证件类型" options={[...]} />
    </ProCard>

    <ProCard title="委托人尽调材料" headerBordered>
      <ProFormSelect name="riskLevel" label="风险承受能力" options={[...]} />
    </ProCard>
  </ProForm>
);
```

要点：
- 每个模块一个 ProCard，`title` 模块名，`headerBordered` 下边框
- ProCard 直接放 ProForm 内部，表单项放 ProCard 里
- 弹窗表单（ModalForm）里同样用 ProCard 分组

### 示例：统计卡片

```tsx
import { Row, Col, Statistic } from 'antd';
import { ProCard } from '@ant-design/pro-components';
import { ShoppingCartOutlined, ClockCircleOutlined, CheckCircleOutlined } from '@ant-design/icons';

const OrderOverview = () => (
  <Row gutter={[16, 16]}>
    <Col span={8}>
      <ProCard title="总订单量" headerBordered>
        <Statistic prefix={<ShoppingCartOutlined />} value={1128} />
      </ProCard>
    </Col>
    <Col span={8}>
      <ProCard title="待处理" headerBordered>
        <Statistic prefix={<ClockCircleOutlined />} value={23} valueStyle={{ color: '#faad14' }} />
      </ProCard>
    </Col>
    <Col span={8}>
      <ProCard title="已完成" headerBordered>
        <Statistic prefix={<CheckCircleOutlined />} value={1105} valueStyle={{ color: '#52c41a' }} />
      </ProCard>
    </Col>
  </Row>
);
```

要点：
- `Statistic`、`Row`、`Col` 从 `antd` 导入，`ProCard` 从 `@ant-design/pro-components`
- 多卡片用 `Row`+`Col span={8}`（三列）或 `span={6}`（四列），不用 flex div
