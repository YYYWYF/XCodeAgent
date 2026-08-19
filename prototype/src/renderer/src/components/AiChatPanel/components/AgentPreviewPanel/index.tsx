import {
  CheckCircleOutlined,
  DeleteOutlined,
  RobotOutlined,
  SendOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import { Button, Input, Spin, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'
import {
  createAgentTrialTurn,
  type AgentTrialTurn,
  type DevelopmentPlanningAgent
} from '../../../../agentDevelopment'
import { agentConfigFingerprint, type AgentConfigState } from '../../../../agentConfig'
import { cx } from '../../../../utils'
import './AgentPreviewPanel.less'

const { Text } = Typography
const DEFAULT_TRIAL_PROMPT = '我的待审核回检单下一步应该怎么处理？'

type Props = {
  agent: DevelopmentPlanningAgent
  config: AgentConfigState
  hidden?: boolean
}

/** 渲染智能体构建后的受控试运行，不展示隐藏思维链。 */
export default function AgentPreviewPanel({ agent, config, hidden }: Props): ReactElement {
  const [draft, setDraft] = useState(DEFAULT_TRIAL_PROMPT)
  const [turns, setTurns] = useState<AgentTrialTurn[]>([])
  const [pendingPrompt, setPendingPrompt] = useState('')
  const [failedPrompt, setFailedPrompt] = useState('')
  const [running, setRunning] = useState(false)
  const timeoutRef = useRef<number>()
  const conversationRef = useRef<HTMLDivElement>(null)
  const configFingerprint = agentConfigFingerprint(config)

  /** 卸载面板时清理尚未完成的模拟运行，避免向已卸载组件写入状态。 */
  useEffect(
    () => () => {
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current)
    },
    []
  )

  /** 切换智能体时创建独立的空白试运行会话，避免串用其他智能体上下文。 */
  useEffect(() => {
    if (timeoutRef.current) window.clearTimeout(timeoutRef.current)
    setDraft(DEFAULT_TRIAL_PROMPT)
    setTurns([])
    setPendingPrompt('')
    setFailedPrompt('')
    setRunning(false)
  }, [agent.id, configFingerprint])

  /** 新消息或生成状态出现后滚动到对话底部。 */
  useEffect(() => {
    conversationRef.current?.scrollTo({
      top: conversationRef.current.scrollHeight,
      behavior: 'smooth'
    })
  }, [pendingPrompt, running, turns])

  /** 模拟一次智能体试运行，并区分首次失败与重试成功两种状态。 */
  const runTrial = (prompt: string, isRetry: boolean): void => {
    const nextSequence = turns.length + 1
    const shouldFail = !isRetry && /失败|权限|拒绝/.test(prompt)
    setPendingPrompt(prompt)
    setFailedPrompt('')
    setDraft('')
    setRunning(true)
    timeoutRef.current = window.setTimeout(() => {
      if (shouldFail) {
        setFailedPrompt(prompt)
        setPendingPrompt('')
        setRunning(false)
        return
      }
      setTurns((current) => [...current, createAgentTrialTurn(agent, prompt, nextSequence, config)])
      setPendingPrompt('')
      setRunning(false)
    }, 650)
  }

  /** 发送一条用户消息，并在模拟生成完成后向会话追加智能体回复。 */
  const handleRun = (): void => {
    const prompt = draft.trim()
    if (!prompt || running) return
    runTrial(prompt, false)
  }

  /** 复用失败消息重新发起试运行，验证失败后的恢复路径。 */
  const handleRetry = (): void => {
    if (!failedPrompt || running) return
    runTrial(failedPrompt, true)
  }

  /** 清空本次试运行历史，并恢复示例输入。 */
  const clearConversation = (): void => {
    if (running) return
    setTurns([])
    setFailedPrompt('')
    setDraft(DEFAULT_TRIAL_PROMPT)
  }

  return (
    <section className={cx('agent-preview')} aria-label={`${agent.label}试运行`} hidden={hidden}>
      <header className={cx('agent-preview-header')}>
        <span className={cx('agent-preview-avatar')} aria-hidden="true">
          <RobotOutlined />
        </span>
        <span>
          <Text strong>{agent.label}</Text>
          <Text type="secondary">{config.model.model} · 对话式试运行</Text>
        </span>
        <Tag color={running ? 'gold' : 'green'}>
          {running ? '正在生成回复' : turns.length > 0 ? `${turns.length} 轮对话` : '等待消息'}
        </Tag>
        {turns.length > 0 ? (
          <Button
            aria-label="清空试运行对话"
            disabled={running}
            icon={<DeleteOutlined />}
            onClick={clearConversation}
            size="small"
            type="text"
          />
        ) : null}
      </header>

      <div
        aria-busy={running}
        aria-live="polite"
        className={cx('agent-preview-conversation')}
        ref={conversationRef}
      >
        {turns.length === 0 && !running ? (
          <div className={cx('agent-preview-empty')}>
            <span className={cx('agent-preview-empty-icon')} aria-hidden="true">
              <RobotOutlined />
            </span>
            <Text strong>发送消息，开始试运行</Text>
            <Text type="secondary">回复、工具调用和证据会按对话顺序保留在这里。</Text>
          </div>
        ) : null}
        {turns.map((turn) => (
          <div className={cx('agent-preview-turn')} key={turn.sequence}>
            <div className={cx('agent-preview-message', 'user')}>
              <span className={cx('agent-preview-message-role')}>
                <UserOutlined aria-hidden="true" /> 你
              </span>
              <Text>{turn.userMessage}</Text>
            </div>
            <div className={cx('agent-preview-message', 'assistant')}>
              <span className={cx('agent-preview-message-role')}>
                <RobotOutlined aria-hidden="true" /> {agent.label}
              </span>
              <Text>{turn.assistantMessage}</Text>
              <details className={cx('agent-preview-evidence')}>
                <summary>
                  <ToolOutlined aria-hidden="true" /> 工具调用 1
                </summary>
                <span>{turn.toolName}</span>
                <code>{turn.endpoint}</code>
                <span>
                  <CheckCircleOutlined aria-hidden="true" /> {turn.evidence}
                </span>
              </details>
            </div>
          </div>
        ))}
        {running ? (
          <div className={cx('agent-preview-turn')} role="status">
            <div className={cx('agent-preview-message', 'user')}>
              <span className={cx('agent-preview-message-role')}>
                <UserOutlined aria-hidden="true" /> 你
              </span>
              <Text>{pendingPrompt}</Text>
            </div>
            <div className={cx('agent-preview-message', 'assistant', 'generating')}>
              <span className={cx('agent-preview-message-role')}>
                <RobotOutlined aria-hidden="true" /> {agent.label}
              </span>
              <span className={cx('agent-preview-generating')}>
                <Spin size="small" /> 正在生成回复并核验工具证据…
              </span>
            </div>
          </div>
        ) : null}
        {failedPrompt ? (
          <div className={cx('agent-preview-error')} role="alert">
            <Text>试运行失败：工具调用失败，未执行任何写操作。</Text>
            <Button aria-label="重试试运行" onClick={handleRetry} size="small" type="link">
              重试
            </Button>
          </div>
        ) : null}
      </div>

      <footer className={cx('agent-preview-composer')}>
        <Input.TextArea
          aria-label="试运行输入"
          autoSize={{ minRows: 2, maxRows: 4 }}
          disabled={running}
          onChange={(event) => setDraft(event.target.value)}
          onPressEnter={(event) => {
            if (!event.shiftKey) {
              event.preventDefault()
              handleRun()
            }
          }}
          value={draft}
          placeholder="输入消息，Enter 发送，Shift + Enter 换行"
        />
        <Button
          aria-label="发送试运行消息"
          disabled={running || !draft.trim()}
          icon={<SendOutlined />}
          loading={running}
          onClick={handleRun}
          type="primary"
        >
          {running ? '生成中' : '发送'}
        </Button>
      </footer>
    </section>
  )
}
