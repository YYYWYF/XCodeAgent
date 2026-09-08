import { InfoCircleOutlined, RobotOutlined } from '@ant-design/icons'
import { Button, Tag, Tooltip } from 'antd'
import type { ReactElement } from 'react'
import type {
  ApplicationLifecycle,
  DevelopmentPlanningAgentOption,
  WorkbenchExecution
} from '../../../../typings'
import { cx } from '../../../../utils'
import AgentDependenciesView from './AgentDependenciesView'
import AgentSettingsView from './AgentSettingsView'
import './AgentDevelopmentDetail.less'

type Props = {
  agent: DevelopmentPlanningAgentOption
  applicationLifecycle?: ApplicationLifecycle
  disabled?: boolean
  onEndAgentExecution?: (execution: WorkbenchExecution) => Promise<boolean>
  onOpenAgentExecution?: (execution: WorkbenchExecution) => Promise<void>
  onSettingsApplied: () => void
  onStartDevelopment: (agent: DevelopmentPlanningAgentOption) => void
  workspaceRoot?: string
}

/** 组合 Agent 概览、七段 Settings、依赖状态和现有 Build 入口。 */
export default function AgentDevelopmentDetail({
  agent,
  applicationLifecycle,
  disabled,
  onEndAgentExecution,
  onOpenAgentExecution,
  onSettingsApplied,
  workspaceRoot,
  onStartDevelopment
}: Props): ReactElement {
  const summary = agent.taskSummary
  return (
    <div className={cx('agent-development-detail')}>
      <header className={cx('agent-detail-hero')}>
        <span className={cx('agent-detail-avatar')}>
          <RobotOutlined />
        </span>
        <div>
          <span className={cx('agent-detail-kicker')}>AGENT CONTRACT</span>
          <h2>{agent.label}</h2>
          <code>{agent.agentId}</code>
        </div>
        <Button
          disabled={disabled}
          onClick={() => onStartDevelopment(agent)}
          type="primary"
        >
          开始开发智能体
        </Button>
      </header>

      <section className={cx('agent-detail-section')}>
        <div className={cx('agent-detail-section-title')}>
          <div className={cx('agent-detail-section-heading-main')}>
            <h3>概览</h3>
            <Tooltip
              overlayClassName={cx('agent-contract-hash-tooltip')}
              title={
                <span>
                  <strong>Contract Hash</strong>
                  <code>{agent.contractHash}</code>
                </span>
              }
            >
              <button
                aria-label="查看 Contract Hash"
                className={cx('agent-detail-info-trigger')}
                type="button"
              >
                <InfoCircleOutlined />
              </button>
            </Tooltip>
          </div>
          <Tag color="purple">{agent.capabilities.length} 项能力</Tag>
        </div>
        <p className={cx('agent-purpose')}>{agent.purpose || '未声明用途'}</p>
        <div className={cx('agent-capability-list')}>
          {agent.capabilities.map((capability) => (
            <article key={capability.capabilityId}>
              <strong>{capability.name}</strong>
              <p>{capability.expectedResult}</p>
              <span>{capability.toolIds.length ? capability.toolIds.join(' · ') : '无需 Tool'}</span>
            </article>
          ))}
        </div>
        {agent.boundaries.length ? (
          <div className={cx('agent-boundaries')}>
            {agent.boundaries.map((boundary) => (
              <Tag key={boundary}>{boundary}</Tag>
            ))}
          </div>
        ) : null}
        {summary ? (
          <div className={cx('agent-task-summary')}>
            <span>任务 {summary.total}</span>
            <span>待执行 {summary.pending}</span>
            <span>进行中 {summary.running}</span>
            <span>已完成 {summary.completed}</span>
            <span>失败 {summary.failed}</span>
          </div>
        ) : null}
      </section>

      <AgentSettingsView
        agent={agent}
        applicationLifecycle={applicationLifecycle}
        onEndAgentExecution={onEndAgentExecution}
        onOpenAgentExecution={onOpenAgentExecution}
        onApplied={onSettingsApplied}
        workspaceRoot={workspaceRoot}
      />
      <AgentDependenciesView
        artifacts={agent.artifacts}
        dependencies={agent.dependencies}
        requiredChecks={agent.requiredChecks}
      />
    </div>
  )
}
