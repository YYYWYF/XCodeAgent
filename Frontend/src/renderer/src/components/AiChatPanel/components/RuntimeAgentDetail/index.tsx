import { RobotOutlined } from '@ant-design/icons'
import { Tabs, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkbenchExecution } from '../../../../typings'
import type { RuntimeAgent, RuntimeSettingsResult } from '../../../../service/runtimeWorkbench'
import AgentPromptEditor from './AgentPromptEditor'
import AgentDevelopmentStatus from './AgentDevelopmentStatus'
import AgentDebugPanel from './AgentDebugPanel'
import './RuntimeAgentDetail.less'

/** 按设计与开发分栏展示 Agent；配置仍经正式修订确认，开发仍使用原任务流程。 */
export default function RuntimeAgentDetail({ agent, workspaceRoot, onSaved, onStartDevelopment, onOpenExecution, disabled }: {
  agent: RuntimeAgent; workspaceRoot: string; onSaved: (result: RuntimeSettingsResult) => void
  onStartDevelopment?: (agent: RuntimeAgent) => Promise<void>
  onOpenExecution?: (execution: WorkbenchExecution) => void
  disabled?: boolean
}): ReactElement {
  return <div className="runtime-agent-detail">
    <header className="agent-detail-hero">
      <span className="agent-detail-avatar"><RobotOutlined /></span>
      <div className="agent-detail-identity"><h2>{agent.name}</h2><code>{agent.agentId}</code></div>
    </header>
    <Tabs defaultActiveKey="design" items={[
      { key: 'design', label: '智能体设计', children: <>
        <section className="agent-design-overview">
          <div className="agent-detail-section-title"><h3>用途与范围</h3><span className="agent-design-source">来源：已确认的技术规划</span></div>
          <Typography.Paragraph>{agent.purpose || '尚未提供用途说明'}</Typography.Paragraph>
          <div className="agent-page-bindings"><span>关联页面</span>{agent.pageIds.length
            ? agent.pageIds.map((pageId) => <code key={pageId}>{pageId}</code>) : <span>未关联页面</span>}</div>
        </section>
        <AgentPromptEditor agent={agent} workspaceRoot={workspaceRoot} onSaved={onSaved} />
      </> },
      { key: 'development', label: '智能体开发', children:
        <AgentDevelopmentStatus agent={agent} disabled={disabled} onStart={onStartDevelopment} onOpen={onOpenExecution} /> },
      { key: 'debug', label: <span className="agent-debug-tab-label">智能体调试<Tag>开发中</Tag></span>,
        children: <AgentDebugPanel agent={agent} workspaceRoot={workspaceRoot} /> }
    ]} />
  </div>
}
