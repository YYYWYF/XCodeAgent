import { DeleteOutlined } from '@ant-design/icons'
import { Button, Empty } from 'antd'
import type { ReactElement } from 'react'
import type { DataSourceDirectory, DataSourceOperation, ExternalApiDataSource } from '../../typings'

export type ExternalApiListItem = { source: ExternalApiDataSource; directory: DataSourceDirectory; operation: DataSourceOperation }

type Props = {
  items: ExternalApiListItem[]
  selectedId?: string
  saving: boolean
  onOpen: (item: ExternalApiListItem) => void
  onDelete: (item: ExternalApiListItem) => void
}

/** 将当前域下的外部接口平铺为紧凑清单，暂时隐藏底层目录信息。 */
export default function ExternalApisPane({ items, selectedId, saving, onOpen, onDelete }: Props): ReactElement {
  return (
    <div className="source-drawer-rows">
      {items.map((item) => (
        <div className={`source-api-row${selectedId === item.operation.id ? ' selected' : ''}`}
          key={`${item.source.id}:${item.operation.id}`}>
          {/* 同一接口栏内使用两个独立按钮，删除点击不会触发选中。 */}
          <button className="source-api-row-open" type="button" disabled={saving}
            aria-current={selectedId === item.operation.id ? 'true' : undefined} onClick={() => onOpen(item)}>
            <code className="source-method">{item.operation.method}</code>
            <strong title={item.operation.name}>{item.operation.name}</strong>
            <span className="source-api-row-path" title={item.operation.path}>{item.operation.path}</span>
          </button>
          <Button className="source-api-row-delete" size="small" icon={<DeleteOutlined />} title="删除接口" disabled={saving}
            aria-label={`删除接口 ${item.operation.name}`} onClick={() => onDelete(item)}>
            删除
          </Button>
        </div>
      ))}
      {!items.length && <Empty description="当前域暂无外部 API 接口" />}
    </div>
  )
}
