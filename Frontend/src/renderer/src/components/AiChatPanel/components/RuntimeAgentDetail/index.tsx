import { RobotOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkbenchExecution } from '../../../../typings'
import type { RuntimeAgent, RuntimeSettingsResult } from '../../../../service/runtimeWorkbench'
import AgentPromptEditor from './AgentPromptEditor'
import AgentDevelopmentStatus from './AgentDevelopmentStatus'
import './RuntimeAgentDetail.less'

/** Agent 详情复用正式技术规划作为权威，修改经原正式修订入口确认后才生效。 */
export default function RuntimeAgentDetail({ agent, workspaceRoot, onSaved, onStartDevelopment, onOpenExecution, disabled }: {
  agent: RuntimeAgent; workspaceRoot: string; onSaved: (result: RuntimeSettingsResult) => void
  onStartDevelopment?: (agent: RuntimeAgent) => Promise<void>
  onOpenExecution?: (execution: WorkbenchExecution) => void
  disabled?: boolean
}): ReactElement {
  return <div className="runtime-agent-detail">
    <header className="agent-detail-hero">
      <span className="agent-detail-avatar"><RobotOutlined /></span>
      <div><span className="agent-detail-kicker">AGENT CONTRACT</span><h2>{agent.name}</h2><code>{agent.agentId}</code></div>
    </header>
        <section className="agent-detail-section">
          <div className="agent-detail-section-title"><h3>概览</h3><Tag>已确认的技术规划</Tag></div>
          <Typography.Paragraph type="secondary">{agent.purpose}</Typography.Paragraph>
          <div className="agent-page-bindings"><span>关联页面</span>{agent.pageIds.map((pageId) => <Tag key={pageId}>{pageId}</Tag>)}</div>
        </section>
        <AgentDevelopmentStatus agent={agent} disabled={disabled} onStart={onStartDevelopment} onOpen={onOpenExecution} />
        <AgentPromptEditor agent={agent} workspaceRoot={workspaceRoot} onSaved={onSaved} />
  </div>
}
