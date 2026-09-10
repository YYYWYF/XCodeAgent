import {
  CheckCircleOutlined,
  InfoCircleOutlined,
  PlayCircleOutlined,
  RobotOutlined
} from '@ant-design/icons'
import { Button, message, Modal, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type {
  ApplicationLifecycle,
  DevelopmentPlanningAgentOption,
  WorkbenchExecution
} from '../../../../typings'
import { cx } from '../../../../utils'
import { startAgentRuntimeDebug } from '../../../../service/agentRuntimeDebug'
import AgentDependenciesView from './AgentDependenciesView'
import AgentSettingsView from './AgentSettingsView'
import './AgentDevelopmentDetail.less'
import './AgentRuntimeDebugButton.less'

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
  const [runtimeDebugStatus, setRuntimeDebugStatus] = useState<
    'idle' | 'starting' | 'running' | 'failed'
  >('idle')

  /** 直接启动工作区 Runtime，供生成流程之外临时验证模板与模型配置。 */
  const handleStartRuntimeDebug = async (): Promise<void> => {
    if (!workspaceRoot || runtimeDebugStatus === 'starting') return
    setRuntimeDebugStatus('starting')
    try {
      const result = await startAgentRuntimeDebug(workspaceRoot)
      setRuntimeDebugStatus('running')
      void window.xcodeAgent?.projectPreview?.registerWorkspace({ workspaceRoot })
      message.success(result.message)
      Modal.success({
        title: 'Agent Runtime 已启动',
        content: (
          <div>
            <p>以下信息仅供本次临时调试，重新启动 Runtime 后会失效。</p>
            <p>
              Runtime 地址：
              <Typography.Text copyable>{result.runtimeUrl}</Typography.Text>
            </p>
            <p>
              Bearer Token：
              <Typography.Text copyable>{result.debugGatewayToken}</Typography.Text>
            </p>
          </div>
        )
      })
    } catch (error) {
      setRuntimeDebugStatus('failed')
      message.error(error instanceof Error ? error.message : 'Agent Runtime 启动失败。')
    }
  }

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
        <div className={cx('agent-detail-hero-actions')}>
          <Tooltip title="临时调试使用：只启动工作区 agent-runtime，不执行智能体代码生成。">
            <Button
              className={cx(
                'agent-runtime-debug-button',
                runtimeDebugStatus === 'running' && 'is-running'
              )}
              danger={runtimeDebugStatus === 'failed'}
              disabled={!workspaceRoot}
              icon={
                runtimeDebugStatus === 'running' ? (
                  <CheckCircleOutlined />
                ) : (
                  <PlayCircleOutlined />
                )
              }
              loading={runtimeDebugStatus === 'starting'}
              onClick={() => void handleStartRuntimeDebug()}
            >
              {runtimeDebugStatus === 'starting'
                ? '正在启动 Runtime'
                : runtimeDebugStatus === 'running'
                  ? '重新启动 Runtime'
                  : runtimeDebugStatus === 'failed'
                    ? '启动失败，重试'
                  : '启动 Runtime'}
            </Button>
          </Tooltip>
          <Button disabled={disabled} onClick={() => onStartDevelopment(agent)} type="primary">
            开始开发智能体
          </Button>
        </div>
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
