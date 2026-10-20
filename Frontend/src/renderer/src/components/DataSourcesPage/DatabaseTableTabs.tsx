import { ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Modal, Select, Table, Tabs } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { DatabaseDataPage, DatabaseTableOperation } from '../../typings/managedDatabase'
import type { SelectedDataTable } from '../../service/dataSources'
import { requestDatabaseTableOperations, requestDatabaseTablePage } from '../../service/databaseTableData'
import { subscribeDataSourcesChanged } from '../../service/dataSourceEvents'
import { DatabaseTableDetail } from './DatabaseTables'
import { databaseOperationName } from '../DatabaseOperationTag'
import { cx } from '../../utils'
import './DatabaseTableTabs.less'

type Props = { workspaceRoot: string; table: SelectedDataTable; theme: 'light' | 'dark' }

/** 内置表详情按结构、数据、操作分栏；非活动栏卸载，切表由父组件重新建立身份。 */
export default function DatabaseTableTabs({ workspaceRoot, table, theme }: Props): ReactElement {
  return <div className="source-table-tabs"><Tabs defaultActiveKey="structure" destroyInactiveTabPane items={[
    { key: 'structure', label: '表结构', children: <DatabaseTableDetail workspaceRoot={workspaceRoot} table={table} readOnly onRemoved={() => undefined} /> },
    { key: 'data', label: '表数据', children: <TableDataPanel workspaceRoot={workspaceRoot} table={table} theme={theme} /> },
    { key: 'operations', label: '操作记录', children: <TableOperationsPanel workspaceRoot={workspaceRoot} table={table} theme={theme} /> }
  ]} /></div>
}

/** 只读数据分页采用 hasMore，不展示伪造总条数，刷新与建表通知均读取当前页事实。 */
function TableDataPanel({ workspaceRoot, table, theme }: Props): ReactElement {
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(20)
  const [revision, setRevision] = useState(0)
  const [data, setData] = useState<DatabaseDataPage>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [cell, setCell] = useState<{ name: string; value: string }>()

  useEffect(() => subscribeDataSourcesChanged(workspaceRoot, () => {
    setPage(1); setRevision((current) => current + 1); setCell(undefined)
  }), [workspaceRoot])
  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    setLoading(true); setError(''); setData(undefined); setCell(undefined)
    requestDatabaseTablePage(workspaceRoot, table.sourceId, table.table, page, size, controller.signal)
      .then((value) => { if (!disposed) setData(value) })
      .catch((reason) => { if (!disposed) setError(reason instanceof Error ? reason.message : '表数据读取失败。') })
      .finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true; controller.abort() }
  }, [workspaceRoot, table.sourceId, table.table, page, size, revision])

  /** 展示精确单元格，NULL、空字符串及二进制格式分开表达。 */
  const renderCell = (name: string, value: string | null): ReactElement => {
    if (value === null) return <span className="source-data-null" title="SQL NULL">NULL</span>
    if (value === '') return <span className="source-data-null">空字符串</span>
    const format = data?.columnFormats[name] === 'HEX' ? '0x' : ''
    if (value.length > 120 || value.includes('\n')) return <div className="source-data-long"><span>{format}{value.slice(0, 60)}…</span>
      <Button type="link" size="small" onClick={() => setCell({ name, value: format + value })}>查看内容</Button></div>
    return <span className="source-data-value">{format}{value}</span>
  }

  return <section className="source-table-data">
    <div className="source-table-data-toolbar"><span>只读查看调试数据</span>
      <Button icon={<ReloadOutlined />} loading={loading} onClick={() => setRevision((current) => current + 1)}>刷新数据</Button></div>
    {error && <Alert showIcon type="error" message={error} />}
    {data?.truncated && <Alert showIcon type="warning" message="长字段已截断为预览内容，此处展示的数据不适合作为完整导出。" />}
    <Table<Record<string, string | null>> size="small" tableLayout="fixed" rowKey={(_row, index) => String(index)}
      loading={loading} pagination={false} dataSource={data?.rows || []} scroll={{ x: 'max-content', y: 420 }}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={error ? '暂时无法读取数据' : '暂无数据，可在调试页面提交后刷新'} /> }}
      columns={(data?.columns || []).map((column) => ({ key: column.name, dataIndex: column.name, width: 190,
        title: <div className="source-data-column" title={`${column.type}${column.primaryKey ? ' · 主键' : ''}`}>
          <strong>{column.name}</strong><small>{column.description || column.type}</small></div>,
        render: (value: string | null) => renderCell(column.name, value) }))} />
    <div className="source-table-data-pagination"><span>第 {page} 页{data ? ` · 本页 ${data.rows.length} 条` : ''}</span>
      <Select aria-label="每页条数" value={size} disabled={loading} getPopupContainer={(node) => node.parentElement || node}
        onChange={(value) => { setSize(value); setPage(1) }}
        options={[10, 20, 50, 100].map((value) => ({ value, label: `${value} 条/页` }))} />
      <Button disabled={loading || page <= 1} onClick={() => setPage((current) => current - 1)}>上一页</Button>
      <Button disabled={loading || !data?.hasMore || page * size > 100000} onClick={() => setPage((current) => current + 1)}>下一页</Button>
    </div>
    <Modal title={cell?.name} visible={!!cell} footer={null} width={680}
      wrapClassName={`${cx('data-source-editor-modal-wrap', `theme-${theme}`)} source-table-cell-modal`} onCancel={() => setCell(undefined)}>
      <pre>{cell?.value}</pre>
    </Modal>
  </section>
}

const operationStates = { RUNNING: '执行中', SUCCEEDED: '成功', FAILED: '失败', UNKNOWN: '待核对' }

/** 将操作时间转为本地可读文本，不对无效日期做错误转换。 */
function operationTime(value: string): string {
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString('zh-CN', { hour12: false })
}

/** 按当前表展示受限最近资源操作，支持手动刷新，不提供重放或删除按钮。 */
function TableOperationsPanel({ workspaceRoot, table }: Props): ReactElement {
  const [result, setResult] = useState<{ operations: DatabaseTableOperation[]; historyLimit: number }>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => subscribeDataSourcesChanged(workspaceRoot, () => setRevision((current) => current + 1)), [workspaceRoot])
  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    setLoading(true); setError(''); setResult(undefined)
    requestDatabaseTableOperations(workspaceRoot, table.sourceId, table.table, controller.signal)
      .then((value) => { if (!disposed) setResult(value) })
      .catch((reason) => { if (!disposed) setError(reason instanceof Error ? reason.message : '操作记录读取失败。') })
      .finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true; controller.abort() }
  }, [workspaceRoot, table.sourceId, table.table, revision])
  return <section className="source-table-data">
    <div className="source-table-data-toolbar"><span>当前表最近资源操作</span>
      <Button icon={<ReloadOutlined />} loading={loading} onClick={() => setRevision((current) => current + 1)}>刷新记录</Button></div>
    <p className="source-table-data-hint">展示本应用最近 {result?.historyLimit || 100} 条操作中与当前表相关的记录。</p>
    {error && <Alert showIcon type="error" message={error} />}
    <Table<DatabaseTableOperation> size="small" rowKey="operationId" loading={loading} dataSource={result?.operations || []}
      pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }} scroll={{ x: 720 }}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={error ? '暂时无法读取记录' : '最近应用操作中暂无该表记录'} /> }}
      columns={[
        { title: '操作', dataIndex: 'mode', width: 85, render: (value: DatabaseTableOperation['mode']) => databaseOperationName(value) },
        { title: '状态', dataIndex: 'status', width: 85, render: (value: DatabaseTableOperation['status']) => <span className={`source-operation-state is-${value.toLowerCase()}`}>{operationStates[value]}</span> },
        { title: '时间', dataIndex: 'createdAt', width: 170, render: operationTime },
        { title: '操作 ID', dataIndex: 'operationId', width: 180, ellipsis: true, className: 'source-data-value' },
        { title: '说明', key: 'summary', render: (_value, record) => record.executionResult.errorSummary || '—' }
      ]} />
  </section>
}
