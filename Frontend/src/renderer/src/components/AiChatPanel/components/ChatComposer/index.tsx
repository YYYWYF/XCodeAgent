import { BugOutlined, FolderOpenOutlined, SendOutlined, StopOutlined } from '@ant-design/icons'
import { Alert, Button, Checkbox, Input, Select, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { EditorMode, WorkflowDebugOptions } from '../../../../typings'
import { cx } from '../../../../utils'
import type { ChatCopy } from '../../types'
import './ChatComposer.less'

const { Text } = Typography
const { TextArea } = Input
const { Option } = Select

const resumeNodeOptions = [
  { value: 'requirements', label: 'requirements' },
  { value: 'project_planning', label: 'project_planning' },
  { value: 'detail_confirmation', label: 'detail_confirmation' },
  { value: 'prepare_build_tasks', label: 'prepare_build_tasks' },
  { value: 'build', label: 'build' },
  { value: 'integration_test', label: 'integration_test' },
  { value: 'launch_project', label: 'launch_project' },
  { value: 'acceptance', label: 'acceptance' },
  { value: 'finalize_project', label: 'finalize_project' }
]

type ChatComposerProps = {
  copy: ChatCopy[EditorMode]
  draft: string
  error?: string
  loading: boolean
  onDraftChange: (value: string) => void
  onSend: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  onStopGenerating: () => void
  stopping: boolean
  workspaceBusy: boolean
  workspaceRoot: string
}

export default function ChatComposer({
  copy,
  draft,
  error,
  loading,
  onDraftChange,
  onSend,
  onStopGenerating,
  stopping,
  workspaceBusy,
  workspaceRoot
}: ChatComposerProps): ReactElement {
  const [debugEnabled, setDebugEnabled] = useState(false)
  const [resumeFrom, setResumeFrom] = useState('requirements')
  const [requirementSpecPath, setRequirementSpecPath] = useState('')
  const [projectPlanPath, setProjectPlanPath] = useState('')
  const [buildTaskPlanPath, setBuildTaskPlanPath] = useState('')
  const hasDebugNode = !debugEnabled || Boolean(resumeFrom)
  const canSend = debugEnabled ? hasDebugNode : Boolean(draft.trim())

  const currentDebugOptions = (): WorkflowDebugOptions | undefined =>
    debugEnabled
      ? {
          enabled: true,
          resumeFrom,
          requirementSpecPath: requirementSpecPath.trim() || undefined,
          projectPlanPath: projectPlanPath.trim() || undefined,
          buildTaskPlanPath: buildTaskPlanPath.trim() || undefined
        }
      : undefined

  const handleSend = (): void => {
    if (!hasDebugNode) return
    onSend(currentDebugOptions())
  }

  return (
    <div className={cx('ai-chat-composer')}>
      {error && <Alert message={error} showIcon type="error" />}
      <TextArea
        aria-label={`${copy.title}输出内容`}
        autoSize={{ minRows: 3, maxRows: 6 }}
        placeholder={copy.placeholder}
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onPressEnter={(event) => {
          if (!event.shiftKey) {
            event.preventDefault()
            handleSend()
          }
        }}
      />
      <div className={cx('workflow-debug-box', debugEnabled && 'enabled')}>
        <Checkbox
          checked={debugEnabled}
          disabled={loading}
          onChange={(event) => setDebugEnabled(event.target.checked)}
        >
          <BugOutlined /> Workflow 调试续跑
        </Checkbox>
        {debugEnabled && (
          <div className={cx('workflow-debug-fields')}>
            <Select
              className={cx('workflow-debug-node-select')}
              disabled={loading}
              placeholder="选择开始节点"
              value={resumeFrom}
              onChange={setResumeFrom}
            >
              {resumeNodeOptions.map((option) => (
                <Option key={option.value} value={option.value}>
                  {option.label}
                </Option>
              ))}
            </Select>
            <Input
              disabled={loading}
              placeholder="需求文档目录或 requirement-spec.json"
              value={requirementSpecPath}
              onChange={(event) => setRequirementSpecPath(event.target.value)}
            />
            <Input
              disabled={loading}
              placeholder="项目文档目录或 project-plan.json"
              value={projectPlanPath}
              onChange={(event) => setProjectPlanPath(event.target.value)}
            />
            <Input
              disabled={loading}
              placeholder="任务文档目录或 build-task-plan.json（从 build 开始时使用）"
              value={buildTaskPlanPath}
              onChange={(event) => setBuildTaskPlanPath(event.target.value)}
            />
          </div>
        )}
      </div>
      <div className={cx('ai-chat-composer-footer')}>
        <Text className={cx('workspace-root-label')} title={workspaceRoot}>
          <FolderOpenOutlined /> 工作目录：{workspaceRoot}
        </Text>
        {workspaceBusy && (
          <Text className={cx('workspace-busy-label')} type="warning">
            工作区中另一个会话正在执行
          </Text>
        )}
        {loading ? (
          <Button danger disabled={stopping} icon={<StopOutlined />} onClick={onStopGenerating}>
            {stopping ? '正在停止...' : '停止生成'}
          </Button>
        ) : (
          <Button
            disabled={!canSend || workspaceBusy}
            icon={<SendOutlined />}
            onClick={handleSend}
            type="primary"
          >
            {debugEnabled ? '从指定节点执行' : '发送给 Workflow'}
          </Button>
        )}
      </div>
    </div>
  )
}
