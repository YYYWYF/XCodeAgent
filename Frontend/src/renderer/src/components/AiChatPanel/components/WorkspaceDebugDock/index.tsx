import { BugOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { EditorMode, WorkflowDebugOptions, WorkflowRunPayload } from '../../../../typings'
import { cx } from '../../../../utils'
import ChatComposer from '../ChatComposer'
import type { ChatCopy } from '../../types'
import './WorkspaceDebugDock.less'

const { Text } = Typography

type Props = {
  activeWorkflow?: WorkflowRunPayload
  copy: ChatCopy[EditorMode]
  initialResumeFrom: string
  loading: boolean
  onSend: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  onStopGenerating: () => void
  rightContent?: ReactElement
  stopping: boolean
  workspaceBusy: boolean
  workspaceRoot: string
}

/** 忽略调试栏内部不会使用的技能选择变更，保持 ChatComposer 的通用接口不变。 */
const ignoreSelectedSkillsChange = (): void => undefined

/** 在工作台底部以紧凑横向布局呈现调试工具，并复用右侧原有控制内容。 */
export default function WorkspaceDebugDock({
  activeWorkflow,
  copy,
  initialResumeFrom,
  loading,
  onSend,
  onStopGenerating,
  rightContent,
  stopping,
  workspaceBusy,
  workspaceRoot
}: Props): ReactElement {
  return (
    <section
      aria-label="Workflow 调试工具与计划控制"
      className={cx('workspace-debug-dock', Boolean(rightContent) && 'has-right')}
    >
      <div className={cx('workspace-debug-panel', 'workspace-debug-controls')}>
        <div className={cx('workspace-debug-panel-header')}>
          <span className={cx('workspace-debug-panel-icon')} aria-hidden="true">
            <BugOutlined />
          </span>
          <div className={cx('workspace-debug-panel-heading')}>
            <Text strong>Workflow 调试</Text>
            <Text type="secondary">选择节点后直接执行或恢复</Text>
          </div>
        </div>
        <div className={cx('workspace-debug-composer')}>
          <ChatComposer
            activeWorkflow={activeWorkflow}
            copy={copy}
            debugOnly
            draft=""
            initialResumeFrom={initialResumeFrom}
            key={`workspace-debug-composer-${activeWorkflow?.runId || 'new'}-${initialResumeFrom}`}
            loading={loading}
            onDraftChange={() => undefined}
            onSelectedSkillsChange={ignoreSelectedSkillsChange}
            onSend={onSend}
            onStopGenerating={onStopGenerating}
            selectedSkills={[]}
            stopping={stopping}
            workspaceBusy={workspaceBusy}
            workspaceRoot={workspaceRoot}
          />
        </div>
      </div>

      {rightContent ? <div className={cx('workspace-debug-right')}>{rightContent}</div> : null}
    </section>
  )
}
