# 页面内查询和组件消费

简单列表页面直接使用 ahooks `useRequest` 管理查询，通过 ProTable 的 `dataSource`、`loading` 和受控分页展示结果。不强制封装自定义 Hook，也不额外维护与 `useRequest` 重复的请求状态。

## 基础分页查询示例

```tsx
// src/pages/Orders/index.tsx
import { useState } from 'react';
import { Alert, Button } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import { useRequest } from 'ahooks';
import { fetchOrderList } from '@/apis/orderApi';
import type { Order, OrderQuery } from '@/apis/orderApi';

export default function Orders() {
  const [query, setQuery] = useState<OrderQuery>({
    page: 1,
    pageSize: 10,
  });

  const { data, loading, error, refresh } = useRequest(
    () => fetchOrderList(query),
    {
      refreshDeps: [query.page, query.pageSize],
    },
  );

  const columns: ProColumns<Order>[] = [
    { title: '订单号', dataIndex: 'id' },
    { title: '状态', dataIndex: 'status' },
  ];

  return (
    <>
      {error && (
        <Alert
          type="error"
          message="查询失败，请稍后重试"
          action={<Button onClick={refresh}>重试</Button>}
        />
      )}

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
          onChange: (page, pageSize) => {
            setQuery({ page, pageSize });
          },
        }}
      />
    </>
  );
}
```

## 使用说明

- **数据请求**：`useRequest` 统一提供 `data`、`loading`、`error` 和 `refresh`。
- **数据与展示解耦**：页面通过 `dataSource`、`loading` 等属性向 ProTable 传递状态，不使用 ProTable 的 `request` 属性发起业务查询。
- **错误反馈**：使用安全、易理解的提示文案；不直接向用户展示未经处理的错误码、堆栈或技术异常。示例提供手动重试。
- **分页管理**：`query` 保存当前页码和每页条数；变更后通过 `refreshDeps` 自动重新查询。
- **结果保留**：`useRequest` 可能保留上次成功的 `data`。

## 动态选项与事件操作

动态选项同样由 `useRequest` 获取，并通过 `<ProFormSelect options={options ?? []} />` 消费；固定业务选项引用已授权的常量文件。

新增、修改、删除、确认等事件型操作可直接在事件处理函数或表单 `onFinish` 中调用已授权的业务 API；操作成功后根据需要调用 `refresh()` 刷新查询。只有复杂联动或逻辑复用时才抽取自定义 Hook。
