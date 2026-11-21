# 组合页面与状态边界

只在当前任务需要多标签页、批量操作或多弹窗组合时读取。沿用 `component-patterns.md` 中手动优化的 Orders 示例：页面 State 管查询，useRequest + refreshDeps 管请求，Result 展示失败，Spin 管提交反馈。沿用同文件的示例 API 契约；接口、组件及文件均需已获任务授权。简单列表无需自定义 Hook，不维护第二份请求状态或 requestQuery 校验。

## 多标签页查询列表：独立状态

每个标签页组件拥有自己的 useRequest、查询参数、分页和选择；Ant Design Tabs 默认惰性挂载已访问页并保留它们（此例不销毁 inactive pane）。首次进入才查询，切回不重复请求、不串数据；切换标签不改另一页的查询或选择。不使用 key=activeTab 强制重建所有页面。

```tsx
// src/pages/OrderTabs/index.tsx
import { useState } from 'react';
import type { Key } from 'react';
import { Button, Result, Tabs } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import { useRequest } from 'ahooks';
import { fetchOrderList } from '@/apis/order';
import type { OrderListType, OrderQuery } from '@/apis/order';

/** 将一个标签的筛选、分页和勾选状态限制在自己的组件实例内。 */
function OrderTab({ status }: { status: string }) {
  const [query, setQuery] = useState<OrderQuery>({ status, current: 1, pageSize: 10 });
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);
  const { data, loading, error, refresh } = useRequest(() => fetchOrderList(query), {
    refreshDeps: [query.current, query.pageSize, query.status, query.keyword],
  });
  /** 当前标签改变查询时仅清理自身选择。 */
  function changeQuery(next: OrderQuery) {
    setSelectedKeys([]);
    setQuery(next);
  }
  if (error && !loading) {
    return <Result status="error" title="订单列表加载失败" subTitle="请稍后重试"
      extra={<Button onClick={() => { setSelectedKeys([]); refresh(); }}>重试</Button>} />;
  }
  return <ProTable<OrderListType, { keyword?: string }> rowKey="id" options={{ reload: false }}
      columns={[
        { title: '订单号', dataIndex: 'id', hideInSearch: true },
        { title: '关键词', dataIndex: 'keyword', hideInTable: true },
        { title: '状态', dataIndex: 'status', hideInSearch: true },
      ]}
      onSubmit={values => changeQuery({ ...query, keyword: values.keyword, current: 1 })}
      onReset={() => changeQuery({ status, current: 1, pageSize: query.pageSize })}
      dataSource={data?.data ?? []} loading={loading}
      rowSelection={{ selectedRowKeys: selectedKeys, preserveSelectedRowKeys: false,
        onChange: keys => setSelectedKeys(keys),
        getCheckboxProps: () => ({ disabled: loading }) }}
      pagination={{ showSizeChanger: true, current: query.current, pageSize: query.pageSize, total: data?.total ?? 0,
        onChange: (current, pageSize) => changeQuery({
          ...query, current: query.pageSize === pageSize ? current : 1, pageSize,
        }) }} />
}

/** 保留已访问标签的独立状态，不在 onChange 中重复触发查询。 */
export default function OrderTabs() {
  return <Tabs defaultActiveKey="pending" destroyInactiveTabPane={false} items={[
    { key: 'pending', label: '待处理', children: <OrderTab status="pending" /> },
    { key: 'rejected', label: '已拒绝', children: <OrderTab status="rejected" /> },
  ]} />;
}
```

只有需求明确要求“所有标签共享筛选”时，才将筛选值提升到父组件，并明确切换后是否重置分页/选择。不能把不同查询的 rows/total 合并成全局列表。若操作会影响其他标签，应按业务刷新受影响的已挂载查询；不要为所有切换无条件刷新或新建缓存。

## 查询 + 批量操作 + 多弹窗组合

页面拥有查询和当前有效选择；编辑表单内聚输入、校验、提交和异常反馈；只读详情与编辑使用可区分的弹窗状态，避免两个弹窗共用 record/open 导致串数据。编辑器见 `component-patterns.md`，不能仅更新 initialValues 而不重置实例。

```tsx
// src/pages/OrderManagement/index.tsx
import { useState } from 'react';
import type { Key } from 'react';
import { Button, Descriptions, Drawer, message, Result, Spin } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import { useRequest } from 'ahooks';
import { batchConfirmOrders, fetchOrderList } from '@/apis/order';
import type { OrderListType, OrderQuery } from '@/apis/order';
import OrderEditor from '@/components/OrderEditor';

type Dialog = { mode: 'edit'; record: OrderListType | null } | { mode: 'detail'; record: OrderListType };
const INITIAL_QUERY: OrderQuery = { current: 1, pageSize: 10 };

/** 组合查询、批量事件和独立编辑器，不在页面重写业务 API。 */
export default function OrderManagement() {
  const [dialog, setDialog] = useState<Dialog>();
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [query, setQuery] = useState<OrderQuery>(INITIAL_QUERY);
  const { data, loading, error, refresh } = useRequest(() => fetchOrderList(query), {
    refreshDeps: [query.current, query.pageSize, query.status],
  });
  const rows = data?.data ?? [];
  const ids = rows.filter(row => selectedKeys.includes(row.id)).map(row => row.id);
  /** 切换查询时清理当前页选择。 */
  function changeQuery(next: OrderQuery) {
    if (submitting || dialog) return;
    setSelectedKeys([]);
    setQuery(next);
  }
  /** 刷新时不沿用旧行选择。 */
  function handleSaved() {
    setSelectedKeys([]);
    refresh();
  }
  /** 在事件中执行已确认的批量操作，失败保留重试上下文。 */
  async function handleBatch() {
    if (submitting || loading || dialog || !ids.length) return;
    setSubmitting(true);
    try { await batchConfirmOrders(ids); message.success('操作成功'); handleSaved(); }
    catch { message.error('操作失败，请稍后重试'); }
    finally { setSubmitting(false); }
  }
  if (error && !loading && !dialog) {
    return <Result status="error" title="订单列表加载失败" subTitle="请稍后重试"
      extra={<Button onClick={handleSaved}>重试</Button>} />;
  }
  return <>
    <Spin spinning={submitting} tip="正在提交...">
      <ProTable<OrderListType, { status?: string }> rowKey="id" dataSource={rows} loading={loading} options={{ reload: false }}
        columns={[
          { title: '订单号', dataIndex: 'id', hideInSearch: true },
          { title: '状态', dataIndex: 'status' },
          { title: '操作', valueType: 'option', render: (_, record) => [
            <Button key="detail" disabled={submitting} onClick={() => setDialog({ mode: 'detail', record })}>详情</Button>,
            <Button key="edit" disabled={submitting} onClick={() => setDialog({ mode: 'edit', record })}>编辑</Button>,
          ] },
        ]}
        onSubmit={values => changeQuery({ ...query, status: values.status, current: 1 })}
        onReset={() => changeQuery({ current: 1, pageSize: query.pageSize })}
        rowSelection={{ selectedRowKeys: selectedKeys, preserveSelectedRowKeys: false,
          onChange: keys => setSelectedKeys(keys),
          getCheckboxProps: () => ({ disabled: submitting || loading }) }}
        toolBarRender={() => [
          <Button key="create" disabled={submitting} onClick={() => setDialog({ mode: 'edit', record: null })}>新增</Button>,
          <Button key="batch" loading={submitting} disabled={loading || !!error || !ids.length}
            onClick={handleBatch}>批量确认</Button>,
        ]}
        pagination={{ showSizeChanger: true, current: query.current, pageSize: query.pageSize,
          total: data?.total ?? 0, disabled: submitting || !!dialog,
          onChange: (current, pageSize) => changeQuery({
            ...query, current: query.pageSize === pageSize ? current : 1, pageSize,
          }) }} />
    </Spin>
    {dialog?.mode === 'edit' && <OrderEditor key={dialog.record?.id ?? 'create'}
      record={dialog.record ?? undefined} onClose={() => setDialog(undefined)} onSaved={handleSaved} />}
    {dialog?.mode === 'detail' && <Drawer title="订单详情" open onClose={() => setDialog(undefined)}>
      <Descriptions column={1}>
        <Descriptions.Item label="订单号">{dialog.record.id}</Descriptions.Item>
        <Descriptions.Item label="状态">{dialog.record.status}</Descriptions.Item>
      </Descriptions>
    </Drawer>}
  </>;
}
```

详情仅展示已有列表字段；需要详情接口时由独立 useRequest 查询，不自行补全未声明字段。编辑器保存成功刷新；关闭后卸载，下次新增没有上次编辑残留。错误提示不得泄漏异常堆栈。异步操作期间禁用重复提交和记录切换。

## 导入导出接入条件

只有正式 API 已声明且文件、依赖均可用时才实现，不假设安装 xlsx/mockjs。服务端导入可由业务 API 接收 File/FormData，上传组件通过 beforeUpload 返回 false 保留文件，由确认事件调用业务 API；文件状态留在独立弹窗，关闭/成功后清理，失败保留重试。文件类型、大小、字段映射和返回格式以契约为准，不自行解析 Excel 或生成临时 HTTP 接口。

导出按已确认“全部筛选结果”或“当前选择 ID”传参，不能导出旧 selectedRows。业务 API 封装传输及二进制响应，页面事件仅处理已返回文件的下载；若使用 object URL，下载完成后 revokeObjectURL 释放资源。缺少 API/依赖时报告缺口，不造接口或修改受管配置。多弹窗可沿用上面的可区分状态，复杂上传状态仅在该弹窗中维护。
