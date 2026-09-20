import { Empty } from 'antd'
import type { ReactElement } from 'react'
import type { DataSourceDirectory, DataSourceOperation, ExternalApiDataSource } from '../../typings'

export type ExternalApiListItem = { source: ExternalApiDataSource; directory: DataSourceDirectory; operation: DataSourceOperation }

type Props = { items: ExternalApiListItem[]; onOpen: (item: ExternalApiListItem) => void }

/** 将当前域下的外部接口平铺为紧凑清单，暂时隐藏底层目录信息。 */
export default function ExternalApisPane({ items, onOpen }: Props): ReactElement {
  return <div className="source-drawer-rows">
    {items.map((item) => <button key={`${item.source.id}:${item.operation.id}`} onClick={() => onOpen(item)} type="button">
      <code className="source-method">{item.operation.method}</code><strong>{item.operation.name}</strong><span title={item.operation.path}>{item.operation.path}</span>
    </button>)}
    {!items.length ? <Empty description="当前域暂无外部 API 接口" /> : null}
  </div>
}
