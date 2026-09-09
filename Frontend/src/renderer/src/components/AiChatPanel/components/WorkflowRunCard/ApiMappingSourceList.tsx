import { Button, Space, Typography } from 'antd'
import { useState } from 'react'
import type { ComponentProps, ReactElement } from 'react'
import type { WorkflowApiSourceField } from '../../../../typings'
import ApiSourceFieldSelector from './ApiSourceFieldSelector'
import { sourceSnapshotToNode } from './apiDesignTableModel'
import { apiDesignSourceFieldLabel, sourceFieldSnapshot } from './apiDesignSerialization'

type Props = Omit<ComponentProps<typeof ApiSourceFieldSelector>, 'selectedSourceNode' | 'onSelect' | 'onClear'> & {
  sources: WorkflowApiSourceField[]
  onChange: (sources: WorkflowApiSourceField[]) => void
}

/** 管理多个来源，仅挂载一个选择器以隔离实时元数据编辑。 */
export default function ApiMappingSourceList({ sources, onChange, ...selector }: Props): ReactElement {
  const [editing, setEditing] = useState<number | null>(null)
  return <div className="api-design-source-list">
    {sources.map((source, index) => <div className="api-design-source-list-row" key={index}>
      <Typography.Text>{apiDesignSourceFieldLabel(source)}</Typography.Text>
      <Space>
        <Button disabled={selector.disabled} onClick={() => setEditing(index)}>编辑</Button>
        <Button danger disabled={selector.disabled} onClick={() => {
          onChange(sources.filter((_item, position) => position !== index))
          setEditing(null)
        }}>删除</Button>
      </Space>
    </div>)}
    <Button disabled={selector.disabled || sources.length >= 100} onClick={() => setEditing(sources.length)}>添加来源字段</Button>
    {editing !== null ? <div className="api-design-editor-section">
      <ApiSourceFieldSelector {...selector} key={editing}
        selectedSourceNode={sources[editing] ? sourceSnapshotToNode(sources[editing]) : undefined}
        onClear={() => setEditing(null)}
        onSelect={(node) => {
          const next = [...sources]
          next[editing] = sourceFieldSnapshot(node, selector.endpoint)
          onChange(next)
          setEditing(null)
        }} />
      <Button onClick={() => setEditing(null)}>取消选择</Button>
    </div> : null}
  </div>
}
