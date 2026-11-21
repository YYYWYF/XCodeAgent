# 页面内查询和组件消费

按场景读取对应章节。简单列表直接使用 ahooks `useRequest`，不强制抽取 Hook，不重复维护 loading/error/data。以下示例使用 React 18、ProComponents 2.x、ahooks 3，以及 Ant Design 4.24 与当前模板 Ant Design 5 均支持的组件接口；真实版本、导出及契约优先，不保证适用于更早的 AntD 4.x。`open`、`Tabs.items` 在 AntD 4.24 已受支持，不应仅据属性名称判为 AntD 5 专属或改回旧 visible。示例不授权新增业务接口、文件或依赖。

## 示例 API 契约

以下模块供本文件及页面组合示例使用。假设订单 ID 不含 URL 保留字符，service 已返回业务 body，失败通过异常表达。仅 API 模块接触 service，页面只导入业务函数；生产任务消费已完成的契约模块，不复制示例接口。

```ts
// src/apis/order.ts
import service from './service';

export interface OrderListType { id: string; status: string; reason?: string }
export interface PageParamsType { current: number; pageSize: number }
export type OrderQuery = PageParamsType & { status?: string; keyword?: string };
export interface OrderList { data: OrderListType[]; total: number }
export interface OrderDraft { status: string; reason?: string }

/** 查询订单列表。 */
export function fetchOrderList(params: OrderQuery): Promise<OrderList> {
  return service.get<OrderList>('/api/orders', { params });
}
/** 创建订单并返回实体。 */
export function createOrder(values: OrderDraft): Promise<OrderListType> {
  return service.post<OrderListType>('/api/orders', values);
}
/** 更新指定订单。 */
export function updateOrder(id: string, values: OrderDraft): Promise<OrderListType> {
  return service.put<OrderListType>('/api/orders/' + id, values);
}
/** 批量确认当前选择的订单。 */
export async function batchConfirmOrders(ids: string[]): Promise<void> {
  await service.post<void>('/api/orders/batch-confirm', { ids });
}
```

## 基础分页查询：筛选、行选择与批量操作

以下 `Orders` 是手动优化后的基准示例：页面 State 保存查询条件和分页，通过 `refreshDeps` 驱动 useRequest；这属于必要的交互状态，不是重复维护请求状态。useRequest 负责 data/loading/error，ProTable 通过属性消费查询结果。加载期间可保留数据并由 loading 明确标识，失败时用 Result 替换列表并提供重试；不强制清空列表，不维护 `data.requestQuery`、竞态标记或第二套 loading/error/data。提交用 Spin、按钮/分页禁用及事件守卫，不用 fieldset 包裹复杂业务组件。示例分页字段为 current，接口模块沿用 `@/apis/order`，不能混入另一套 page 字段或导出名称。

```tsx
// src/pages/Orders/index.tsx

import { useState } from 'react';
import type { Key } from 'react';
import { Button, message, Result, Spin } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import { useRequest } from 'ahooks';

import { fetchOrderList, batchConfirmOrders } from '@/apis/order';
import type { PageParamsType, OrderListType } from '@/apis/order';

type OrderQuery = PageParamsType & {
  status?: string;
};

type SearchValues = {
  status?: string;
};

const INITIAL_QUERY: OrderQuery = {
  current: 1,
  pageSize: 10,
};

export default function Orders() {
  const [query, setQuery] = useState<OrderQuery>(INITIAL_QUERY);
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // useRequest 统一管理列表查询。
  const { data, loading, error, refresh } = useRequest(
    () => fetchOrderList(query),
    {
      refreshDeps: [query.current, query.pageSize, query.status],
    },
  );

  const rows = data?.data ?? [];

  // 从当前页数据派生选中的业务 ID。
  const selectedIds = rows
    .filter((row) => selectedRowKeys.includes(row.id))
    .map((row) => row.id);

  const columns: ProColumns<OrderListType>[] = [
    {
      title: '订单号',
      dataIndex: 'id',
      hideInSearch: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      valueType: 'select',
      valueEnum: {
        pending: { text: '待确认' },
        confirmed: { text: '已确认' },
      },
    },
  ];

  function changeQuery(next: OrderQuery) {
    if (submitting) return;

    setSelectedRowKeys([]);
    setQuery(next);
  }

  async function handleBatchConfirm() {
    if (submitting || loading || selectedIds.length === 0) return;

    setSubmitting(true);

    try {
      await batchConfirmOrders(selectedIds);
      message.success('操作成功');
      setSelectedRowKeys([]);
      refresh();
    } catch {
      message.error('操作失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  }

  if (error && !loading) {
    return (
      <Result
        status='error'
        title='订单列表加载失败'
        subTitle='请稍后重试'
        extra={
          <Button
            onClick={() => {
              setSelectedRowKeys([]);
              refresh();
            }}
          >
            重试
          </Button>
        }
      />
    );
  }

  return (
    <Spin spinning={submitting} tip='正在提交...'>
      <ProTable<OrderListType, SearchValues>
        rowKey='id'
        columns={columns}
        dataSource={rows}
        loading={loading}
        options={{ reload: false }}
        onSubmit={(values) =>
          changeQuery({
            ...query,
            status: values.status,
            current: 1,
          })
        }
        onReset={() =>
          changeQuery({
            current: 1,
            pageSize: query.pageSize,
          })
        }
        rowSelection={{
          selectedRowKeys,
          preserveSelectedRowKeys: false,
          onChange: setSelectedRowKeys,
          getCheckboxProps: () => ({
            disabled: loading || submitting,
          }),
        }}
        toolBarRender={() => [
          <Button
            key='batch'
            type='primary'
            loading={submitting}
            disabled={loading || selectedIds.length === 0}
            onClick={handleBatchConfirm}
          >
            批量确认
          </Button>,
        ]}
        pagination={{
          showSizeChanger: true,
          current: query.current,
          pageSize: query.pageSize,
          total: data?.total ?? 0,
          disabled: submitting,
          onChange: (current, pageSize) =>
            changeQuery({
              ...query,
              current: pageSize === query.pageSize ? current : 1,
              pageSize,
            }),
        }}
      />
    </Spin>
  );
}
```

内置 search 只提交筛选值，不使用 ProTable.request。失败与成功空结果有区分；默认不跨页保留选择。删除最后一行导致页码越界时按实际契约回到有效页，不凭示例增加删除操作。跨页选择须有明确需求和 API 合同。

## ModalForm 新增 / 编辑与 ProFormDependency

父组件仅在打开时挂载编辑器，关闭后卸载，不同记录用 key 重建。initialValues 只在初始化生效；外部更新同一记录时按交互重新打开或显式 resetFields/setFieldsValue。

```tsx
// src/components/OrderEditor/index.tsx
import { useState } from 'react';
import { message } from 'antd';
import { ModalForm, ProFormDependency, ProFormSelect, ProFormText } from '@ant-design/pro-components';
import { createOrder, updateOrder } from '@/apis/order';
import type { OrderListType, OrderDraft } from '@/apis/order';

interface Props { record?: OrderListType; onClose: () => void; onSaved: () => void }

/** 打开时初始化表单，保存后通知父级刷新。 */
export default function OrderEditor({ record, onClose, onSaved }: Props) {
  const [submitting, setSubmitting] = useState(false);
  /** 校验后提交；异常保留输入，成功才关闭。 */
  async function handleFinish(values: OrderDraft) {
    if (submitting) return false;
    setSubmitting(true);
    try {
      // 隐藏字段不提交旧值，可省略性由示例契约定义。
      const payload = { status: values.status,
        ...(values.status === 'rejected' ? { reason: values.reason } : {}) };
      if (record) await updateOrder(record.id, payload);
      else await createOrder(payload);
      message.success('保存成功');
      onSaved();
      return true;
    } catch {
      message.error('保存失败，请稍后重试');
      return false;
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <ModalForm<OrderDraft> title={record ? '编辑订单' : '新增订单'} open
      initialValues={record ?? { status: 'pending' }}
      onOpenChange={open => { if (!open) onClose(); }}
      modalProps={{ destroyOnClose: true, closable: !submitting,
        maskClosable: !submitting, keyboard: !submitting }}
      submitter={{ submitButtonProps: { loading: submitting },
        resetButtonProps: { disabled: submitting } }}
      onFinish={handleFinish}>
      <ProFormSelect name="status" label="状态" disabled={submitting}
        rules={[{ required: true }]} options={[
          { label: '待处理', value: 'pending' }, { label: '拒绝', value: 'rejected' },
        ]} />
      <ProFormDependency name={['status']}>
        {({ status }) => status === 'rejected' ? (
          <ProFormText name="reason" label="拒绝原因" preserve={false}
            disabled={submitting} rules={[{ required: true }]} />
        ) : null}
      </ProFormDependency>
    </ModalForm>
  );
}
```

父级以 undefined 表示关闭、null 表示新增、OrderListType 表示编辑，条件挂载并用 `key={editor?.id ?? 'create'}` 切换，见页面组合示例。提交时遮罩阻止父级切换记录。动态选项用 useRequest 查询后传给 ProFormSelect.options，不在 JSX request 中调用 API。

## DrawerForm / StepsForm

多字段且需保留页面上下文用 DrawerForm；将前例 ModalForm 改为 DrawerForm，容器配置改为 drawerProps，其余生命周期相同。下面提供可独立挂载的抽屉，取消可用，提交时禁用关闭与重复提交。父级关闭时卸载、保存时刷新。

```tsx
// src/components/OrderDrawer/index.tsx
import { useState } from 'react';
import { message } from 'antd';
import { DrawerForm, ProFormText } from '@ant-design/pro-components';
import { createOrder } from '@/apis/order';
import type { OrderDraft } from '@/apis/order';

/** 在独立挂载的抽屉中创建订单。 */
export default function OrderDrawer({ onClose, onSaved }: {
  onClose: () => void; onSaved: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  return <DrawerForm<OrderDraft> title="新增订单" open
    onOpenChange={open => { if (!open) onClose(); }}
    drawerProps={{ destroyOnClose: true, closable: !submitting,
      maskClosable: !submitting, keyboard: !submitting }}
    submitter={{ submitButtonProps: { loading: submitting },
      resetButtonProps: { disabled: submitting } }}
    onFinish={async values => {
      if (submitting) return false;
      setSubmitting(true);
      try {
        await createOrder(values); message.success('保存成功');
        onSaved(); return true;
      } catch { message.error('保存失败，请稍后重试'); return false; }
      finally { setSubmitting(false); }
    }}>
    <ProFormText name="status" label="状态" disabled={submitting} rules={[{ required: true }]} />
  </DrawerForm>;
}
```

确有阶段校验时使用 StepsForm，自动校验当前 StepForm 后推进，最终 onFinish 收到合并值。父级 onSaved 刷新并卸载，重开重新挂载；提交时不要提供外部切换/关闭入口。

```tsx
// src/components/OrderSteps/index.tsx
import { useState } from 'react';
import { message } from 'antd';
import { StepsForm, ProFormText } from '@ant-design/pro-components';
import { createOrder } from '@/apis/order';
import type { OrderDraft } from '@/apis/order';

/** 分阶段校验并汇总字段，成功后交由父级卸载以清理状态。 */
export default function OrderSteps({ onSaved }: { onSaved: () => void }) {
  const [submitting, setSubmitting] = useState(false);
  return <StepsForm<OrderDraft> submitter={{
    submitButtonProps: { loading: submitting, disabled: submitting },
    resetButtonProps: { disabled: submitting },
  }} onFinish={async values => {
    if (submitting) return false;
    setSubmitting(true);
    try { await createOrder(values); message.success('保存成功'); onSaved(); return true; }
    catch { message.error('保存失败，请稍后重试'); return false; }
    finally { setSubmitting(false); }
  }}>
    <StepsForm.StepForm name="basic" title="基本信息">
      <ProFormText name="status" label="状态" disabled={submitting} rules={[{ required: true }]} />
    </StepsForm.StepForm>
    <StepsForm.StepForm name="details" title="补充信息">
      <ProFormText name="reason" label="说明" disabled={submitting} />
    </StepsForm.StepForm>
  </StepsForm>;
}
```

## ProList / ProCard / Row / Col

图文条目用 ProList 的 metas，分组与指标用 ProCard；Row/Col 使用 24 栅格及响应式断点。只消费父级 useRequest 查询结果，不用硬编码颜色模拟卡片。

```tsx
// src/components/OrderOverview/index.tsx
import { Col, Row, Statistic } from 'antd';
import { ProCard, ProList } from '@ant-design/pro-components';
import type { OrderListType } from '@/apis/order';

/** 用响应式栅格分组显示已查询的摘要和条目。 */
export default function OrderOverview({ orders, total }: { orders: OrderListType[]; total: number }) {
  return <Row gutter={[16, 16]}>
    <Col xs={24} md={8}><ProCard title="概览"><Statistic title="订单总数" value={total} /></ProCard></Col>
    <Col xs={24} md={16}><ProCard title="订单">
      <ProList<OrderListType> rowKey="id" dataSource={orders} split
        metas={{ title: { dataIndex: 'id' }, description: { dataIndex: 'status' } }} />
    </ProCard></Col>
  </Row>;
}
```

表单内按业务分区用 ProCard 包裹字段。上传等长标签使用足够宽的 Col 或垂直布局，日期/上传值按当前组件类型和 API 契约转换，不搬运旧 Moment 类型或假定已安装 Excel/Mock 库。
