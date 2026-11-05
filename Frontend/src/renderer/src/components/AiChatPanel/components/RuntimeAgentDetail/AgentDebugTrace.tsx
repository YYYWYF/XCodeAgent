import { Tree } from 'antd'
import { ApartmentOutlined, ToolOutlined } from '@ant-design/icons'
import type { DebugConversation } from '../../../../service/runtimeDebugConversation'
import './AgentDebugTrace.less'

/** 用树形轨迹展示已有调用记录，未接入的模型调用不生成占位执行节点。 */
export default function AgentDebugTrace({ view }: { view: DebugConversation }): JSX.Element {
  const nodes = view.tools.map((tool) => ({
    key: tool.id,
    title: <div className="agent-debug-trace-row">
      <span className="agent-debug-trace-name"><ToolOutlined /><span>{tool.name}</span><small>工具</small></span>
      <span className="agent-debug-trace-state" data-running={tool.elapsed === undefined && view.status === 'running'}>
        {tool.elapsed !== undefined ? '已返回' : view.status === 'running' ? '执行中' : '未收到结果'}</span>
      <span className="agent-debug-trace-duration">{tool.elapsed === undefined ? '—' : `${tool.elapsed} ms`}</span>
    </div>,
    children: [{ key: `${tool.id}:detail`, selectable: false,
      title: <div className="agent-debug-trace-detail">
        <span>输入参数</span><pre>{tool.args || '暂无参数'}</pre>
        <span>返回结果</span><pre>{tool.result || '尚未收到结果'}</pre>
      </div> }]
  }))
  return <details className="agent-debug-trace" aria-label="执行轨迹">
    <summary>执行轨迹<span>{nodes.length ? `最近 ${nodes.length} 个工具节点` : '等待轨迹数据'}</span></summary>
    <div className="agent-debug-trace-columns" aria-hidden="true"><span>调用节点</span><span>状态</span><span>耗时</span></div>
    {nodes.length ? <Tree blockNode showLine={{ showLeafIcon: false }} selectable={false} treeData={nodes} />
      : <div className="agent-debug-trace-empty"><ApartmentOutlined /><strong>暂无执行轨迹</strong>
        <p>接入可观测数据后，在这里查看模型、工具等调用的层级与耗时。</p></div>}
  </details>
}
