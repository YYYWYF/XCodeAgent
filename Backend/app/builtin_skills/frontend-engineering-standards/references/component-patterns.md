# 页面内查询和组件消费

页面直接编排单个分页查询。把查询结果和状态传给表格，事件操作直接调用业务 API。

```tsx
// src/pages/Orders/index.tsx
import { useState } from 'react';
import { Alert, Button } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import { useRequest } from 'ahooks';
import { confirmOrder, fetchOrderList } from '@/apis/orderApi';
import type { Order, OrderQuery } from '@/apis/orderApi';

/** 渲染订单列表和已授权的确认操作。 */
export default function Orders() {
  const [query, setQuery] = useState<OrderQuery>({ page: 1, pageSize: 10 });
  const { data, error, loading, refresh } = useRequest(() => fetchOrderList(query), {
    refreshDeps: [query],
  });
  const columns: ProColumns<Order>[] = [
    { title: '订单号', dataIndex: 'id' },
    { title: '状态', dataIndex: 'status' },
    {
      title: '操作',
      render: (_, order) => (
        <Button onClick={async () => {
          await confirmOrder(order.id);
          refresh();
        }}>确认</Button>
      ),
    },
  ];
  return (
    <>
      {error && <Alert type="error" message={error.message} />}
      <ProTable<Order>
        rowKey="id"
        search={false}
        columns={columns}
        dataSource={data?.data ?? []}
        loading={loading}
        pagination={{
          current: query.page,
          pageSize: query.pageSize,
          total: data?.total ?? 0,
          onChange: (page, pageSize) => setQuery({ page, pageSize }),
        }}
      />
    </>
  );
}
```

动态选项由同样的请求层管理，用 `<ProFormSelect options={options ?? []} />` 展示。固定业务选项引用已授权常量文件。
