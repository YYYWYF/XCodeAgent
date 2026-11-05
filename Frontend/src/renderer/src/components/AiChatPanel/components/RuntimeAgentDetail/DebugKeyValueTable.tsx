import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Checkbox, Input, Table } from 'antd'
import { debugKeyValueMap } from '../../../../service/debugKeyValue'
import type { DebugKeyValueRow } from '../../../../service/debugKeyValue'
import './DebugKeyValueTable.less'

/** 复用 Ant Design Table 编辑参数和 Header，启用、增删操作只改变本地请求草稿。 */
export default function DebugKeyValueTable({ label, rows, onChange, disabled, fixedKeys, validate }: {
  label: string; rows: DebugKeyValueRow[]; onChange: (rows: DebugKeyValueRow[]) => void; disabled?: boolean
  fixedKeys?: boolean; validate?: (values: Record<string, string>) => void
}): JSX.Element {
  let error = ''
  try { const values = debugKeyValueMap(rows, label); validate?.(values) } catch (reason) { error = reason instanceof Error ? reason.message : '参数无效。' }
  /** 只更新指定行，保留停用行的内容，便于修改后再次启用。 */
  const update = (id: string, patch: Partial<DebugKeyValueRow>): void => onChange(rows.map((row) => row.id === id ? { ...row, ...patch } : row))
  return <section className="debug-key-value-table" aria-label={label}>
    <h5>{label}</h5>
    <Table<DebugKeyValueRow> size="small" pagination={false} rowKey="id" tableLayout="fixed" dataSource={rows}
      locale={{ emptyText: '暂无参数，可添加 Key–Value 行' }} columns={[
        { key: 'enabled', title: <span className="debug-key-value-enabled-label">启用</span>, width: 48,
          render: (_, row) => <Checkbox aria-label={`启用 ${label} ${row.key || '未命名行'}`} checked={row.enabled}
            disabled={disabled || fixedKeys} onChange={(event) => update(row.id, { enabled: event.target.checked })} /> },
        { key: 'key', title: 'Key', width: '36%', render: (_, row) => <Input aria-label={`${label} Key`} placeholder="Key"
          value={row.key} disabled={disabled || fixedKeys} onChange={(event) => update(row.id, { key: event.target.value })} /> },
        { key: 'value', title: 'Value', render: (_, row) => <Input aria-label={`${label} ${row.key || '未命名行'} Value`}
          placeholder="Value" value={row.value} disabled={disabled} onChange={(event) => update(row.id, { value: event.target.value })} /> },
        ...(!fixedKeys ? [{ key: 'remove', width: 36, render: (_: unknown, row: DebugKeyValueRow) => <Button type="text" size="small"
          icon={<DeleteOutlined />} aria-label={`删除 ${label} ${row.key || '未命名行'}`} disabled={disabled}
          onClick={() => onChange(rows.filter((item) => item.id !== row.id))} /> }] : [])
      ]} />
    {!fixedKeys && <Button type="text" size="small" icon={<PlusOutlined />} disabled={disabled}
      onClick={() => onChange([...rows, { id: crypto.randomUUID(), key: '', value: '', enabled: true }])}>添加行</Button>}
    {error && <p className="debug-key-value-error" role="alert">{error}</p>}
  </section>
}
