import { Table } from 'antd'
import type { DebugConversation } from '../../../../service/runtimeDebugConversation'
import './AgentDebugEvents.less'

/** 按接收顺序逐行展示本次请求的全部事件，展开后查看原始 AG-UI 对象。 */
export default function AgentDebugEvents({ events = [] }: { events?: DebugConversation['events'] }): JSX.Element {
  const rows = events.map((event, index) => ({ ...event, sequence: index + 1 }))
  return <div className="agent-debug-events">
    <p>本次请求 · {events.length} 条 AG-UI 事件<span>按接收顺序排列，展开查看完整内容</span></p>
    <Table size="small" tableLayout="fixed" rowKey="sequence" pagination={false}
      dataSource={rows} scroll={{ x: 640, y: 420 }} locale={{ emptyText: '等待接收 AG-UI 事件' }}
      columns={[
        { title: '序号', dataIndex: 'sequence', width: 60 },
        { title: '事件类型', dataIndex: 'type', width: 230 },
        { title: '数据', key: 'data', ellipsis: true,
          // 预览保留 JSON 转义，长事件通过展开阅读，不截断原始数据。
          render: (_, row) => <code>{JSON.stringify(row.value)}</code> }
      ]}
      expandable={{ expandedRowRender: (row) => <pre>{JSON.stringify(row.value, null, 2)}</pre>,
        columnWidth: 36 }} />
  </div>
}
