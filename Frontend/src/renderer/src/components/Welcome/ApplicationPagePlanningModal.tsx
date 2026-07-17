import { LeftOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons'
import { Button, Collapse, Form, Input, message, Modal, Result, Select, Steps, Switch } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  ApplicationConfig,
  ApplicationPlanningConfirmation,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../../typings'
import {
  buildApplicationPlanningRequest,
  createApplicationPlanningSession
} from '../../service/applicationPagePlanning'
import { isAuthenticationFailure } from '../../service/authentication'
import { cx } from '../../utils'
import { formatError } from './utils'
import ApplicationPlanningProgress, {
  type ApplicationPlanningProgressEvent
} from './ApplicationPlanningProgress'
import ApplicationPlanningQuestionPanel from './ApplicationPlanningQuestionPanel'
import './ApplicationPagePlanningModal.less'

const { Step } = Steps

export type SettingsValues = {
  appName: string
  appIcon: string
  layout: ApplicationConfig['layout']
  auth: ApplicationConfig['auth']
  track: ApplicationConfig['track']
  apiTrack: ApplicationConfig['apiTrack']
}

type Props = {
  application: ApplicationConfig
  theme: 'dark' | 'light'
  threadId: string
  onCancel: () => void
  onConfirmed: (confirmation: ApplicationPlanningConfirmation) => Promise<void>
  onSettingsSave: (values: SettingsValues) => Promise<void>
}

const phaseOrder = ['requirements', 'project_planning']

const phaseProgress: Record<string, { active: number; complete: number; message: string; title: string }> = {
  requirements: {
    active: 18,
    complete: 34,
    message: '正在分析需求并生成需求文档…',
    title: '正在确认产品需求'
  },
  project_planning: {
    active: 58,
    complete: 100,
    message: '正在生成项目级 ProjectPlan…',
    title: '正在规划项目结构'
  }
}

// 从 Workflow 公开状态中读取 specs/plans 产物校验结果。
function workflowConfirmation(workflow?: WorkflowRunPayload): ApplicationPlanningConfirmation | undefined {
  for (const source of [workflow?.result, workflow?.state]) {
    const value = source?.application_planning_confirmation
    if (value && typeof value === 'object') return value as ApplicationPlanningConfirmation
  }
  return undefined
}

// 根据当前阶段计算两步规划条的高亮位置。
function workflowStep(workflow?: WorkflowRunPayload): number {
  const phase = String(workflow?.summary.phase || '')
  const index = phaseOrder.indexOf(phase)
  return index >= 0 ? index : 0
}

// 将独立 Workflow 的当前节点转换为原页面规划进度组件需要的阶段时间线。
function workflowProgressEvents(workflow?: WorkflowRunPayload): ApplicationPlanningProgressEvent[] {
  if (!workflow) return []
  const currentIndex = workflowStep(workflow)
  const finished = workflow.summary.status === 'completed'
  return phaseOrder.slice(0, currentIndex + 1).map((stage, index) => {
    const meta = phaseProgress[stage]
    const completed = index < currentIndex || (finished && index === currentIndex)
    return {
      stage,
      percent: completed ? meta.complete : meta.active,
      message: completed ? `${meta.title.replace('正在', '')}已完成` : meta.message,
      detail: index === currentIndex && workflow.summary.message
        ? String(workflow.summary.message)
        : undefined
    }
  })
}

// 返回当前节点在动态进度卡上的标题与兜底说明。
function workflowProgressCopy(workflow?: WorkflowRunPayload): { fallback: string; title: string } {
  const stage = phaseOrder[workflowStep(workflow)]
  const meta = phaseProgress[stage] || phaseProgress.requirements
  return { fallback: meta.message, title: meta.title }
}

// 在创建应用弹窗中运行并可视化独立的两节点规划 Graph。
export default function ApplicationPagePlanningModal({
  application,
  theme,
  threadId,
  onCancel,
  onConfirmed,
  onSettingsSave
}: Props): JSX.Element {
  const [settingsForm] = Form.useForm<SettingsValues>()
  const session = useMemo(() => createApplicationPlanningSession(threadId), [threadId])
  const originalRequest = useMemo(() => buildApplicationPlanningRequest(application), [application])
  const startedRef = useRef(false)
  const completedRef = useRef(false)
  const [workflow, setWorkflow] = useState<WorkflowRunPayload>()
  const [running, setRunning] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [error, setError] = useState('')
  const progressCopy = workflowProgressCopy(workflow)

  const settingsInitialValues = useMemo<SettingsValues>(() => ({
    appName: application.appName,
    appIcon: application.appIcon,
    layout: application.layout,
    auth: application.auth,
    track: application.track,
    apiTrack: application.apiTrack
  }), [application])

  // 运行初始或恢复轮次，并在项目规划确认后直接打开工作台。
  const runPlanning = async (
    messageText: string,
    answers?: WorkflowClarificationAnswers,
    resumeState?: WorkflowRunPayload
  ): Promise<void> => {
    if (!application.workspaceRoot) return
    setRunning(true)
    setError('')
    setStreamingContent('')
    try {
      const result = await session.sendMessage(messageText, {
        application,
        clarificationAnswers: answers,
        editorMode: 'frontend',
        originalRequest,
        resumeState,
        workflowDebug: resumeState ? undefined : { enabled: true, resumeFrom: 'requirements' },
        workflowScope: 'application_planning',
        workspaceRoot: application.workspaceRoot,
        onContent: setStreamingContent,
        onWorkflow: setWorkflow
      })
      setWorkflow(result.workflow)
      const confirmation = workflowConfirmation(result.workflow)
      if (confirmation && !completedRef.current) {
        completedRef.current = true
        await onConfirmed(confirmation)
        message.success('需求与项目计划已确认，正在进入工作台')
      }
    } catch (reason) {
      if (isAuthenticationFailure(reason)) return
      setError(formatError(reason, '创建规划运行失败'))
    } finally {
      setRunning(false)
    }
  }

  // 弹窗首次挂载时从 requirements 节点启动独立规划 Graph。
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    void runPlanning(originalRequest)
  }, [originalRequest])

  // 提交当前确认卡答案，并由后端从公开状态推断恢复节点。
  const handleSubmitClarification = (
    currentWorkflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers
  ): void => {
    void runPlanning('请根据本轮确认继续创建规划。', answers, currentWorkflow)
  }

  // 验证并持久化规划期间修改的应用基础设置。
  const handleSaveSettings = async (): Promise<void> => {
    setSavingSettings(true)
    try {
      const values = await settingsForm.validateFields()
      await onSettingsSave(values)
      message.success('设置已保存')
    } catch (reason) {
      if (reason && typeof reason === 'object' && 'errorFields' in reason) return
      if (isAuthenticationFailure(reason)) return
      message.error(formatError(reason, '保存设置失败'))
    } finally {
      setSavingSettings(false)
    }
  }

  // 停止当前请求并返回创建表单。
  const handleCancel = (): void => {
    if (running) session.stop()
    onCancel()
  }

  return (
    <Modal
      closable={false}
      destroyOnClose
      footer={null}
      keyboard={false}
      maskClosable={false}
      open
      title={
        <div className={cx('page-planning-title')}>
          <Button className={cx('page-planning-title-back')} icon={<LeftOutlined />} onClick={handleCancel} type="text">
            返回
          </Button>
          <span className={cx('page-planning-title-divider')} />
          <span>规划「{application.appName}」</span>
        </div>
      }
      style={{ top: '5vh' }}
      width="90vw"
      wrapClassName={cx('welcome-modal', 'page-planning-modal', `theme-${theme}`)}
    >
      <Steps className={cx('page-planning-steps')} current={workflowStep(workflow)} size="small">
        <Step title="需求确认" description="需求文档" />
        <Step title="项目规划" description="ProjectPlan" />
      </Steps>

      {error ? (
        <Result
          extra={<Button icon={<ReloadOutlined />} onClick={() => void runPlanning(originalRequest)} type="primary">重试</Button>}
          status="error"
          subTitle={error}
          title="规划流程暂时中断"
        />
      ) : (
        <section className={cx('page-planning-review')}>
          {running || !workflow ? (
            <div className={cx('page-planning-loading')}>
              <ApplicationPlanningProgress
                events={workflowProgressEvents(workflow)}
                fallbackMessage={progressCopy.fallback}
                streamingContent={streamingContent}
                title={progressCopy.title}
              />
            </div>
          ) : null}
          {!running && workflow ? (
            <ApplicationPlanningQuestionPanel
              disabled={running}
              onSubmit={handleSubmitClarification}
              workflow={workflow}
            />
          ) : null}
        </section>
      )}

      <Collapse className={cx('page-planning-settings')} ghost>
        <Collapse.Panel header={<span className={cx('page-planning-settings-label')}><SettingOutlined />设置</span>} key="settings">
          <Form form={settingsForm} initialValues={settingsInitialValues} layout="vertical">
            <Form.Item label="应用名称" name="appName" rules={[{ required: true, message: '请输入应用名称' }]}>
              <Input />
            </Form.Item>
            <Form.Item label="导航布局" name={['layout', 'type']}>
              <Select options={[
                { label: '侧边导航', value: 'side' },
                { label: '顶部导航', value: 'top' },
                { label: '混合导航', value: 'mix' }
              ]} />
            </Form.Item>
            <Form.Item label="启用认证" name={['auth', 'enable']} valuePropName="checked"><Switch /></Form.Item>
            <Form.Item label="启用埋点" name={['track', 'enable']} valuePropName="checked"><Switch /></Form.Item>
            <Button loading={savingSettings} onClick={() => void handleSaveSettings()} type="primary">保存设置</Button>
          </Form>
        </Collapse.Panel>
      </Collapse>
    </Modal>
  )
}
