import { confirmWorkspaceAction } from '../workspaceDialogs'
import { SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Checkbox, Empty, Input, Modal, Space, Spin, Table, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { DatabaseDataSource } from '../../typings'
import { changeSelectedTables, requestApiDesignDatabaseColumns, requestApiDesignDatabaseTables, requestSourceReferences } from '../../service/dataSources'
import type { ApiDesignDatabaseMetadata, SelectedDataTable } from '../../service/dataSources'
import { cx } from '../../utils'
import './DatabaseTables.less'

const { Text } = Typography

type ImportProps = { workspaceRoot: string; source: DatabaseDataSource; selected: SelectedDataTable[]; theme: 'light' | 'dark'; onClose: () => void; onSaved: (tables: SelectedDataTable[]) => void }

/** 从实时发现的数据库表中批量添加应用候选，不创建或修改数据库表。 */
export function AddDatabaseTables({ workspaceRoot, source, selected, theme, onClose, onSaved }: ImportProps): ReactElement {
  const [metadata, setMetadata] = useState<ApiDesignDatabaseMetadata>()
  const [filterInput, setFilterInput] = useState('')
  const [filterText, setFilterText] = useState('')
  const [checked, setChecked] = useState<string[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [retry, setRetry] = useState(0)
  const added = new Set(selected.filter((item) => item.sourceId === source.id && item.schema === metadata?.schema).map((item) => item.table))
  useEffect(() => {
    let disposed = false
    setLoading(true); setError('')
    requestApiDesignDatabaseTables(workspaceRoot, source.id).then((value) => { if (!disposed) setMetadata(value) })
      .catch((reason) => { if (!disposed) setError(String(reason.message || reason)) })
      .finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [workspaceRoot, source.id, retry])

  /** 提交增量选择，由服务端再次验证真实表存在性。 */
  const save = async (): Promise<void> => {
    setSaving(true); setError('')
    try { onSaved(await changeSelectedTables(workspaceRoot, source.id, checked)); onClose() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '添加失败。') }
    finally { setSaving(false) }
  }
  /** 关闭选择窗口前保护尚未提交的批量勾选。 */
  const cancel = (): void => {
    if (saving) return
    if (!checked.length) { onClose(); return }
    confirmWorkspaceAction({ title: '放弃未添加的数据表选择？', okText: '放弃选择', cancelText: '继续选择', onOk: onClose })
  }
  /** 仅在用户按下回车后应用当前输入的前端筛选条件。 */
  const applyFilter = (): void => { setFilterText(filterInput.trim()) }
  /** 清空输入时同步清除已生效的筛选结果，其余输入只保留待提交文本。 */
  const changeFilterInput = (value: string): void => { setFilterInput(value); if (!value) setFilterText('') }
  // 只在前端按表名和说明筛选，不改变实时元数据请求与提交的数据边界。
  const normalizedFilter = filterText.trim().toLowerCase()
  const visible = (metadata?.tables || []).filter((table) => `${table.name} ${table.description || ''}`.toLowerCase().includes(normalizedFilter)).sort((a, b) => a.name.localeCompare(b.name))
  return <Modal centered className={cx('data-source-table-picker-modal')} destroyOnClose getContainer={false}
    title={`添加数据表 · ${source.name}`} visible width={480} wrapClassName={cx('data-source-editor-modal-wrap', `theme-${theme}`)}
    bodyStyle={{ padding: 0, overflow: 'hidden' }} onCancel={cancel} onOk={() => void save()}
    okText={`添加 ${checked.length} 张表`} cancelText="取消" okButtonProps={{ disabled: !checked.length || loading, loading: saving }}>
    <div className={cx('data-source-table-picker-body')}>
      <Text className={cx('data-source-table-picker-hint')} type="secondary">勾选「{source.name}」中要添加的数据表</Text>
      {error ? <Alert className={cx('data-source-table-picker-error')} type="error" message={error} showIcon action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>} /> : null}
      <Input allowClear aria-label="筛选数据表" className={cx('data-source-table-picker-filter')} onChange={(event) => changeFilterInput(event.target.value)} onPressEnter={applyFilter} placeholder="筛选表名或说明，按回车筛选" prefix={<SearchOutlined />} value={filterInput} />
      <Spin spinning={loading}><div className={cx('data-source-table-picker-list')}>
        {visible.map((table) => {
          const alreadyAdded = added.has(table.name)
          const columnCount = typeof table.columnCount === 'number' ? ` · ${table.columnCount} 字段` : ''
          return <label className={cx('data-source-table-picker-item', alreadyAdded && 'is-added')} key={table.name}>
            <Checkbox disabled={alreadyAdded || saving} checked={alreadyAdded || checked.includes(table.name)}
              onChange={(event) => setChecked((value) => event.target.checked ? [...value, table.name] : value.filter((name) => name !== table.name))} />
            <span className={cx('data-source-table-picker-item-main')}><strong>{table.name}</strong><small>{table.description || '暂无说明'}{columnCount}</small></span>
            {alreadyAdded ? <em>已添加</em> : null}
          </label>
        })}
        {!loading && !visible.length ? <Empty description={normalizedFilter ? '暂无匹配的数据表' : '暂无可添加的数据表'} /> : null}
      </div></Spin>
    </div>
  </Modal>
}

/** 展示表字段并只移除应用中的候选引用。 */
export function DatabaseTableDetail({ workspaceRoot, table, onRemoved }: { workspaceRoot: string; table: SelectedDataTable; onRemoved: (tables: SelectedDataTable[]) => void }): ReactElement {
  const [metadata, setMetadata] = useState<ApiDesignDatabaseMetadata>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let disposed = false
    setLoading(true); setError(''); setMetadata(undefined)
    requestApiDesignDatabaseColumns(workspaceRoot, table.sourceId, table.table).then((value) => { if (!disposed) setMetadata(value) })
      .catch((reason) => { if (!disposed) setError(String(reason.message || reason)) })
      .finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [workspaceRoot, table.sourceId, table.table, retry])

  /** 展示引用后确认移除，操作不会删除真实表或正式映射。 */
  const remove = async (): Promise<void> => {
    try {
      const references = await requestSourceReferences(workspaceRoot, table.sourceId, { table: table.table })
      confirmWorkspaceAction({ title: `移除 ${table.table}？`, content: `只移除应用候选，不删除真实表。${references.length ? `关联映射：${references.join('、')}，再次编辑需重新添加。` : '暂无正式映射引用。'}`,
        okText: '移除', cancelText: '取消', okButtonProps: { danger: true }, onOk: async () => {
          try { onRemoved(await changeSelectedTables(workspaceRoot, table.sourceId, [table.table], true)) }
          catch (reason) { setError(reason instanceof Error ? reason.message : '移除失败。'); throw reason }
        } })
    } catch (reason) { setError(reason instanceof Error ? reason.message : '读取引用失败。') }
  }
  return <div className="source-table-detail"><p>{table.schema} · {table.table}</p><p>{table.description}</p>
    {error && <Alert type="error" message={error} action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>} />}
    <Table size="small" loading={loading} rowKey="name" dataSource={metadata?.columns || []} pagination={false}
      columns={[{ title: '字段', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' }, { title: '说明', dataIndex: 'description' }]} />
    <Space><Button danger onClick={() => void remove()}>移除</Button></Space></div>
}
