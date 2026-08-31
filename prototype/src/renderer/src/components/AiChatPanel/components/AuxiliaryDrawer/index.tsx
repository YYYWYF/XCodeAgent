import { CheckCircleOutlined, CloseOutlined, ReloadOutlined, SendOutlined } from '@ant-design/icons'
import { Button, Collapse, Input, Progress, Tag } from 'antd'
import type { CSSProperties, ReactElement } from 'react'
import { useState } from 'react'
import type { TestCasePreparationSnapshot } from '../../../../testCasePreparation'
import { testCasePreparationLabel } from '../../../../testCasePreparation'
import { cx } from '../../../../utils'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import './AuxiliaryDrawer.less'

const { Panel } = Collapse
const { TextArea } = Input

export type AuxiliaryDrawerMode = 'temporary-conversation' | 'test-preparation'

type Props = {
  mode: AuxiliaryDrawerMode
  onClose: () => void
  onRetryTestCases: () => void
  testPreparation: TestCasePreparationSnapshot
}

type TemporaryMessage = { id: number; role: 'assistant' | 'user'; content: string }

/** 渲染不具备正式写权限的临时问答空间。 */
function TemporaryConversation(): ReactElement {
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState<TemporaryMessage[]>([
    {
      id: 1,
      role: 'assistant',
      content: '嗨，你好呀！我是你的临时对话助手，页面、接口、代码上的疑问都可以直接问我，也能帮你分析问题、聊聊思路。'
    }
  ])

  /** 追加用户问题和基础演示回复，不创建任何 Workflow。 */
  const send = (): void => {
    const content = draft.trim()
    if (!content) return
    const now = Date.now()
    setMessages((current) => [
      ...current,
      { id: now, role: 'user', content },
      {
        id: now + 1,
        role: 'assistant',
        content: '我会基于当前工作区只读分析这个问题。若需要真正修改，请转到阶段主对话创建正式任务。'
      }
    ])
    setDraft('')
  }

  return (
    <>
      <div className={cx('temporary-conversation-messages')}>
        {messages.map((message) => (
          <div className={cx('temporary-message', message.role)} key={message.id}>
            {message.content}
          </div>
        ))}
      </div>
      <div className={cx('temporary-conversation-composer')}>
        <TextArea
          autoSize={{ minRows: 1, maxRows: 5 }}
          bordered={false}
          onChange={(event) => setDraft(event.target.value)}
          onPressEnter={(event) => {
            if (event.shiftKey) return
            event.preventDefault()
            send()
          }}
          placeholder="问问当前应用、产物或代码的问题…"
          value={draft}
        />
        <button
          type="button"
          aria-label="发送"
          className={cx('temporary-conversation-send')}
          disabled={!draft.trim()}
          onClick={send}
        >
          <SendOutlined />
        </button>
      </div>
    </>
  )
}

/** 渲染后台用例生成的分组进度和重试入口。 */
function TestPreparation({
  onRetry,
  snapshot
}: {
  onRetry: () => void
  snapshot: TestCasePreparationSnapshot
}): ReactElement {
  const percent = snapshot.total ? Math.round((snapshot.generated / snapshot.total) * 100) : 0
  return (
    <div className={cx('test-preparation-content')}>
      <div className={cx('test-preparation-summary')}>
        <div>
          <strong>{testCasePreparationLabel(snapshot)}</strong>
          <span>ProductPlan 确认后在后台按业务场景分批生成</span>
        </div>
        <Progress percent={percent} size="small" status={snapshot.status === 'failed' ? 'exception' : 'active'} />
      </div>
      <Collapse bordered={false} defaultActiveKey={snapshot.groups.find((group) => group.status === 'generating')?.id}>
        {snapshot.groups.map((group) => (
          <Panel
            header={`${group.label} · ${group.generated}/${group.total}`}
            key={group.id}
            extra={group.status === 'completed' ? <CheckCircleOutlined className={cx('completed')} /> : null}
          >
            <p>该分组将生成页面、接口组合路径、异常处理和数据校验用例。</p>
            <p>具体前置条件、步骤和预期结果将在用例生成后继续按条目折叠展示。</p>
          </Panel>
        ))}
      </Collapse>
      <div className={cx('test-preparation-actions')}>
        {snapshot.status === 'failed' || snapshot.status === 'stale' ? (
          <Button icon={<ReloadOutlined />} onClick={onRetry}>重新生成</Button>
        ) : null}
        {snapshot.status === 'ready' ? <Tag color="success">已满足测试准备条件</Tag> : null}
      </div>
    </div>
  )
}

/** 在同一辅助槽位中承载临时对话和测试准备，禁止抽屉叠加。 */
export default function AuxiliaryDrawer(props: Props): ReactElement {
  const isConversation = props.mode === 'temporary-conversation'
  return (
    <section
      className={cx('auxiliary-drawer')}
      aria-label={isConversation ? '临时对话' : '测试准备'}
    >
      <header>
        <span aria-hidden="true" className={cx('auxiliary-drawer-badge')}>
          <span
            aria-hidden="true"
            className={cx('auxiliary-drawer-badge-icon')}
            style={{ '--auxiliary-drawer-badge-source': `url("${freeChatIcon}")` } as CSSProperties}
          />
        </span>
        <div>
          <strong>{isConversation ? '临时对话' : '测试准备'}</strong>
          <small>{isConversation ? '辅助主对话解决即时疑问' : '后台异步生成业务测试用例'}</small>
        </div>
        <button aria-label="关闭辅助抽屉" onClick={props.onClose} type="button"><CloseOutlined /></button>
      </header>
      <div className={cx('auxiliary-drawer-body')}>
        {isConversation ? (
          <TemporaryConversation />
        ) : (
          <TestPreparation
            onRetry={props.onRetryTestCases}
            snapshot={props.testPreparation}
          />
        )}
      </div>
    </section>
  )
}
