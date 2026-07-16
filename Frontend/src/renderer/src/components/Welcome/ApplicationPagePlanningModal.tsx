import {
  AppstoreOutlined,
  BankOutlined,
  CheckCircleOutlined,
  BulbOutlined,
  CloudOutlined,
  DashboardOutlined,
  DesktopOutlined,
  EditOutlined,
  FundOutlined,
  LeftOutlined,
  MessageOutlined,
  ReloadOutlined,
  SettingOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import type { AntdIconProps } from '@ant-design/icons/lib/components/AntdIcon'
import { Alert, Button, Collapse, Form, Input, message, Modal, Radio, Result, Select, Steps, Switch, Typography } from 'antd'
import type { ComponentType, ReactNode } from 'react'
import { useMemo, useState } from 'react'
import {
  confirmApplicationPagePlan,
  requestApplicationPagePlan
} from '../../service/applicationPagePlanning'
import { isAuthenticationFailure } from '../../service/authentication'
import type {
  ApplicationConfig,
  ApplicationPageContext,
  ApplicationPagePlan,
  ConfirmedPagePlan,
  PagePlanningAnswer,
  PagePlanningProgress,
  PagePlanningQuestion
} from '../../typings'
import { cx } from '../../utils'
import { applicationIconOptions, trackMethodOptions } from './constants'
import { formatError } from './utils'
import ApplicationPagePlanReview from './ApplicationPagePlanReview'
import ApplicationPlanningProgress, {
  appendPagePlanningProgress
} from './ApplicationPlanningProgress'
import './ApplicationPagePlanningModal.less'

const { Paragraph, Title } = Typography
const { Step } = Steps
const { TextArea } = Input

const iconComponents: Record<string, ComponentType<AntdIconProps>> = {
  AppstoreOutlined,
  DesktopOutlined,
  DashboardOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  UserOutlined,
  ToolOutlined,
  CloudOutlined,
  MessageOutlined,
  BankOutlined,
  FundOutlined
}

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
  questions: PagePlanningQuestion[]
  questionsError?: string
  questionsLoading: boolean
  questionsProgressEvents: PagePlanningProgress[]
  questionsStream?: string
  theme: 'dark' | 'light'
  threadId: string
  onCancel: () => void
  onConfirmed: (plan: ApplicationPagePlan, confirmation: ConfirmedPagePlan) => Promise<void>
  onRetryQuestions: () => void
  onSettingsSave: (values: SettingsValues) => Promise<void>
}

function toPageContext(application: ApplicationConfig): ApplicationPageContext {
  return {
    name: application.appName,
    scenario: application.senario,
    terminal: application.terminal
  }
}

// 渲染页面结构规划弹窗，并复用首页当前的明暗主题与紫色强调样式。
export default function ApplicationPagePlanningModal({
  application,
  questions,
  questionsError,
  questionsLoading,
  questionsProgressEvents,
  questionsStream,
  theme,
  threadId,
  onCancel,
  onConfirmed,
  onRetryQuestions,
  onSettingsSave
}: Props): JSX.Element {
  const [form] = Form.useForm<{ answers: Record<string, string> }>()
  const [settingsForm] = Form.useForm<SettingsValues>()
  const [plan, setPlan] = useState<ApplicationPagePlan>()
  const [generating, setGenerating] = useState(false)
  const [revisionFeedback, setRevisionFeedback] = useState('')
  const [revising, setRevising] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [planningProgressEvents, setPlanningProgressEvents] = useState<PagePlanningProgress[]>([])
  const [planningStream, setPlanningStream] = useState('')

  const settingsInitialValues = useMemo<SettingsValues>(
    () => ({
      appName: application.appName,
      appIcon: application.appIcon,
      layout: application.layout,
      auth: application.auth,
      track: application.track,
      apiTrack: application.apiTrack
    }),
    [application]
  )

  // 验证并持久化规划期间修改的应用基础设置。
  const handleSaveSettings = async (): Promise<void> => {
    setSavingSettings(true)
    try {
      const values = await settingsForm.validateFields()
      await onSettingsSave(values)
      message.success('设置已保存')
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      if (isAuthenticationFailure(error)) return
      message.error(formatError(error, '保存设置失败'))
    } finally {
      setSavingSettings(false)
    }
  }

  // 累积 AG-UI 进度事件，使界面可以展示完整阶段而非仅展示最后一条。
  const handlePlanningProgress = (progress: PagePlanningProgress): void => {
    setPlanningProgressEvents((history) => appendPagePlanningProgress(history, progress))
  }

  // 根据用户回答生成首版页面与 API 设计方案。
  const handleGeneratePlan = async (): Promise<void> => {
    try {
      const values = await form.validateFields()
      setGenerating(true)
      setPlanningProgressEvents([])
      setPlanningStream('')
      const answers: PagePlanningAnswer[] = questions.map((question) => ({
        questionId: question.id,
        question: question.question,
        answer: values.answers[question.id].trim()
      }))
      const nextPlan = await requestApplicationPagePlan(
        toPageContext(application),
        answers,
        threadId,
        undefined,
        handlePlanningProgress,
        setPlanningStream
      )
      setPlan(nextPlan)
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      if (isAuthenticationFailure(error)) return
      message.error(formatError(error, '生成页面结构失败'))
    } finally {
      setGenerating(false)
    }
  }

  // 用户明确确认后保存当前方案并进入应用。
  const handleConfirmPlan = async (): Promise<void> => {
    if (!application.workspaceRoot || !plan) return
    setConfirming(true)
    setPlanningProgressEvents([])
    setPlanningStream('')
    try {
      const confirmation = await confirmApplicationPagePlan(
        application.workspaceRoot,
        plan,
        threadId,
        handlePlanningProgress
      )
      await onConfirmed(plan, confirmation)
      message.success('页面与 API 设计已确认并写入 application.json')
    } catch (error) {
      if (isAuthenticationFailure(error)) return
      message.error(formatError(error, '保存 application.json 失败'))
    } finally {
      setConfirming(false)
    }
  }

  // 将用户的二次修改意见应用到完整设计方案后继续审核。
  const handleRevisePlan = async (): Promise<void> => {
    const feedback = revisionFeedback.trim()
    if (!plan || !feedback) {
      message.warning('请先填写你希望如何调整页面结构')
      return
    }
    setRevising(true)
    setPlanningProgressEvents([])
    setPlanningStream('')
    try {
      const nextPlan = await requestApplicationPagePlan(
        toPageContext(application),
        plan.clarifications,
        threadId,
        { currentPlan: plan, feedback },
        handlePlanningProgress,
        setPlanningStream
      )
      setPlan(nextPlan)
      setRevisionFeedback('')
      message.success('已根据你的意见更新页面结构')
    } catch (error) {
      if (isAuthenticationFailure(error)) return
      message.error(formatError(error, '调整页面结构失败'))
    } finally {
      setRevising(false)
    }
  }

  const backDisabled = generating || revising || confirming || savingSettings

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
          <Button
            className={cx('page-planning-title-back')}
            disabled={backDisabled}
            icon={<LeftOutlined />}
            onClick={onCancel}
            type="text"
          >
            返回
          </Button>
          <span className={cx('page-planning-title-divider')} />
          <span>规划「{application.appName}」的页面结构</span>
        </div>
      }
      width={980}
      wrapClassName={cx('welcome-modal', 'page-planning-modal', `theme-${theme}`)}
    >
      <Steps className={cx('page-planning-steps')} current={plan ? 1 : 0} size="small">
        <Step title="补充细节" />
        <Step title="审核页面结构" />
      </Steps>

      {questionsLoading ? (
        <div className={cx('page-planning-loading')}>
          <ApplicationPlanningProgress
            events={questionsProgressEvents}
            fallbackMessage="正在连接规划 Agent，准备分析应用场景…"
            streamingContent={questionsStream}
            title="正在准备规划问题"
          />
        </div>
      ) : questionsError ? (
        <Result
          status="error"
          title="暂时无法生成细节问题"
          subTitle={questionsError}
          extra={
            <Button icon={<ReloadOutlined />} onClick={onRetryQuestions} type="primary">
              重试
            </Button>
          }
        />
      ) : plan ? (
        <section className={cx('page-planning-review')}>
          <Alert
            message="请审核页面目录、功能、页面关系、交互流程和 API 设计。确认前不会生成代码；确认后才会写入 application.json 的 menus 和 apis。"
            showIcon
            type="info"
          />
          {revising || confirming ? (
            <ApplicationPlanningProgress
              events={planningProgressEvents}
              fallbackMessage={confirming ? '正在准备保存已确认方案…' : '正在准备重新分析设计方案…'}
              streamingContent={planningStream}
              title={confirming ? '正在确认并保存' : '正在根据意见调整方案'}
            />
          ) : null}
          <ApplicationPagePlanReview plan={plan} />
          <section className={cx('page-planning-feedback')}>
            <Title level={5}>还有调整意见？</Title>
            <Paragraph type="secondary">
              可以直接说明要新增、删除、合并或修改哪些页面。模型会基于当前草案调整，完成后仍由你继续审核。
            </Paragraph>
            <TextArea
              autoSize={{ minRows: 3, maxRows: 6 }}
              onChange={(event) => setRevisionFeedback(event.target.value)}
              placeholder="例如：不需要独立登录页；把通知中心合并进首页；增加客户详情页，并说明它与客户列表的关系。"
              value={revisionFeedback}
            />
            <Button
              disabled={confirming}
              icon={<EditOutlined />}
              loading={revising}
              onClick={handleRevisePlan}
            >
              根据意见调整页面结构
            </Button>
          </section>
          <div className={cx('page-planning-actions')}>
            <Button
              disabled={revising || confirming}
              icon={<LeftOutlined />}
              onClick={() => setPlan(undefined)}
            >
              返回修改回答
            </Button>
            <Button
              disabled={revising}
              icon={<CheckCircleOutlined />}
              loading={confirming}
              onClick={handleConfirmPlan}
              type="primary"
            >
              确认并更新 application.json
            </Button>
          </div>
        </section>
      ) : generating ? (
        <div className={cx('page-planning-loading')}>
          <ApplicationPlanningProgress
            events={planningProgressEvents}
            fallbackMessage="正在连接规划 Agent，准备生成页面与 API 方案…"
            streamingContent={planningStream}
            title="正在生成设计方案"
          />
        </div>
      ) : (
        <Form form={form} layout="vertical">
          <Paragraph type="secondary">
            为了让页面划分更贴近真实业务，请补充以下信息。回答只用于本次页面规划，不会启动现有 workflow。
          </Paragraph>
          {questions.map((question) => (
            <Form.Item
              extra={question.rationale}
              key={question.id}
              label={question.question}
              name={['answers', question.id]}
              rules={[{ required: true, whitespace: true, message: '请补充这个问题' }]}
            >
              <TextArea
                autoSize={{ minRows: 2, maxRows: 5 }}
                placeholder={question.placeholder}
              />
            </Form.Item>
          ))}
          <div className={cx('page-planning-actions')}>
            <Button
              icon={<BulbOutlined />}
              loading={generating}
              onClick={handleGeneratePlan}
              type="primary"
            >
              根据回答设计页面
            </Button>
          </div>
        </Form>
      )}

      <Collapse
        className={cx('page-planning-settings')}
        ghost
      >
        <Collapse.Panel
          header={
            <span className={cx('page-planning-settings-label')}>
              <SettingOutlined />
              设置
            </span>
          }
          key="settings"
        >
          <Form
            className={cx('settings-form')}
            form={settingsForm}
            initialValues={settingsInitialValues}
            layout="horizontal"
            labelCol={{ flex: '0 0 96px' }}
            wrapperCol={{ flex: '1 1 auto' }}
          >
              <div className={cx('settings-card')}>
                <div className={cx('settings-card-head')}>基础信息</div>
                <Form.Item label="应用名称" name="appName" rules={[{ required: true, message: '请输入应用名称' }]}>
                  <Input />
                </Form.Item>
                <Form.Item label="应用图标" name="appIcon">
                  <Select>
                    {applicationIconOptions.map((option) => {
                      const Icon = iconComponents[option.value]
                      return (
                        <Select.Option key={option.value} value={option.value}>
                          {Icon ? <Icon /> : null} {option.label}
                        </Select.Option>
                      )
                    })}
                  </Select>
                </Form.Item>
              </div>

              <div className={cx('settings-card')}>
                <div className={cx('settings-card-head')}>导航模式</div>
                <Form.Item label="布局方式" name={['layout', 'type']}>
                  <Radio.Group optionType="button" buttonStyle="solid">
                    <Radio.Button value="side">左侧导航</Radio.Button>
                    <Radio.Button value="top">顶部导航</Radio.Button>
                    <Radio.Button value="mix">混合导航</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                <Form.Item label="开启头部" name={['layout', 'useHeader']} valuePropName="checked">
                  <Switch checkedChildren="开" unCheckedChildren="关" />
                </Form.Item>
                <Form.Item label="开启底部" name={['layout', 'useFooter']} valuePropName="checked">
                  <Switch checkedChildren="开" unCheckedChildren="关" />
                </Form.Item>
              </div>

              <SettingsToggleSection
                title="认证"
                enableName={['auth', 'enable']}
                form={settingsForm}
              >
                <Form.Item label="认证来源" name={['auth', 'authnSource']}>
                  <Input />
                </Form.Item>
                <Form.Item label="clientId" name={['auth', 'yht', 'clientId']}>
                  <Input />
                </Form.Item>
              </SettingsToggleSection>

              <SettingsToggleSection
                title="页面埋点"
                enableName={['track', 'enable']}
                form={settingsForm}
              >
                <Form.Item label="上传标识" name={['track', 'uploadId']}>
                  <Input />
                </Form.Item>
                <Form.Item label="上报地址" name={['track', 'apiHost']}>
                  <Input />
                </Form.Item>
                <Form.Item label="请求方式" name={['track', 'method']}>
                  <Select>
                    {trackMethodOptions.map((option) => (
                      <Select.Option key={option.value} value={option.value}>
                        {option.label}
                      </Select.Option>
                    ))}
                  </Select>
                </Form.Item>
              </SettingsToggleSection>

              <SettingsToggleSection
                title="接口埋点"
                enableName={['apiTrack', 'enable']}
                form={settingsForm}
              >
                <Form.Item label="业务标识" name={['apiTrack', 'businessId']}>
                  <Input />
                </Form.Item>
                <Form.Item label="链路透传" name={['apiTrack', 'traceBaggage']}>
                  <Input />
                </Form.Item>
                <Form.Item label="埋点地址" name={['apiTrack', 'apiTrackHost']}>
                  <Input />
                </Form.Item>
              </SettingsToggleSection>

              <div className={cx('settings-save-bar')}>
                <Button
                  icon={<CheckCircleOutlined />}
                  loading={savingSettings}
                  onClick={handleSaveSettings}
                  type="primary"
                >
                  保存设置
                </Button>
              </div>
          </Form>
        </Collapse.Panel>
      </Collapse>
    </Modal>
  )
}

// 渲染可单独启停的应用设置分组，关闭时保留已填内容。
function SettingsToggleSection({
  title,
  enableName,
  form,
  children
}: {
  title: string
  enableName: string[]
  form: ReturnType<typeof Form.useForm<SettingsValues>>[0]
  children: ReactNode
}): JSX.Element {
  const enabled = Form.useWatch(enableName as never, form) ?? true
  return (
    <div className={cx('settings-card', !enabled && 'settings-card--disabled')}>
      <div className={cx('settings-card-head')}>
        {title}
        <Form.Item name={enableName} valuePropName="checked" noStyle>
          <Switch checkedChildren="启用" unCheckedChildren="关闭" size="small" />
        </Form.Item>
      </div>
      <div className={cx('settings-card-body')}>
        {children}
      </div>
    </div>
  )
}
