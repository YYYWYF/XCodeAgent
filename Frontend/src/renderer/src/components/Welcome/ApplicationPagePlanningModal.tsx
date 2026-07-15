import {
  CheckCircleOutlined,
  BulbOutlined,
  EditOutlined,
  LeftOutlined,
  ReloadOutlined
} from '@ant-design/icons'
import { Alert, Button, Form, Input, List, message, Modal, Result, Spin, Steps, Tag, Typography } from 'antd'
import { useState } from 'react'
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
  PagePlanningQuestion
} from '../../typings'
import { cx } from '../../utils'
import { formatError } from './utils'
import './ApplicationPagePlanningModal.less'

const { Paragraph, Text, Title } = Typography
const { Step } = Steps
const { TextArea } = Input

type Props = {
  application: ApplicationConfig
  questions: PagePlanningQuestion[]
  questionsError?: string
  questionsLoading: boolean
  theme: 'dark' | 'light'
  threadId: string
  onCancel: () => void
  onConfirmed: (plan: ApplicationPagePlan, confirmation: ConfirmedPagePlan) => Promise<void>
  onRetryQuestions: () => void
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
  theme,
  threadId,
  onCancel,
  onConfirmed,
  onRetryQuestions
}: Props): JSX.Element {
  const [form] = Form.useForm<{ answers: Record<string, string> }>()
  const [plan, setPlan] = useState<ApplicationPagePlan>()
  const [generating, setGenerating] = useState(false)
  const [revisionFeedback, setRevisionFeedback] = useState('')
  const [revising, setRevising] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const handleGeneratePlan = async (): Promise<void> => {
    setGenerating(true)
    try {
      const values = await form.validateFields()
      const answers: PagePlanningAnswer[] = questions.map((question) => ({
        questionId: question.id,
        question: question.question,
        answer: values.answers[question.id].trim()
      }))
      const nextPlan = await requestApplicationPagePlan(
        toPageContext(application),
        answers,
        threadId
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

  const handleConfirmPlan = async (): Promise<void> => {
    if (!application.workspaceRoot || !plan) return
    setConfirming(true)
    try {
      const confirmation = await confirmApplicationPagePlan(application.workspaceRoot, plan, threadId)
      await onConfirmed(plan, confirmation)
      message.success('页面结构已确认并写入 application.json')
    } catch (error) {
      if (isAuthenticationFailure(error)) return
      message.error(formatError(error, '保存 application.json 失败'))
    } finally {
      setConfirming(false)
    }
  }

  const handleRevisePlan = async (): Promise<void> => {
    const feedback = revisionFeedback.trim()
    if (!plan || !feedback) {
      message.warning('请先填写你希望如何调整页面结构')
      return
    }
    setRevising(true)
    try {
      const nextPlan = await requestApplicationPagePlan(
        toPageContext(application),
        plan.clarifications,
        threadId,
        { currentPlan: plan, feedback }
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

  const backDisabled = generating || revising || confirming

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
      width={820}
      wrapClassName={cx('welcome-modal', 'page-planning-modal', `theme-${theme}`)}
    >
      <Steps className={cx('page-planning-steps')} current={plan ? 1 : 0} size="small">
        <Step title="补充细节" />
        <Step title="审核页面结构" />
      </Steps>

      {questionsLoading ? (
        <div className={cx('page-planning-loading')}>
          <Spin tip="模型正在根据应用名称和场景整理细节问题…" />
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
            message="请确认这些页面能覆盖应用的核心使用流程。确认后将更新工作目录 application.json 中的菜单和页面规划。"
            showIcon
            type="info"
          />
          <List
            dataSource={plan.pages}
            renderItem={(page) => (
              <List.Item className={cx('page-planning-page')}>
                <div>
                  <Title level={5}>{page.name}</Title>
                  <Text code>{page.path}</Text>
                  <Paragraph>{page.purpose}</Paragraph>
                  <div>
                    {page.keyFeatures.map((feature) => (
                      <Tag key={feature}>{feature}</Tag>
                    ))}
                  </div>
                </div>
              </List.Item>
            )}
          />
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
    </Modal>
  )
}
