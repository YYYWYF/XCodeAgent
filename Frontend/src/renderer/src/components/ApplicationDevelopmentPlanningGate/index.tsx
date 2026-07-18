import { CheckCircleOutlined, LoadingOutlined, PlayCircleOutlined, ReloadOutlined, RocketOutlined } from '@ant-design/icons'
import { Button, Form, Input, Progress, Radio, Tag, Typography, message } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useProgressivePercent } from '../../hooks/useProgressivePercent'
import { confirmApplicationDevelopmentPlan, createDevelopmentPlanningThreadId, requestApplicationDevelopmentPlan } from '../../service/applicationDevelopmentPlanning'
import type { ApplicationDevelopmentPlan, ApplicationDevelopmentTask, ConfirmedDevelopmentPlan, DevelopmentPlanningAnswer, DevelopmentPlanningPageOption, DevelopmentPlanningProgress, DevelopmentPlanningQuestion } from '../../typings'
import { cx } from '../../utils'
import './ApplicationDevelopmentPlanningGate.less'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input

type Props = {
  applicationName: string
  pages: DevelopmentPlanningPageOption[]
  ready: boolean
  workspaceRoot: string
  onConfirmed: (confirmation: ConfirmedDevelopmentPlan) => Promise<void>
}

type Phase = 'intro' | 'planning' | 'questions' | 'review' | 'confirming' | 'error'

const STAGE_LABELS: Record<string, string> = {
  reading_application: '读取应用设计',
  identifying_shared_modules: '确认开发边界',
  planning_dependencies: '拆分任务与依赖',
  validating_plan: '校验开发顺序',
  persisting_plan: '写入应用配置'
}

const TASK_KIND_LABELS: Record<ApplicationDevelopmentTask['kind'], string> = {
  feature: '功能开发',
  integration: '页面联调',
  shared: '公共任务'
}

const TASK_STATUS_LABELS: Record<ApplicationDevelopmentTask['status'], string> = {
  todo: '待开发',
  in_progress: '进行中',
  completed: '已完成'
}

// 追加或更新同一 AG-UI 阶段，保留稳定的加载时间线。
function appendProgress(history: DevelopmentPlanningProgress[], next: DevelopmentPlanningProgress): DevelopmentPlanningProgress[] {
  const index = history.findIndex((item) => item.stage === next.stage)
  return index < 0 ? [...history, next] : history.map((item, itemIndex) => itemIndex === index ? next : item)
}

// 把未知异常转换为用户可读的错误信息。
function formatError(error: unknown): string {
  return error instanceof Error ? error.message : String(error || '生成开发计划失败')
}

// 为开发计划的每个真实阶段保留下一锚点，并让当前阶段有足够空间持续推进。
function developmentProgressCeiling(stage: string | undefined, target: number): number {
  if (target >= 100) return 100
  if (stage === 'reading_application') return 33.9
  if (stage === 'identifying_shared_modules') return 55.9
  if (stage === 'planning_dependencies') return 91.9
  if (stage === 'validating_plan') return 99.9
  if (stage === 'persisting_plan') return 99.9
  return 17.9
}

// 展示由 AG-UI 阶段锚点、模型活动和缓动共同驱动的开发计划进度。
function DevelopmentPlanningLoading({
  phase,
  progressEvents,
  streamingContent
}: {
  phase: 'planning' | 'confirming'
  progressEvents: DevelopmentPlanningProgress[]
  streamingContent: string
}): JSX.Element {
  const currentProgress = progressEvents[progressEvents.length - 1]
  const targetPercent = currentProgress?.percent ?? 6
  const percent = useProgressivePercent(
    targetPercent,
    developmentProgressCeiling(currentProgress?.stage, targetPercent),
    streamingContent.length
  )

  return (
    <section aria-live="polite" className={cx('development-planning-loading')}>
      <span className={cx('development-planning-spinner')}><LoadingOutlined spin /></span>
      <Text className={cx('development-planning-eyebrow')}>{phase === 'confirming' ? 'SAVING PLAN' : 'PLANNING WITH AG-UI'}</Text>
      <Title level={3}>{currentProgress?.message || (phase === 'confirming' ? '正在保存已确认计划…' : '正在连接规划模型…')}</Title>
      <Paragraph>{currentProgress?.detail || '正在准备页面功能和任务上下文。'}</Paragraph>
      <Progress
        format={() => `${percent.toFixed(1)}%`}
        percent={percent}
        status={percent >= 100 ? 'success' : 'active'}
        strokeColor={{ from: '#7c4dff', to: '#35d0ba' }}
      />
      <div className={cx('development-planning-timeline')}>
        {progressEvents.map((event, index) => (
          <div className={cx('development-planning-stage', index === progressEvents.length - 1 && 'is-active')} key={event.stage}>
            <span>{index === progressEvents.length - 1 ? <LoadingOutlined spin /> : <CheckCircleOutlined />}</span>
            <div><Text strong>{STAGE_LABELS[event.stage] || event.stage}</Text><Text>{event.message}</Text></div>
          </div>
        ))}
      </div>
      {streamingContent ? <pre className={cx('development-planning-stream')}>{streamingContent}</pre> : null}
    </section>
  )
}

// 用编号、状态、依赖和验收清单展示一个可独立更新状态的开发任务。
function DevelopmentTaskCard({ task, index }: { task: ApplicationDevelopmentTask; index: number }): JSX.Element {
  return (
    <article className={cx('development-planning-task')}>
      <header>
        <span className={cx('development-planning-task-index')}>{index + 1}</span>
        <div className={cx('development-planning-task-heading')}>
          <Text strong>{task.title}</Text>
          <div className={cx('development-planning-task-badges')}>
            <span className={cx('development-planning-task-status', `is-${task.status}`)}><i />{TASK_STATUS_LABELS[task.status]}</span>
            <Tag color={task.kind === 'integration' ? 'blue' : 'purple'}>{TASK_KIND_LABELS[task.kind]}</Tag>
          </div>
        </div>
      </header>
      <div className={cx('development-planning-task-scope')}>
        <Text className={cx('development-planning-task-label')}>实现范围</Text>
        <span>{task.description}</span>
      </div>
      {task.coversFeatures.length ? (
        <div className={cx('development-planning-task-features')}>
          <Text className={cx('development-planning-task-label')}>覆盖功能</Text>
          <div>{task.coversFeatures.map((feature) => <Tag key={feature}>{feature}</Tag>)}</div>
        </div>
      ) : null}
      <div className={cx('development-planning-task-relations')}>
        <span><Text className={cx('development-planning-task-label')}>前置任务</Text>{task.dependsOn.length ? task.dependsOn.join('、') : '无'}</span>
        <span><Text className={cx('development-planning-task-label')}>后续阻塞</Text>{task.blocks.length ? task.blocks.join('、') : '无'}</span>
      </div>
      <section className={cx('development-planning-acceptance')}>
        <header><Text strong>验收项</Text><Text type="secondary">{task.acceptanceCriteria.length} 项</Text></header>
        <ol>
          {task.acceptanceCriteria.map((criterion, criterionIndex) => (
            <li key={`${task.id}-${criterionIndex}`}><span>{criterionIndex + 1}</span><Text>{criterion}</Text></li>
          ))}
        </ol>
      </section>
    </article>
  )
}

// 在工作台功能启用前承载开发计划生成，并始终允许用户返回主页。
export default function ApplicationDevelopmentPlanningGate({ applicationName, pages, ready, workspaceRoot, onConfirmed }: Props): JSX.Element {
  const [form] = Form.useForm<{ answers: Record<string, string> }>()
  const [phase, setPhase] = useState<Phase>('intro')
  const [threadId] = useState(createDevelopmentPlanningThreadId)
  const [questions, setQuestions] = useState<DevelopmentPlanningQuestion[]>([])
  const [answers, setAnswers] = useState<DevelopmentPlanningAnswer[]>([])
  const [plan, setPlan] = useState<ApplicationDevelopmentPlan>()
  const [progressEvents, setProgressEvents] = useState<DevelopmentPlanningProgress[]>([])
  const [streamingContent, setStreamingContent] = useState('')
  const [error, setError] = useState('')
  const [failedAction, setFailedAction] = useState<'plan' | 'confirm'>('plan')
  const [selectedPageKey, setSelectedPageKey] = useState('')

  const taskCount = useMemo(() => plan ? plan.menuPlans.reduce((sum, item) => sum + item.tasks.length, 0) : 0, [plan])
  const selectedPage = useMemo(() => pages.find((page) => page.key === selectedPageKey), [pages, selectedPageKey])

  // 页面清单异步读取完成后默认选中第一个可规划页面。
  useEffect(() => {
    if (!pages.some((page) => page.key === selectedPageKey)) setSelectedPageKey(pages[0]?.key || '')
  }, [pages, selectedPageKey])

  // 调用模型生成计划；模型认为信息不足时切换为澄清表单。
  const generatePlan = async (nextAnswers: DevelopmentPlanningAnswer[] = answers): Promise<void> => {
    if (!selectedPageKey) {
      setError('当前 ProjectPlan 中没有可规划的页面。')
      setPhase('error')
      return
    }
    setPhase('planning')
    setProgressEvents([])
    setStreamingContent('')
    setError('')
    try {
      const result = await requestApplicationDevelopmentPlan(
        workspaceRoot,
        selectedPageKey,
        nextAnswers,
        threadId,
        (progress) => setProgressEvents((history) => appendProgress(history, progress)),
        setStreamingContent
      )
      if (result.questions?.length) {
        setQuestions(result.questions)
        setPhase('questions')
        return
      }
      setPlan(result.plan)
      setPhase('review')
    } catch (nextError) {
      setFailedAction('plan')
      setError(formatError(nextError))
      setPhase('error')
    }
  }

  // 校验用户回答并用补充信息再次请求开发计划。
  const submitAnswers = async (): Promise<void> => {
    try {
      const values = await form.validateFields()
      const nextAnswers = questions.map((question) => ({
        questionId: question.id,
        question: question.question,
        answer: values.answers[question.id].trim()
      }))
      setAnswers(nextAnswers)
      await generatePlan(nextAnswers)
    } catch (nextError) {
      if (nextError && typeof nextError === 'object' && 'errorFields' in nextError) return
      setFailedAction('plan')
      setError(formatError(nextError))
      setPhase('error')
    }
  }

  // 在显式确认后持久化计划，并通知工作台重新读取 application.json。
  const confirmPlan = async (): Promise<void> => {
    if (!plan) return
    setPhase('confirming')
    setProgressEvents([])
    setStreamingContent('')
    try {
      const confirmation = await confirmApplicationDevelopmentPlan(
        workspaceRoot,
        selectedPageKey,
        plan,
        threadId,
        (progress) => setProgressEvents((history) => appendProgress(history, progress))
      )
      await onConfirmed(confirmation)
      message.success('应用开发计划已写入 application.json')
    } catch (nextError) {
      setFailedAction('confirm')
      setError(formatError(nextError))
      setPhase('error')
    }
  }

  return (
    <div className={cx('development-planning-gate')}>
      <div aria-hidden className={cx('development-planning-aurora')} />
      <main className={cx('development-planning-panel')}>
        {!ready ? (
          <section aria-live="polite" className={cx('development-planning-loading')}>
            <span className={cx('development-planning-spinner')}><LoadingOutlined spin /></span>
            <Title level={3}>正在检查规划产物…</Title>
            <Paragraph>仅确认 specs 与 plans 目录中的两步规划结果。</Paragraph>
          </section>
        ) : phase === 'intro' ? (
          <section className={cx('development-planning-intro')}>
            <span className={cx('development-planning-logo')}><RocketOutlined /></span>
            <Text className={cx('development-planning-eyebrow')}>WORKBENCH READY</Text>
            <Title level={2}>想先从「{applicationName}」的哪个页面开始？</Title>
            <Paragraph>选择第一个要开发的页面。我会复用现有路由、API 调用、导航和布局能力，为该页面拆分任务、依赖与验收清单。</Paragraph>
            {pages.length ? (
              <Radio.Group className={cx('development-planning-page-options')} onChange={(event) => setSelectedPageKey(event.target.value)} value={selectedPageKey}>
                {pages.map((page) => (
                  <Radio.Button key={page.key} value={page.key}>
                    <span className={cx('development-planning-page-name')}>{page.label}</span>
                    <span className={cx('development-planning-page-path')}>{page.path}</span>
                    <span className={cx('development-planning-page-purpose')}>{page.purpose}</span>
                  </Radio.Button>
                ))}
              </Radio.Group>
            ) : <Text type="secondary">已确认的 ProjectPlan 中没有可规划页面。</Text>}
            <Button className={cx('development-planning-primary-action')} disabled={!selectedPageKey} icon={<PlayCircleOutlined />} onClick={() => void generatePlan([])} size="large" type="primary">为「{selectedPage?.label || '所选页面'}」生成开发计划</Button>
          </section>
        ) : null}

        {phase === 'planning' || phase === 'confirming' ? (
          <DevelopmentPlanningLoading
            phase={phase}
            progressEvents={progressEvents}
            streamingContent={streamingContent}
          />
        ) : null}

        {phase === 'questions' ? (
          <section className={cx('development-planning-questions')}>
            <Text className={cx('development-planning-eyebrow')}>NEED YOUR INPUT</Text>
            <Title level={3}>先确认几个会影响任务边界的问题</Title>
            <Paragraph>这些回答只用于本次计划生成，回答后仍需你审核最终计划。</Paragraph>
            <Form form={form} layout="vertical">
              {questions.map((question) => (
                <Form.Item extra={question.rationale} key={question.id} label={question.question} name={['answers', question.id]} rules={[{ required: true, whitespace: true, message: '请补充这个问题' }]}>
                  <TextArea autoSize={{ minRows: 2, maxRows: 5 }} placeholder={question.placeholder} />
                </Form.Item>
              ))}
              <Button icon={<PlayCircleOutlined />} onClick={() => void submitAnswers()} type="primary">根据回答继续生成</Button>
            </Form>
          </section>
        ) : null}

        {phase === 'review' && plan ? (
          <section className={cx('development-planning-review')}>
            <header><div><Text className={cx('development-planning-eyebrow')}>DEVELOPMENT ROADMAP</Text><Title level={3}>应用开发计划</Title><Paragraph>{plan.summary}</Paragraph></div><span className={cx('development-planning-count')}>{taskCount} 项任务</span></header>
            <div className={cx('development-planning-menu-list')}>
              {plan.menuPlans.map((menuPlan, menuIndex) => (
                <article className={cx('development-planning-menu')} key={menuPlan.menuKey}>
                  <header><span>{String(menuIndex + 1).padStart(2, '0')}</span><div><Text strong>{menuPlan.menuLabel}</Text><Text code>{menuPlan.menuKey}</Text></div><Text type="secondary">{menuPlan.tasks.length} 个任务</Text></header>
                  <div className={cx('development-planning-task-list')}>
                    {menuPlan.tasks.map((task, taskIndex) => <DevelopmentTaskCard index={taskIndex} key={task.id} task={task} />)}
                  </div>
                </article>
              ))}
            </div>
            <footer><Text type="secondary">确认后，编号任务、初始状态和验收项会写入 application.json 的每个 menus 项。</Text><Button className={cx('development-planning-primary-action')} icon={<CheckCircleOutlined />} onClick={() => void confirmPlan()} size="large" type="primary">确认并写入开发计划</Button></footer>
          </section>
        ) : null}

        {phase === 'error' ? (
          <section className={cx('development-planning-error')}><Title level={3}>计划生成暂时中断</Title><Paragraph>{error}</Paragraph><Button icon={<ReloadOutlined />} onClick={() => void (failedAction === 'confirm' ? confirmPlan() : generatePlan())} type="primary">重试</Button></section>
        ) : null}
      </main>
    </div>
  )
}
