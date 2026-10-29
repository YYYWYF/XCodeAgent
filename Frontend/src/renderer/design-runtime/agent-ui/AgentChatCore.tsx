import React, { useState } from 'react'
import {
  Alert,
  Avatar,
  Button,
  Card,
  Empty,
  Input,
  Result,
  Skeleton,
  Space,
  Tag,
  Typography
} from 'antd5'
import {
  CheckCircleOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import type { AgentPreviewState, AgentUiTemplateConfig } from './types'

const { Paragraph, Text } = Typography

interface AgentChatCoreProps {
  config: AgentUiTemplateConfig
  state: AgentPreviewState
  onStateChange?: (state: AgentPreviewState) => void
}

/** 根据 Surface 决定是否在聊天核心节点上输出 UiManifest 部件证据。 */
function standalonePartProps(
  config: AgentUiTemplateConfig,
  part: 'messages' | 'status' | 'composer'
): Record<string, string> {
  if (config.surface !== 'standalone_page') return {}
  return {
    'data-agent-id': config.agentId,
    'data-agent-surface': config.surface,
    'data-agent-part': part,
    'data-control-id': `${config.agentId}-${part}`,
    ...(part === 'composer' ? { 'data-action-id': config.actionId } : {})
  }
}

/** 渲染加载、空、错误、停止等互斥的运行状态。 */
function AgentExclusiveState({
  config,
  state
}: Pick<AgentChatCoreProps, 'config' | 'state'>): React.ReactElement | null {
  if (state === 'loading') {
    return <Skeleton active aria-label="智能体正在加载" />
  }
  if (state === 'empty') {
    return <Empty description={`${config.name} 暂无会话，选择一个建议问题开始。`} />
  }
  if (state === 'error') {
    return (
      <Alert
        action={<Button data-preview-only="true">重试</Button>}
        description={config.mock.errorMessage}
        message="本次运行未完成"
        showIcon
        type="error"
      />
    )
  }
  if (state === 'stopped') {
    return (
      <Result status="warning" subTitle="已保留当前消息，你可以继续追问。" title="生成已停止" />
    )
  }
  return null
}

/** 渲染固定的左右消息气泡以及 Tool、审批和成功状态。 */
function AgentMessageStack({
  config,
  state
}: Pick<AgentChatCoreProps, 'config' | 'state'>): React.ReactElement {
  const showTool = config.features.tools && (state === 'normal' || state === 'tool')
  const showApproval = config.features.approvals && (state === 'normal' || state === 'approval')
  return (
    <div className="x-agent-chat__stack">
      <div className="x-agent-message x-agent-message--user">
        <Avatar icon={<UserOutlined />} />
        <div className="x-agent-message__body">
          <Text className="x-agent-message__author">你</Text>
          <div className="x-agent-message__bubble">
            <Paragraph>{config.mock.userMessage}</Paragraph>
          </div>
        </div>
      </div>
      <div className="x-agent-message">
        <Avatar icon={<RobotOutlined />} />
        <div className="x-agent-message__body">
          <Text className="x-agent-message__author">{config.name}</Text>
          <div className="x-agent-message__bubble">
            <Paragraph>{config.mock.assistantMessage}</Paragraph>
            {showTool ? (
              <Card className="x-agent-chat__tool" size="small">
                <Space wrap>
                  <ToolOutlined />
                  <Text strong>{config.mock.toolTitle}</Text>
                  <Tag color={state === 'tool' ? 'processing' : 'success'}>
                    {state === 'tool' ? '执行中' : '已完成'}
                  </Tag>
                </Space>
                <Paragraph type="secondary">{config.mock.toolDetail}</Paragraph>
              </Card>
            ) : null}
            {showApproval ? (
              <Card className="x-agent-chat__approval" size="small">
                <Text strong>{config.mock.approvalTitle}</Text>
                <Paragraph type="secondary">{config.mock.approvalDetail}</Paragraph>
                <Space>
                  <Button type="primary" data-preview-only="true">
                    批准
                  </Button>
                  <Button data-preview-only="true">拒绝</Button>
                </Space>
              </Card>
            ) : null}
            {state === 'success' ? (
              <Alert
                icon={<CheckCircleOutlined />}
                message={config.mock.successMessage}
                showIcon
                type="success"
              />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}

/** 渲染固定输入区，并只进行本地模拟状态切换。 */
function AgentComposer({ config, onStateChange }: AgentChatCoreProps): React.ReactElement {
  const [value, setValue] = useState('')
  return (
    <div className="x-agent-composer" {...standalonePartProps(config, 'composer')}>
      <Input.TextArea
        aria-label={`向${config.name}提问`}
        autoSize={{ minRows: 1, maxRows: 4 }}
        onChange={(event) => setValue(event.target.value)}
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        value={value}
      />
      <Space>
        <Button
          aria-label="停止生成"
          icon={<StopOutlined />}
          onClick={() => onStateChange?.('stopped')}
          data-preview-only="true"
        />
        <Button
          disabled={!value.trim()}
          icon={<SendOutlined />}
          onClick={() => onStateChange?.('running')}
          type="primary"
          data-preview-only="true"
        >
          发送
        </Button>
      </Space>
    </div>
  )
}

/** 统一渲染两种 Agent Surface 共用的消息、状态和 Composer。 */
export function AgentChatCore(props: AgentChatCoreProps): React.ReactElement {
  const { config, state } = props
  const hasExclusiveState = ['loading', 'empty', 'error', 'stopped'].includes(state)
  return (
    <section className="x-agent-chat" aria-label={`${config.name}会话`}>
      <div
        aria-busy={state === 'loading' || state === 'running'}
        aria-live="polite"
        className="x-agent-chat__messages"
        {...standalonePartProps(config, 'messages')}
      >
        {hasExclusiveState ? (
          <div className="x-agent-chat__state">
            <AgentExclusiveState config={config} state={state} />
          </div>
        ) : (
          <AgentMessageStack config={config} state={state} />
        )}
      </div>
      <div className="x-agent-chat__status" {...standalonePartProps(config, 'status')}>
        {state === 'running' ? '模拟运行中，可随时停止' : '模拟运行 · 上下文受页面白名单约束'}
      </div>
      <AgentComposer {...props} />
    </section>
  )
}
