import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  HourglassOutlined,
  LoadingOutlined,
  MoonOutlined,
  PauseCircleOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Input,
  Progress,
  Radio,
  Tag,
  Tooltip,
  Typography
} from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import type {
  WorkflowBuildExecutionSlice,
  WorkflowBuildExecutionTask,
  WorkflowClarification,
  WorkflowClarificationQuestion,
  WorkflowClarificationSelectionGroup,
  WorkflowClarificationAnswer,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../../../../typings'
import type { UiDesignPage } from '../../../../initializationPlanning'
import { getAvailableTemplates } from '../../../../service/templateService'
import UiTemplateWireframe from '../UiTemplateWireframe'
import { cx } from '../../../../utils'
import type { WorkspaceDocKey } from '../../types'
import {
  pageAcceptanceContinuationMessage,
  backgroundDispatchContinuationMessage
} from '../../workflowContinuation'
import type { WorkflowInteractionAvailability } from '../../planExecutionMode'
import BackgroundDispatchCard, { type BackgroundDispatchOption } from '../BackgroundDispatchCard'
import {
  ApiSourceMissingCard,
  ApiSourceSelectCard,
  ApiSourceTypeCard,
  type ApiSourceDatabaseGroup,
  type ApiSourceExternalOption,
  type ApiSourceTypeOption
} from '../ApiSourceStepCards'
import { DetailReviewAuthBar } from './DetailReview'
import {
  taskId,
  numberValue,
  taskStatusColor,
  taskStatusText,
  dedupeStrings,
  taskDependencies,
  dedupeLocalizedTaskTexts,
  taskFailureCategoryText,
  displayTaskTitle,
  displayTaskDescription,
  sortBuildTasksForDisplay
} from './taskDisplay'
import './WorkflowRunCard.less'

const { Text } = Typography
const { TextArea } = Input

const OTHER_OPTION_VALUE = '__other__'

// 设计与计划阶段的确认卡 mode → 正式产物信息（驱动 ArtifactConfirmationCard 渲染）。
const ARTIFACT_CONFIRMATION_MAP: Record<
  string,
  { docKey: WorkspaceDocKey; title: string; summary: string }
> = {
  requirement_spec_confirmation: {
    docKey: 'requirement-spec',
    title: '需求文档',
    summary: '需求文档已生成，请确认内容。'
  },
  requirement_document_confirmation: {
    docKey: 'requirement-spec',
    title: '需求规格说明书',
    summary: '需求规格说明书已生成，请通过右侧表单审阅并确认。'
  },
  project_plan_confirmation: {
    docKey: 'project-plan',
    title: '项目计划',
    summary: '项目计划已生成，确认后生成构建任务清单。'
  },
  technical_plan_confirmation: {
    docKey: 'technical-plan',
    title: '技术规划方案',
    summary: '技术架构、应用API、数据来源意向与应用页面使用关系已生成，请确认。'
  }
}

export type ClarificationAnswers = WorkflowClarificationAnswers

/** 执行方式选择卡的固定选项：同步执行或进入某套任务系统；描述与任务抽屉头部同一套口径。 */
const BACKGROUND_DISPATCH_OPTIONS: BackgroundDispatchOption[] = [
  {
    key: 'sync',
    label: '同步任务',
    description: '常规算力，当场执行并实时展示生成过程，执行完成前需要等待。',
    icon: <ThunderboltOutlined />
  },
  {
    key: 'async',
    label: '异步任务',
    description: '常规算力队列，后台执行，消耗码豆。',
    icon: <HourglassOutlined />
  },
  {
    key: 'tide',
    label: '潮汐任务',
    description: '闲时算力队列，低优先级执行，不消耗码豆。',
    icon: <MoonOutlined />
  }
]

type WorkflowRunCardProps = {
  disabled?: boolean
  /** 是否作为流程节点的内嵌动作渲染，避免形成独立的对话卡片。 */
  embedded?: boolean
  interactionAvailability: WorkflowInteractionAvailability
  /** UI 设计确认卡逐页选模板用的实时应用页面清单（模板名随剧本重写同步刷新）。 */
  uiDesignPages?: UiDesignPage[]
  /** 用户是否已提交过一轮版式选择：区分首轮“待选择版式”与改稿轮“生成中显示已选模板”。 */
  uiDesignTemplatesSelected?: boolean
  onDiscard?: (docKey: WorkspaceDocKey) => void
  /** 字段映射面板控制：面板是否就绪、打开面板、以面板当前草稿完成确认。 */
  fieldMappingControl?: { ready: boolean; open: () => void; confirm: () => void }
  onSubmitClarification?: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<boolean>
  workflow: WorkflowRunPayload
}

export default function WorkflowRunCard({
  disabled,
  embedded = false,
  interactionAvailability,
  uiDesignPages,
  uiDesignTemplatesSelected = false,
  fieldMappingControl,
  onSubmitClarification,
  workflow
}: WorkflowRunCardProps): ReactElement | null {
  const status = String(workflow.summary.status || 'unknown')
  const clarification = workflowClarification(workflow)
  const cardCopy = workflowCardCopy(clarification?.mode, workflow.summary.phase)
  const clarificationQuestions = clarification?.questions || []
  const persistedAnswers =
    workflow.state?.clarificationAnswers &&
    typeof workflow.state.clarificationAnswers === 'object' &&
    !Array.isArray(workflow.state.clarificationAnswers)
      ? (workflow.state.clarificationAnswers as ClarificationAnswers)
      : {}
  const isQuestionCard = clarificationQuestions.length > 0
  const isTestCaseAuthorization = clarification?.mode === 'test_case_execute'
  const isArtifactAcceptance = clarification?.mode === 'page_acceptance'
  const isBackgroundDispatch = clarification?.mode === 'background_dispatch'
  // ui_confirmation 阶段的运行中快照不带 clarification（生成中），按阶段名兜底识别：
  // 静默批量生成期间卡片必须仍落在 UI 设计分支，否则整张卡被卸载再重挂，表现为闪烁。
  const isUiDesignConfirmation =
    clarification?.mode === 'ui_design_confirmation' ||
    workflow.summary?.phase === 'ui_confirmation'
  const isRequirementDocumentConfirmation =
    clarification?.mode === 'requirement_document_confirmation'
  const isTechnicalPlanConfirmation = clarification?.mode === 'technical_plan_confirmation'
  const detailReview = clarification?.mode === 'detail_review' ? clarification.review : undefined
  const artifactConfirmation = clarification?.mode
    ? ARTIFACT_CONFIRMATION_MAP[clarification.mode]
    : undefined
  const requiresConfirmation = clarification?.status === 'requires_user_input'
  // 已提交的确认卡保留只读回看：对齐原工程历史轮次行为，卡片留在自己的消息里禁用展示。
  const isSubmittedConfirmation = clarification?.status === 'submitted'
  // 交互统一禁用条件：外层禁用或该确认卡已不再处于可交互窗口（提交中/已失效）。
  const actionDisabled = disabled || interactionAvailability !== 'active'
  const [isSubmittingClarification, setIsSubmittingClarification] = useState(false)
  // 同一条测试用例的提交中状态应持续到运行快照替换；切换到下一条用例时再恢复新的授权入口。
  const testAuthorizationKey = [
    workflow.runId,
    workflow.state?.testWorkflowKey,
    workflow.result?.testWorkflowKey,
    workflow.summary.message
  ].join(':')
  useEffect(() => {
    if (isTestCaseAuthorization && requiresConfirmation) {
      setIsSubmittingClarification(false)
    }
  }, [isTestCaseAuthorization, requiresConfirmation, testAuthorizationKey])
  // 历史卡按原向导布局整体禁用回看：已提交，或已被更新的待确认卡取代时都不可再编辑，
  // 但保留“第 X / Y 项”与上一步/下一步的浏览切换，仅去掉提交动作。
  const readOnlyClarification =
    isQuestionCard && (!requiresConfirmation || interactionAvailability === 'stale')
  // 使用问题自身提供的默认答案初始化卡片，保留可直接调整的演示起点。
  const [answers, setAnswers] = useState<ClarificationAnswers>(() => {
    const initial: ClarificationAnswers = {}
    clarificationQuestions.forEach((question, index) => {
      const key = clarificationQuestionKey(question, index)
      const savedAnswer = persistedAnswers[key]
      const preset = (
        question as WorkflowClarificationQuestion & { presetAnswer?: WorkflowClarificationAnswer }
      ).presetAnswer
      if (savedAnswer !== undefined) initial[key] = savedAnswer
      else if (preset !== undefined) initial[key] = preset
    })
    return initial
  })
  const [clarificationStep, setClarificationStep] = useState(0)
  // 技术规划方案“重新生成”的展开式意见输入：首次点击只展开输入区，再点才提交；
  // 用户可表达真实修改意图，留空则退回默认文案，不增加默认状态的视觉负担。
  const [technicalPlanRevisionOpen, setTechnicalPlanRevisionOpen] = useState(false)
  const [technicalPlanFeedback, setTechnicalPlanFeedback] = useState('')
  // UI 设计确认卡的逐页选模板：展开哪一页的选择器、当前翻看的模板序号、已记录的版式选择。
  // 选模板只记录选择，不触发生成；全部应用页面选定后自动批量交后台重画，右侧一次性统一呈现。
  const uiTemplates = useMemo(() => getAvailableTemplates(), [])
  const [uiTemplatePickerPageId, setUiTemplatePickerPageId] = useState('')
  const [uiTemplateIndex, setUiTemplateIndex] = useState(0)
  const [uiTemplateSelections, setUiTemplateSelections] = useState<Record<string, string>>({})
  // 应用级验收的主交互位于右侧预览底部，避免在对话区重复渲染一张确认卡。
  if (clarification?.mode === 'application_acceptance') return null
  // 开发准入与设计完成后的计划准入统一由阶段门禁弹框承载：
  // 对话区只留一句 agent 文本收尾，弹框可关闭后从顶部阶段条「计划/开发阶段」再次唤起。
  if (clarification?.mode === 'development_entry_confirmation') return null
  if (clarification?.mode === 'planning_stage_entry') return null
  const canSubmitClarification =
    clarification?.status === 'requires_user_input' &&
    clarificationQuestions.length > 0 &&
    clarificationQuestions.every((question, index) =>
      clarificationAnswerComplete(question, answers[clarificationQuestionKey(question, index)])
    )
  // 分步向导：一次只展示一个待确认项，降低单屏信息量。
  const totalQuestions = clarificationQuestions.length
  const safeStep = totalQuestions > 0 ? Math.min(clarificationStep, totalQuestions - 1) : 0
  const currentQuestion = totalQuestions > 0 ? clarificationQuestions[safeStep] : undefined
  const currentAnswerKey = currentQuestion
    ? clarificationQuestionKey(currentQuestion, safeStep)
    : ''
  const currentRequired = currentQuestion ? currentQuestion.required !== false : true
  const currentComplete = currentQuestion
    ? clarificationAnswerComplete(currentQuestion, answers[currentAnswerKey])
    : false
  const updateAnswer = (key: string, value: WorkflowClarificationAnswer): void => {
    setAnswers((currentAnswers) => ({
      ...currentAnswers,
      [key]: value
    }))
  }

  /** 提交卡片动作；续跑 promise 结束后立即恢复动作入口，纯右侧状态切换（如修改需求）没有新快照可依赖。 */
  const submitClarification = (nextAnswers: ClarificationAnswers): void => {
    if (!onSubmitClarification || isSubmittingClarification) return
    setIsSubmittingClarification(true)
    void onSubmitClarification(workflow, nextAnswers)
      .catch(() => undefined)
      .finally(() => setIsSubmittingClarification(false))
  }

  // 需求确认属于主对话的连续工作流；右侧仅承载同一产物的阅读与原位编辑。
  if (isRequirementDocumentConfirmation && (requiresConfirmation || isSubmittedConfirmation)) {
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-ui-design',
          requiresConfirmation && 'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {/* 卡头统一用标准结构：信号点与标题同一行（workflow-run-name 主题色 15px），不再让圆点独占一行。 */}
        {!embedded && (
          <div className={cx('workflow-run-header')}>
            <div className={cx('workflow-run-title')}>
              <span className={cx('workflow-run-signal')} aria-hidden="true" />
              <div>
                <Text className={cx('workflow-run-name')} strong>
                  确认需求规格说明书
                </Text>
              </div>
            </div>
          </div>
        )}
        <Text className={cx('workflow-ui-design-copy')}>
          需求草稿已展示在右侧，确认后将转为正式需求文档并进入下一阶段。
        </Text>
        <div className={cx('workflow-ui-design-actions')}>
          <Button
            disabled={actionDisabled}
            onClick={() =>
              submitClarification({
                planning_action: { action: 'edit_requirements', artifactKey: 'requirement-spec' }
              })
            }
          >
            {/* 与右侧面板头部的“预览|编辑”开关共用同一措辞，两个入口指向同一本机动作。 */}
            编辑需求
          </Button>
          <Button
            disabled={actionDisabled}
            onClick={() =>
              submitClarification({
                planning_action: { action: 'confirm', artifactKey: 'requirement-spec' }
              })
            }
            type="primary"
          >
            确认并继续规划
          </Button>
        </div>
      </div>
    )
  }
  // 技术规划方案与需求规格同样由工作流授权；右侧只展示内容，不能绕过流程直接推进。
  if (isTechnicalPlanConfirmation && (requiresConfirmation || isSubmittedConfirmation)) {
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-ui-design',
          requiresConfirmation && 'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && (
          <div className={cx('workflow-run-header')}>
            <div className={cx('workflow-run-title')}>
              <span className={cx('workflow-run-signal')} aria-hidden="true" />
              <div>
                <Text className={cx('workflow-run-name')} strong>
                  确认技术规划方案
                </Text>
              </div>
            </div>
          </div>
        )}
        <Text className={cx('workflow-ui-design-copy')}>
          请在右侧审阅架构、应用API契约与页面技术绑定；确认后才会生成应用模板。
        </Text>
        {/* 重新生成展开为“意见输入 + 提交”两步：默认收起不加视觉负担，展开后意见选填。 */}
        {requiresConfirmation && technicalPlanRevisionOpen && (
          <TextArea
            autoSize={{ minRows: 2, maxRows: 4 }}
            disabled={actionDisabled}
            onChange={(event) => setTechnicalPlanFeedback(event.target.value)}
            placeholder="想调整什么？可填写修改意见（选填），留空则按当前审阅内容重新生成。"
            value={technicalPlanFeedback}
          />
        )}
        <div className={cx('workflow-ui-design-actions')}>
          {requiresConfirmation && technicalPlanRevisionOpen ? (
            <>
              <Button
                disabled={actionDisabled}
                onClick={() => {
                  setTechnicalPlanRevisionOpen(false)
                  setTechnicalPlanFeedback('')
                }}
              >
                取消
              </Button>
              <Button
                disabled={actionDisabled}
                onClick={() =>
                  submitClarification({
                    planning_action: {
                      action: 'revise',
                      artifactKey: 'technical-plan',
                      feedback:
                        technicalPlanFeedback.trim() || '请根据当前审阅内容重新生成技术规划方案。'
                    }
                  })
                }
                type="primary"
              >
                提交并重新生成
              </Button>
            </>
          ) : (
            <>
              <Button
                disabled={actionDisabled}
                onClick={() => setTechnicalPlanRevisionOpen(true)}
              >
                重新生成
              </Button>
              <Button
                disabled={actionDisabled}
                onClick={() =>
                  submitClarification({
                    planning_action: { action: 'confirm', artifactKey: 'technical-plan' }
                  })
                }
                type="primary"
              >
                确认技术规划方案
              </Button>
            </>
          )}
        </div>
      </div>
    )
  }
  // 其余历史产物确认仍由各自专属交互承载，避免重复渲染无效的通用卡片。
  if (artifactConfirmation) {
    return null
  }
  // 逐文件接受：确认同样由“文件改动”卡承担，这里不渲染待确认卡。
  if (clarification?.mode === 'file_acceptance' && requiresConfirmation) {
    return null
  }
  // 开发详细设计的产物授权统一由右侧 Diff 和输入框上方授权区块承载，
  // 对话区不再渲染“待确认事项 / 确认保存”卡片。
  if (detailReview) return null
  // 用例授权是一次性的启动门禁，不作为历史交互卡回放；否则新运行态接管前会闪现旧卡。
  if (isTestCaseAuthorization && (isSubmittingClarification || !requiresConfirmation)) return null

  // 执行方式选择是「选择执行方式」节点的动作：紧凑卡内嵌在节点轨迹中渲染，
  // 不再脱离流程单独成块；选项即提交，解释话术收进选项的注释图标。
  if (isBackgroundDispatch && requiresConfirmation) {
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            选择执行方式
          </Text>
        )}
        <BackgroundDispatchCard
          disabled={actionDisabled}
          options={BACKGROUND_DISPATCH_OPTIONS}
          answerKey={
            String(workflow.state?.dispatchTarget || '') === 'endpoint'
              ? 'background_dispatch_endpoint'
              : 'background_dispatch'
          }
          onSelect={(key, answerKey) => submitClarification({ [answerKey]: key })}
        />
        {interactionAvailability !== 'active' && (
          <Alert
            className={cx('workflow-dispatch-availability')}
            message={
              interactionAvailability === 'unavailable'
                ? '正在校准确认状态，请稍候。'
                : '该确认已提交或已失效，请在当前工作流继续操作。'
            }
            showIcon
            type="info"
          />
        )}
      </div>
    )
  }

  // 应用API数据来源类型选择：「选择数据来源类型」节点的动作，点击选项即提交。
  // 用什么类型的数据源是用户自己的判断，卡面不给建议。
  if (clarification?.mode === 'api_source_type' && requiresConfirmation) {
    const options: ApiSourceTypeOption[] = [
      {
        key: '数据库',
        label: '数据表',
        description: '绑定数据库表，按增删查改固定模板把契约出入参填进槽位，配置量小。'
      },
      {
        key: '外部服务',
        label: '外部API',
        description: '绑定外部API接口，做契约与接口之间的出入参参数适配，可加函数表达式。'
      }
    ]
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            选择数据来源类型
          </Text>
        )}
        <ApiSourceTypeCard
          objectName={String(clarification.objectName || '')}
          options={options}
          disabled={actionDisabled}
          submitting={isSubmittingClarification}
          onSelect={(kind) => submitClarification({ api_source_type: kind })}
        />
        {interactionAvailability !== 'active' && (
          <Alert
            className={cx('workflow-dispatch-availability')}
            message={
              interactionAvailability === 'unavailable'
                ? '正在校准确认状态，请稍候。'
                : '该确认已提交或已失效，请在当前工作流继续操作。'
            }
            showIcon
            type="info"
          />
        )}
      </div>
    )
  }

  // 应用API数据来源选择：「选择数据来源」节点的动作，单个表单项（表用级联、外部API用下拉）。
  if (clarification?.mode === 'api_source_select' && requiresConfirmation) {
    const databases: ApiSourceDatabaseGroup[] = Array.isArray(clarification.databases)
      ? (clarification.databases as ApiSourceDatabaseGroup[])
      : []
    const externals: ApiSourceExternalOption[] = Array.isArray(clarification.externals)
      ? (clarification.externals as ApiSourceExternalOption[])
      : []
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            选择数据来源
          </Text>
        )}
        <ApiSourceSelectCard
          kind={String(clarification.kind || '数据库') === '外部服务' ? '外部服务' : '数据库'}
          databases={databases}
          externals={externals}
          disabled={actionDisabled}
          submitting={isSubmittingClarification}
          onSelect={(key) => submitClarification({ api_source_select: key })}
        />
        {interactionAvailability !== 'active' && (
          <Alert
            className={cx('workflow-dispatch-availability')}
            message={
              interactionAvailability === 'unavailable'
                ? '正在校准确认状态，请稍候。'
                : '该确认已提交或已失效，请在当前工作流继续操作。'
            }
            showIcon
            type="info"
          />
        )}
      </div>
    )
  }

  // 应用API来源缺失引导：「选择数据来源」节点在目录为空时的引导动作。
  if (clarification?.mode === 'api_source_missing' && requiresConfirmation) {
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            选择数据来源
          </Text>
        )}
        <ApiSourceMissingCard
          kindLabel={String(clarification.kindLabel || '数据表')}
          disabled={actionDisabled}
          submitting={isSubmittingClarification}
          onRetry={() => submitClarification({ api_source_check: 'retry' })}
        />
        {interactionAvailability !== 'active' && (
          <Alert
            className={cx('workflow-dispatch-availability')}
            message={
              interactionAvailability === 'unavailable'
                ? '正在校准确认状态，请稍候。'
                : '该确认已提交或已失效，请在当前工作流继续操作。'
            }
            showIcon
            type="info"
          />
        )}
      </div>
    )
  }

  // 应用API映射绑定：绑定配置整体在右侧「字段映射」面板完成，节点卡只保留文本提示
  // 与最重要的确认动作——「打开字段映射」进入面板，「保存并确认」以面板草稿进入下一步。
  if (clarification?.mode === 'api_binding' && requiresConfirmation) {
    const objectName = String(clarification.objectName || '')
    const sourceSummary = [String(clarification.sourceName || ''), String(clarification.targetName || '')]
      .filter(Boolean)
      .join(' · ')
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            配置映射绑定
          </Text>
        )}
        <p className={cx('api-binding-step-hint')}>
          请在右侧「字段映射」面板完成「{objectName}」与 {sourceSummary || '数据来源'} 的映射绑定；
          保存并确认后将生成数据适配逻辑。
        </p>
        <div className={cx('api-binding-step-actions')}>
          <Button disabled={actionDisabled} onClick={() => fieldMappingControl?.open()}>
            打开字段映射
          </Button>
          <Button
            type="primary"
            disabled={actionDisabled || !fieldMappingControl?.ready}
            loading={isSubmittingClarification}
            onClick={() => fieldMappingControl?.confirm()}
          >
            保存并确认
          </Button>
        </div>
      </div>
    )
  }

  // UI 设计确认卡：模板选择是过程互动，按设计原则留在对话区完成（右侧只承载静态预览与终态标记）。
  // 选择→批量生成→确认→终态记录都演进在同一张卡上；生成中只把动作置为禁用并显示状态标签。
  if (isUiDesignConfirmation) {
    const uiDesignGenerating = workflow.summary.status === 'running'
    const uiPages = uiDesignPages || []
    const pickerOpen = uiTemplatePickerPageId && uiTemplates[uiTemplateIndex]
    const unconfirmedPages = uiPages.filter((page) => page.status !== 'confirmed')
    // “确认全部设计稿”必须等所有页面都完成版式选择并生成后才能点击。
    const uiPagesReady =
      uiPages.length > 0 &&
      uiPages.every((page) => page.status === 'generated' || page.status === 'confirmed')
    // 终态记录：全部应用页面已确认，或已跳过（页面清空且本卡已提交）。
    // 生成中的运行态不算终态——卡保持交互布局、仅按钮禁用，避免动作区闪没又闪回。
    const uiDesignRecord =
      !uiDesignGenerating &&
      (uiPages.length > 0
        ? uiPages.every((page) => page.status === 'confirmed')
        : Boolean(isSubmittedConfirmation))
    const uiDesignSkipped = uiDesignRecord && uiPages.length === 0
    /** 记录一页的版式选择：只写本地不触发生成；选满全部应用页面后一次性批量交后台重画（右侧统一刷新）。 */
    const applyUiTemplateSelection = (pageId: string, template: string): void => {
      const next = { ...uiTemplateSelections, [pageId]: template }
      setUiTemplateSelections(next)
      if (unconfirmedPages.length > 0 && unconfirmedPages.every((page) => next[page.pageId])) {
        setUiTemplateSelections({})
        submitClarification({
          planning_action: {
            action: 'select_template',
            artifactKey: 'ui-designs',
            pages: unconfirmedPages.map((page) => ({
              pageId: page.pageId,
              template: next[page.pageId]
            }))
          }
        })
      }
    }
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-ui-design',
          requiresConfirmation && 'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && (
          <div className={cx('workflow-run-header')}>
            <div className={cx('workflow-run-title')}>
              <span className={cx('workflow-run-signal')} aria-hidden="true" />
              <div>
                <Text className={cx('workflow-run-name')} strong>
                  确认 UI 设计稿
                </Text>
              </div>
            </div>
            {/* 生成中只变状态标签：卡片与右侧布局都保持稳定，不闪烁。 */}
            {uiDesignGenerating && <Tag color="purple">生成中</Tag>}
          </div>
        )}
        <Text className={cx('workflow-ui-design-copy')}>
          {uiDesignSkipped
            ? '已跳过 UI 设计稿，未生成页面设计。'
            : uiDesignRecord
              ? '全部应用页面设计稿已确认。'
              : '为每页选择版式模板；全部选定后统一生成，右侧一次性呈现所有设计稿。'}
        </Text>
        {uiPages.length > 0 && (
          <div className={cx('workflow-ui-template-list')}>
            {uiPages.map((page) => {
              const pickerActive = pickerOpen && uiTemplatePickerPageId === page.pageId
              // 已记录但尚未生成的选择用主题色标出；生成完成后以正式记录为准。
              const pendingTemplate = uiTemplateSelections[page.pageId]
              // 首轮页面尚未选定版式：不展示种子里自带的默认模板名，避免被误读为已选择；
              // 改稿轮（已提交过版式选择）的 queued 页面展示的正是本次所选模板。
              const chosenTemplate =
                pendingTemplate ||
                (page.status === 'queued' && !uiDesignTemplatesSelected ? '' : page.template)
              /** 打开本页选择器：翻看起点定位到该页当前版式，跨页翻看互不残留；再点一次收起。 */
              const togglePicker = (): void => {
                if (uiTemplatePickerPageId === page.pageId) {
                  setUiTemplatePickerPageId('')
                  return
                }
                const index = uiTemplates.findIndex(
                  (template) => template.manifest.name === (pendingTemplate || page.template)
                )
                setUiTemplateIndex(index >= 0 ? index : 0)
                setUiTemplatePickerPageId(page.pageId)
              }
              return (
                <div className={cx('workflow-ui-template-item')} key={page.pageId}>
                  <div className={cx('workflow-ui-template-row')}>
                    <Text className={cx('workflow-ui-template-name')} strong>
                      {page.name}
                    </Text>
                    <Text
                      className={cx('workflow-ui-template-current', pendingTemplate && 'pending')}
                      type={pendingTemplate ? undefined : 'secondary'}
                    >
                      {chosenTemplate || '待选择版式'}
                      {pendingTemplate ? '（待生成）' : ''}
                    </Text>
                    {page.status === 'confirmed' ? (
                      <Tag color="green">已确认</Tag>
                    ) : (
                      <Button
                        disabled={actionDisabled}
                        size="small"
                        onClick={togglePicker}
                      >
                        选模板
                      </Button>
                    )}
                  </div>
                  {pickerActive && (
                    <div className={cx('workflow-ui-template-picker')}>
                      <div className={cx('workflow-ui-template-picker-preview')}>
                        <UiTemplateWireframe
                          templateId={uiTemplates[uiTemplateIndex].manifest.id}
                        />
                      </div>
                      <div className={cx('workflow-ui-template-picker-copy')}>
                        <Text strong>{uiTemplates[uiTemplateIndex].manifest.name}</Text>
                        <Text type="secondary">
                          {uiTemplates[uiTemplateIndex].manifest.description}
                        </Text>
                      </div>
                      <div className={cx('workflow-ui-template-picker-nav')}>
                        <Button
                          disabled={uiTemplateIndex === 0}
                          size="small"
                          onClick={() => setUiTemplateIndex((index) => Math.max(0, index - 1))}
                        >
                          上一个
                        </Button>
                        <Text type="secondary" className={cx('workflow-ui-template-stepper')}>
                          第 {uiTemplateIndex + 1} / {uiTemplates.length} 个
                        </Text>
                        <Button
                          disabled={uiTemplateIndex === uiTemplates.length - 1}
                          size="small"
                          onClick={() =>
                            setUiTemplateIndex((index) =>
                              Math.min(uiTemplates.length - 1, index + 1)
                            )
                          }
                        >
                          下一个
                        </Button>
                        <span className={cx('workflow-ui-template-nav-spacer')} />
                        <Button
                          disabled={actionDisabled}
                          size="small"
                          type="primary"
                          onClick={() => {
                            applyUiTemplateSelection(
                              page.pageId,
                              uiTemplates[uiTemplateIndex].manifest.name
                            )
                            setUiTemplatePickerPageId('')
                          }}
                        >
                          使用此模板
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
        {/* 终态只留记录：逐页模板与已确认标记就是历史，不再渲染一组永远禁用的按钮。 */}
        {!uiDesignRecord && (
          <div className={cx('workflow-ui-design-actions')}>
            <Button
              disabled={actionDisabled}
              onClick={() => submitClarification({ ui_design_action: 'skip' })}
            >
              跳过 UI 设计
            </Button>
            <Button
              disabled={
                disabled ||
                interactionAvailability !== 'active' ||
                uiDesignGenerating ||
                !uiPagesReady
              }
              title={uiPagesReady ? undefined : '请先为每页选择版式模板并生成设计稿'}
              onClick={() => submitClarification({ ui_design_action: 'confirm' })}
              type="primary"
            >
              确认全部设计稿
            </Button>
          </div>
        )}
      </div>
    )
  }

  // 产物验收只负责启动动作；页面预览与接口调试统一放在右侧开发产物工作区，由验收工作流先行打开。
  if (isArtifactAcceptance && requiresConfirmation) {
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            产物验收
          </Text>
        )}
        <Text className={cx('workflow-test-case-authorization-copy')}>
          请在右侧审查确认实现内容，确认后接受产物。
        </Text>
        <Button
          className={cx('workflow-test-case-authorization-action')}
          type="primary"
          disabled={actionDisabled}
          onClick={() => submitClarification({ page_acceptance: 'accepted' })}
        >
          确认验收
        </Button>
      </div>
    )
  }

  // 用例授权只负责启动动作；用例详情、脚本和预期结果统一放在右侧用例面板。
  if (isTestCaseAuthorization && requiresConfirmation) {
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            授权执行用例
          </Text>
        )}
        <Text className={cx('workflow-test-case-authorization-copy')}>
          请在右侧查看用例内容，确认后开始执行。
        </Text>
        <Button
          className={cx('workflow-test-case-authorization-action')}
          type="primary"
          disabled={actionDisabled}
          onClick={() => submitClarification({ confirm_test_case: '是' })}
        >
          开始执行
        </Button>
      </div>
    )
  }

  return (
    <div
      className={cx(
        'workflow-run-card',
        isQuestionCard && 'workflow-run-card-question',
        requiresConfirmation && 'workflow-run-card-pending',
        readOnlyClarification && 'workflow-run-card-readonly',
        embedded && 'workflow-run-card-embedded'
      )}
    >
      {/* 内嵌卡直接使用流程节点标题，避免“节点名 + 卡片标题”重复占据纵向空间。 */}
      {!embedded && (
        <div className={cx('workflow-run-header')}>
          <div className={cx('workflow-run-title')}>
            <span className={cx('workflow-run-signal')} aria-hidden="true" />
            <div>
              {/* 标题始终用模式标题（如“细化需求”“修改需求文档”），提交前后保持同名——
                  待确认/已提交由控件可用态与只读提示表达，不用通用“事项”措辞掩盖业务语义。 */}
              <Text className={cx('workflow-run-name')} strong>
                {cardCopy.title}
              </Text>
            </div>
          </div>
          {isQuestionCard && !isTestCaseAuthorization ? (
            <Text className={cx('workflow-clarification-stepper-header')} type="secondary">
              第 {safeStep + 1} / {totalQuestions} 项
            </Text>
          ) : (
            <Tag className={cx('workflow-run-status')} color={workflowStatusColor(status)}>
              {workflowStatusText(status)}
            </Tag>
          )}
        </div>
      )}
      {workflow.summary.message && !embedded && (
        <div className={cx('workflow-run-message')}>
          <Text>{String(workflow.summary.message)}</Text>
        </div>
      )}
      {(clarificationQuestions.length > 0 || detailReview) && (
        <div className={cx('workflow-clarification')}>
          {requiresConfirmation && interactionAvailability !== 'active' && (
            <Alert
              message={
                interactionAvailability === 'unavailable'
                  ? '正在校准确认状态，请稍候。'
                  : '该确认已提交，以下为当时的填写记录。'
              }
              showIcon
              type="info"
            />
          )}
          {detailReview && interactionAvailability !== 'stale' ? (
            <DetailReviewAuthBar
              detailReview={detailReview}
              disabled={disabled}
              onConfirm={(submission) => submitClarification({ detail_review: submission })}
            />
          ) : (
            currentQuestion && (
              <div className={cx('workflow-clarification-body')}>
                <ClarificationContext clarification={clarification} />
                <div
                  className={cx('workflow-clarification-question')}
                  key={currentQuestion.id || safeStep}
                >
                  <div className={cx('workflow-clarification-title')}>
                    {/* 内嵌问答卡没有卡片头部，向导进度“第 X / Y 项”并入问题行首，
                        与问题同处一行，卡片保持紧凑的单行问题 + 单行操作布局。 */}
                    {embedded && isQuestionCard && (
                      <Text
                        className={cx('workflow-clarification-stepper-header')}
                        type="secondary"
                      >
                        第 {safeStep + 1} / {totalQuestions} 项
                      </Text>
                    )}
                    <Tag>{currentQuestion.header || currentQuestion.dimension || '需求'}</Tag>
                    <Text className={cx('workflow-clarification-question-text')}>
                      {currentQuestion.question || '请补充需求细节。'}
                    </Text>
                    <Text
                      className={cx(
                        'workflow-required-hint',
                        currentRequired ? 'required' : 'optional'
                      )}
                      type={currentRequired ? 'danger' : 'secondary'}
                    >
                      {currentRequired ? '必填' : '选填'}
                    </Text>
                  </div>
                  <ClarificationQuestionControl
                    disabled={disabled || readOnlyClarification}
                    onChange={(value) => updateAnswer(currentAnswerKey, value)}
                    question={currentQuestion}
                    value={answers[currentAnswerKey]}
                  />
                </div>
                <div className={cx('workflow-clarification-nav')}>
                  <Button
                    disabled={readOnlyClarification ? safeStep === 0 : disabled || safeStep === 0}
                    onClick={() => setClarificationStep((s) => Math.max(0, s - 1))}
                  >
                    上一步
                  </Button>
                  {safeStep >= totalQuestions - 1 ? (
                    readOnlyClarification ? null : (
                      <Button
                        type="primary"
                        disabled={disabled || !canSubmitClarification}
                        onClick={() => submitClarification(answers)}
                      >
                        {cardCopy.primaryAction}
                      </Button>
                    )
                  ) : (
                    <Button
                      type="primary"
                      disabled={
                        readOnlyClarification
                          ? false
                          : disabled || (currentRequired && !currentComplete)
                      }
                      onClick={() =>
                        setClarificationStep((s) => Math.min(totalQuestions - 1, s + 1))
                      }
                    >
                      下一步
                    </Button>
                  )}
                </div>
              </div>
            )
          )}
        </div>
      )}
    </div>
  )
}

type WorkflowCardCopy = {
  title: string
  primaryAction: string
}

/** 将结构化交互卡转换为当前业务动作的文案，避免把需求确认误称为工作流执行。 */
function workflowCardCopy(mode?: string, phase?: string): WorkflowCardCopy {
  switch (mode) {
    case 'requirement_clarification':
      return {
        title: '细化需求',
        primaryAction: '确认需求并生成文档'
      }
    case 'requirement_revision':
      return {
        title: '修改需求文档',
        primaryAction: '提交修改意见'
      }
    case 'requirement_spec_confirmation':
      return {
        title: '确认需求文档',
        primaryAction: '确认并生成项目计划'
      }
    case 'requirement_document_confirmation':
      return {
        title: '确认需求规格说明书',
        primaryAction: '确认并生成 UI 设计'
      }
    case 'project_plan_revision':
      return {
        title: '修改项目计划',
        primaryAction: '提交修改意见'
      }
    case 'project_plan_confirmation':
      return {
        title: '确认项目计划',
        primaryAction: '确认并进入开发'
      }
    case 'ui_design_confirmation':
      return {
        title: '确认 UI 设计稿',
        primaryAction: '确认设计稿'
      }
    case 'technical_plan_confirmation':
      return {
        title: '确认技术规划方案',
        primaryAction: '确认并生成模板'
      }
    case 'page_acceptance':
      return {
        title: '页面验收',
        primaryAction: '确认验收'
      }
    case 'test_case_execute':
      return {
        title: '授权执行用例',
        primaryAction: '开始执行'
      }
    default:
      // 没有明确交互类型时保留通用标题，兼容其它工作台过程卡片。
      return {
        title: phase === 'requirements' ? '需求细化' : '工作流执行',
        primaryAction: '确认并继续'
      }
  }
}

function BuildExecutionSliceProgress({
  executionSlice
}: {
  executionSlice: WorkflowBuildExecutionSlice
}): ReactElement | null {
  /** 展示当前页面或数据源范围的构建进度，不做应用级汇总。 */

  const [activeTaskKeys, setActiveTaskKeys] = useState<string[]>([])
  const scope = executionSlice.scope
  if (!scope) return null
  const tasks = Array.isArray(executionSlice.tasks) ? executionSlice.tasks : []
  const summary = executionSlice.summary || {}
  const total = numberValue(summary.total, tasks.length)
  const completed = numberValue(
    summary.completed,
    tasks.filter((task) => task.status === 'completed').length
  )
  const failed = numberValue(
    summary.failed,
    tasks.filter((task) => task.status === 'failed').length
  )
  const running = numberValue(
    summary.running,
    tasks.filter((task) => task.status === 'running').length
  )
  const pending = numberValue(
    summary.pending,
    tasks.filter((task) => !task.status || task.status === 'pending').length
  )
  const reused = numberValue(summary.reused, executionSlice.reusable_task_ids?.length || 0)
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0
  const targetLabel =
    scope.type === 'page'
      ? '应用页面'
      : scope.type === 'data_source'
        ? '数据源'
        : scope.type === 'endpoint'
          ? '接口'
          : '应用'
  const targetId = scope.targetId || executionSlice.target_unit_ids?.[0] || ''
  const progressStatus =
    failed > 0 ? 'exception' : completed === total && total > 0 ? 'success' : 'active'
  const displayTasks = sortBuildTasksForDisplay(tasks)
  const expandedTaskKeys = new Set(activeTaskKeys)

  return (
    <div className={cx('workflow-build-progress')}>
      <div className={cx('workflow-build-progress-header')}>
        <div>
          <Text strong>执行进度</Text>
          <Text type="secondary">
            {targetId ? `${targetLabel}：${targetId}` : `${targetLabel}执行范围`}
          </Text>
        </div>
        <Tag
          className={cx(
            'workflow-build-progress-count-tag',
            failed > 0 ? 'failed' : completed === total && total > 0 ? 'completed' : 'running'
          )}
          color={failed > 0 ? 'red' : completed === total && total > 0 ? 'green' : 'purple'}
        >
          {completed}/{total}
        </Tag>
      </div>
      <Progress
        percent={percent}
        showInfo={false}
        status={progressStatus}
        strokeColor={failed > 0 ? 'var(--wb-danger)' : 'var(--wb-accent)'}
        trailColor="var(--wb-surface-subtle)"
      />
      <Text className={cx('workflow-build-progress-percent')} type="secondary">
        {percent}% 完成
      </Text>
      <div className={cx('workflow-build-progress-stats')}>
        <BuildProgressStat
          icon={<PauseCircleOutlined />}
          label="待执行"
          tone="pending"
          value={pending}
        />
        <BuildProgressStat
          icon={<LoadingOutlined />}
          label="执行中"
          tone="running"
          value={running}
        />
        <BuildProgressStat
          icon={<CheckCircleOutlined />}
          label="已完成"
          tone="completed"
          value={completed}
        />
        <BuildProgressStat
          icon={<CloseCircleOutlined />}
          label="失败"
          tone="failed"
          value={failed}
        />
        <BuildProgressStat
          icon={<ClockCircleOutlined />}
          label="已复用"
          tone="reused"
          value={reused}
        />
      </div>
      {tasks.length > 0 && (
        <div className={cx('workflow-build-task-section')}>
          <Text strong>任务详情</Text>
          <Collapse
            activeKey={activeTaskKeys}
            className={cx('workflow-build-task-list')}
            expandIconPosition="right"
            onChange={(keys) => {
              const nextKeys = Array.isArray(keys) ? keys : [keys]
              setActiveTaskKeys(nextKeys.map(String))
            }}
          >
            {displayTasks.map((task) => (
              <Collapse.Panel
                className={cx('workflow-build-task-panel', task.status || 'pending')}
                header={
                  <BuildExecutionTaskHeader
                    expanded={expandedTaskKeys.has(taskId(task))}
                    task={task}
                  />
                }
                key={taskId(task)}
              >
                <BuildExecutionTaskDetails task={task} />
              </Collapse.Panel>
            ))}
          </Collapse>
        </div>
      )}
    </div>
  )
}

export function BuildExecutionRunCard({
  executionSlice,
  status
}: {
  executionSlice: WorkflowBuildExecutionSlice
  status: 'running' | 'completed' | 'failed' | 'requires_user_input'
}): ReactElement {
  /** 在对应构建步骤内部渲染独立的构建轮次卡片。 */

  return (
    <section className={cx('workflow-run-card', 'workflow-build-run-card', status)}>
      <div className={cx('workflow-run-header')}>
        <div className={cx('workflow-run-title')}>
          <span className={cx('workflow-run-signal')} aria-hidden="true" />
          <Text className={cx('workflow-run-name')} strong>
            构建执行
          </Text>
        </div>
        <Tag className={cx('workflow-run-status')} color={workflowStatusColor(status)}>
          {workflowStatusText(status)}
        </Tag>
      </div>
      <BuildExecutionSliceProgress executionSlice={executionSlice} />
    </section>
  )
}

function BuildProgressStat({
  icon,
  label,
  tone,
  value
}: {
  icon: ReactElement
  label: string
  tone: 'pending' | 'running' | 'completed' | 'failed' | 'reused'
  value: number
}): ReactElement {
  /** 渲染当前构建范围内的单项计数，避免上升到应用级统计。 */

  return (
    <span className={cx('workflow-build-progress-stat', tone)}>
      <span className={cx('workflow-build-progress-stat-icon')} aria-hidden="true">
        {icon}
      </span>
      <Text strong>{value}</Text>
      <Text type="secondary">{label}</Text>
    </span>
  )
}

function BuildExecutionTaskHeader({
  expanded,
  task
}: {
  expanded: boolean
  task: WorkflowBuildExecutionTask
}): ReactElement {
  /** 渲染可折叠任务卡片的头部摘要。 */

  const status = String(task.status || 'pending')
  const title = displayTaskTitle(task)
  const description = displayTaskDescription(task)
  return (
    <div className={cx('workflow-build-task-header-shell')}>
      <div className={cx('workflow-build-task-header', status)}>
        <span className={cx('workflow-build-task-status-icon')} aria-hidden="true">
          {taskStatusIcon(status)}
        </span>
        <div className={cx('workflow-build-task-title')}>
          <Text strong>{title}</Text>
          <Text type="secondary">{description}</Text>
        </div>
        <Tag
          className={cx('workflow-build-task-status-tag', status)}
          color={taskStatusColor(status)}
        >
          {taskStatusText(status)}
        </Tag>
      </div>
      {buildToolActivityPlacement(task, expanded) === 'header' && (
        <BuildToolActivity activity={task.activeToolActivity!} />
      )}
    </div>
  )
}

function BuildExecutionTaskDetails({ task }: { task: WorkflowBuildExecutionTask }): ReactElement {
  /** 展示单个构建任务的定位、失败原因、文件范围和验收点。 */

  const dependencies = taskDependencies(task)
  const paths = [
    ...stringList(task.targetFiles),
    ...stringList(task.target_files),
    ...stringList(task.allowed_paths),
    ...stringList(task.allowedPaths)
  ]
  const acceptance = dedupeLocalizedTaskTexts(
    [...stringList(task.acceptanceCriteria), ...stringList(task.acceptance_criteria)],
    task
  )
  const failureReason = taskFailureReason(task)
  const failureCategory = taskFailureCategoryText(task.failure_category)
  return (
    <div className={cx('workflow-build-task-details')}>
      <div className={cx('workflow-build-task-detail-grid')}>
        <BuildTaskDetailItem label="任务 ID" value={taskId(task)} />
        <BuildTaskDetailItem
          label="依赖"
          value={dependencies.length > 0 ? dependencies.join('、') : '无'}
        />
      </div>
      {task.status === 'failed' && (
        <div className={cx('workflow-build-task-detail-block', 'workflow-build-task-failure')}>
          <Text type="secondary">失败原因</Text>
          <Text>{failureReason || '任务执行失败，但后端未返回具体原因。'}</Text>
          {failureCategory && <Tag color="red">{failureCategory}</Tag>}
        </div>
      )}
      {paths.length > 0 && (
        <div className={cx('workflow-build-task-detail-block')}>
          <Text type="secondary">文件范围</Text>
          <div className={cx('workflow-build-task-tags')}>
            {dedupeStrings(paths).map((path) => (
              <Tag key={path}>{path}</Tag>
            ))}
          </div>
        </div>
      )}
      {acceptance.length > 0 && (
        <div className={cx('workflow-build-task-detail-block')}>
          <Text type="secondary">验收点</Text>
          <ul className={cx('workflow-build-task-detail-list')}>
            {acceptance.map((item) => (
              <li key={item}>
                <Text>{item}</Text>
              </li>
            ))}
          </ul>
        </div>
      )}
      {buildToolActivityPlacement(task, true) === 'details' && (
        <BuildToolActivity activity={task.activeToolActivity!} />
      )}
    </div>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function buildToolActivityPlacement(
  task: WorkflowBuildExecutionTask,
  expanded: boolean
): 'header' | 'details' | undefined {
  /** 决定实时工具活动的唯一渲染位置，任务终态或无活动时不展示。 */

  if (task.status !== 'running' || !task.activeToolActivity) return undefined
  return expanded ? 'details' : 'header'
}

function BuildToolActivity({
  activity
}: {
  activity: NonNullable<WorkflowBuildExecutionTask['activeToolActivity']>
}): ReactElement {
  /** 以单行高亮样式展示当前任务最新工具操作，不展开原始工具参数。 */

  return (
    <div
      aria-label={activity.message}
      aria-live="polite"
      className={cx('workflow-build-tool-activity', activity.status)}
      title={activity.message}
    >
      <span className={cx('workflow-build-tool-activity-icon')} aria-hidden="true">
        {activity.status === 'running' ? <LoadingOutlined spin /> : <CloseCircleOutlined />}
      </span>
      <Text>{activity.message}</Text>
    </div>
  )
}

function BuildTaskDetailItem({ label, value }: { label: string; value: string }): ReactElement {
  /** 渲染任务详情中的单个键值项。 */

  return (
    <div className={cx('workflow-build-task-detail-item')}>
      <Text type="secondary">{label}</Text>
      <Text>{value}</Text>
    </div>
  )
}

function taskFailureReason(task: WorkflowBuildExecutionTask): string {
  /** 提取失败原因，兼容后端后续扩展的 failure_detail 文本字段。 */

  if (typeof task.failure_reason === 'string' && task.failure_reason.trim()) {
    return task.failure_reason.trim()
  }
  const detail = objectValue(task.failure_detail)
  for (const key of ['reason', 'message', 'agent_note']) {
    const value = detail[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function taskStatusIcon(status: string): ReactElement {
  /** 将任务状态映射为卡片头部图标。 */

  if (status === 'completed') return <CheckCircleOutlined />
  if (status === 'failed') return <CloseCircleOutlined />
  if (status === 'running') return <LoadingOutlined />
  return <PauseCircleOutlined />
}

function ClarificationContext({
  clarification
}: {
  clarification?: WorkflowClarification
}): ReactElement | null {
  const groups = (clarification?.selection_groups || []).filter(
    (group) => Array.isArray(group.items) && group.items.length > 0
  )
  const context = clarification?.context
  if (groups.length === 0 && !context) return null

  return (
    <div className={cx('workflow-clarification-context')}>
      {groups.map((group, index) => (
        <SelectionGroup group={group} key={`${group.type || group.title}-${index}`} />
      ))}
      {context && <WorkflowContext context={context} />}
    </div>
  )
}

function SelectionGroup({ group }: { group: WorkflowClarificationSelectionGroup }): ReactElement {
  return (
    <div className={cx('workflow-selection-group')}>
      <Text strong>{group.title || group.type || '候选项'}</Text>
      <ul className={cx('workflow-selection-list')}>
        {(group.items || []).map((item) => (
          <li className={cx('workflow-selection-item')} key={item.id || item.label}>
            <Text>{item.label || item.name || item.id}</Text>
            <ul className={cx('workflow-selection-item-meta')}>
              {item.id && (
                <li>
                  <Text type="secondary">id: </Text>
                  <Text code>{item.id}</Text>
                </li>
              )}
              {item.description && (
                <li>
                  <Text type="secondary">{item.description}</Text>
                </li>
              )}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}

function WorkflowContext({ context }: { context: Record<string, unknown> }): ReactElement {
  const page = objectValue(context.page)
  const layout = objectValue(context.layout)
  const interactions = stringList(context.interactions)
  const dataSources = objectList(context.data_sources)
  const permissions = stringList(context.permissions)

  return (
    <div className={cx('workflow-page-context')}>
      {Object.keys(page).length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text strong>{stringValue(page.name) || '应用页面'}</Text>
          <Text type="secondary">
            {stringValue(page.path)}
            {stringValue(page.goal) ? `：${stringValue(page.goal)}` : ''}
          </Text>
        </div>
      )}
      {stringList(layout.structure).length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">布局</Text>
          <Text>{stringList(layout.structure).join('、')}</Text>
        </div>
      )}
      {interactions.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">交互</Text>
          <Text>{interactions.join('、')}</Text>
        </div>
      )}
      {dataSources.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">数据源</Text>
          <Text>
            {dataSources
              .map((source) => stringValue(source.name) || stringValue(source.id))
              .filter(Boolean)
              .join('、')}
          </Text>
        </div>
      )}
      {permissions.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">权限</Text>
          <Text>{permissions.join('、')}</Text>
        </div>
      )}
    </div>
  )
}

function ClarificationQuestionControl({
  disabled,
  onChange,
  question,
  value
}: {
  disabled?: boolean
  onChange: (value: WorkflowClarificationAnswer) => void
  question: WorkflowClarificationQuestion
  value?: WorkflowClarificationAnswer
}): ReactElement {
  const options = (question.options || [])
    .filter((option) => option.label)
    .map((option) => ({
      label: option.label || '',
      value: option.value || option.label || '',
      description: option.description || ''
    }))
  const optionsWithOther =
    question.allowOther !== false && !options.some((option) => option.value === OTHER_OPTION_VALUE)
      ? [...options, { label: '其他', value: OTHER_OPTION_VALUE, description: '' }]
      : options
  const selectedValues = selectedAnswerValues(value)
  const otherSelected = selectedValues.includes(OTHER_OPTION_VALUE)
  const otherValue = answerOtherText(value)
  const setSelectedValues = (selected: string[]): void => {
    onChange({ selected, other: otherValue || undefined })
  }
  const setOtherValue = (other: string): void => {
    onChange({ selected: selectedValues, other })
  }

  if (question.type === 'yesno') {
    return (
      <>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => setSelectedValues([String(event.target.value)])}
          value={selectedValues[0]}
        >
          <Radio value="是">是</Radio>
          <Radio value="否">否</Radio>
          {question.allowOther !== false && <Radio value={OTHER_OPTION_VALUE}>其他</Radio>}
        </Radio.Group>
        {otherSelected && (
          <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
        )}
      </>
    )
  }

  if (question.type === 'choice' && optionsWithOther.length > 0) {
    if (question.multiSelect) {
      return (
        <>
          <Checkbox.Group
            disabled={disabled}
            onChange={(checkedValues) => setSelectedValues(checkedValues.map(String))}
            value={selectedValues}
          >
            {optionsWithOther.map((option) => (
              <Checkbox
                className={cx('workflow-clarification-option')}
                key={option.value}
                value={option.value}
              >
                {renderOptionLabel(option)}
              </Checkbox>
            ))}
          </Checkbox.Group>
          {otherSelected && (
            <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
          )}
        </>
      )
    }

    return (
      <>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => setSelectedValues([String(event.target.value)])}
          value={selectedValues[0]}
        >
          {optionsWithOther.map((option) => (
            <Radio
              className={cx('workflow-clarification-option')}
              key={option.value}
              value={option.value}
            >
              {renderOptionLabel(option)}
            </Radio>
          ))}
        </Radio.Group>
        {otherSelected && (
          <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
        )}
      </>
    )
  }

  return (
    <TextArea
      autoSize={{ minRows: 1, maxRows: 4 }}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder={question.placeholder || '请输入你的补充说明'}
      // 单行起步保持各步骤底部操作栏基线一致；长文本向上限内换行增高，不再横向滚动。
      value={typeof value === 'string' ? value : ''}
    />
  )
}

/** 待确认选项带说明时在文案上加悬浮提示；无说明时保持原样（对齐正式工程 description 字段）。 */
function renderOptionLabel(option: { label: string; description: string }): ReactElement {
  if (!option.description) return <span>{option.label}</span>
  return (
    <Tooltip
      overlayClassName={cx('workflow-clarification-option-tooltip')}
      placement="top"
      title={option.description}
    >
      <span>{option.label}</span>
    </Tooltip>
  )
}

function OtherInput({
  disabled,
  onChange,
  value
}: {
  disabled?: boolean
  onChange: (value: string) => void
  value: string
}): ReactElement {
  return (
    <TextArea
      autoSize={{ minRows: 2, maxRows: 4 }}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder="请补充其他选择或说明"
      value={value}
    />
  )
}

function clarificationQuestionKey(question: WorkflowClarificationQuestion, index: number): string {
  return question.id || question.header || question.question || String(index)
}

function clarificationAnswerComplete(
  question: WorkflowClarificationQuestion,
  value: WorkflowClarificationAnswer | undefined
): boolean {
  if (question.required === false) return true
  if (question.type === 'choice' || question.type === 'yesno') {
    const selected = selectedAnswerValues(value)
    if (selected.length === 0) return false
    return !selected.includes(OTHER_OPTION_VALUE) || Boolean(answerOtherText(value).trim())
  }
  if (Array.isArray(value)) return value.length > 0
  return typeof value === 'string' && value.trim().length > 0
}

function selectedAnswerValues(value: WorkflowClarificationAnswer | undefined): string[] {
  if (typeof value === 'object' && value && !Array.isArray(value) && 'selected' in value) {
    const selected = value.selected
    return Array.isArray(selected) ? selected.map(String) : [String(selected)]
  }
  if (Array.isArray(value)) return value.map(String)
  return typeof value === 'string' && value ? [value] : []
}

function answerOtherText(value: WorkflowClarificationAnswer | undefined): string {
  return typeof value === 'object' && value && !Array.isArray(value) && 'other' in value
    ? String(value.other || '')
    : ''
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function objectList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === 'object')
      )
    : []
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter((item) => item.trim()) : []
}

// eslint-disable-next-line react-refresh/only-export-components
export function workflowOriginalRequest(workflow: WorkflowRunPayload): string {
  for (const source of [workflow.result, workflow.state]) {
    const requirementSpec = objectValue(source?.requirement_spec)
    const sourceRequest = requirementSpec.source_request
    if (typeof sourceRequest === 'string' && sourceRequest.trim()) return sourceRequest.trim()
  }

  const resultRequest = workflow.result?.request
  if (typeof resultRequest === 'string' && resultRequest.trim()) return resultRequest.trim()

  const stateRequest = workflow.state?.request
  if (typeof stateRequest === 'string' && stateRequest.trim()) return stateRequest.trim()

  const startedEvent = workflow.events.find((event) => event.type === 'workflow.run.started')
  const eventRequest = startedEvent?.data?.request
  return typeof eventRequest === 'string' ? eventRequest.trim() : ''
}

/** 根据当前结构化交互生成续跑消息；设计/计划阶段它同时作为用户回复气泡落进对话留痕。 */
// eslint-disable-next-line react-refresh/only-export-components
export function buildClarificationContinuationMessage(
  workflow: WorkflowRunPayload,
  answers: ClarificationAnswers
): string {
  const clarification = workflowClarification(workflow)
  const acceptanceMessage = pageAcceptanceContinuationMessage(clarification, answers)
  if (acceptanceMessage) return acceptanceMessage
  const dispatchMessage = backgroundDispatchContinuationMessage(clarification, answers)
  if (dispatchMessage) return dispatchMessage
  // 应用API数据绑定链路的续跑文案：与各步骤卡的提交动作逐字对齐。
  if (clarification?.mode === 'api_source_type' && answers.api_source_type !== undefined) {
    return `已选择绑定${answers.api_source_type === '外部服务' ? '外部API' : '数据表'}，请选择具体数据来源。`
  }
  if (clarification?.mode === 'api_source_select' && answers.api_source_select !== undefined) {
    return '已选定数据来源，请在右侧配置面板完成映射绑定。'
  }
  if (clarification?.mode === 'api_source_missing' && answers.api_source_check !== undefined) {
    return '已配置数据来源，请重新检测可用来源。'
  }
  // 应用API映射绑定确认：映射在右侧配置面板收口，续跑进入数据适配生成。
  if (clarification?.mode === 'api_binding' && answers.api_binding !== undefined) {
    return '已完成映射绑定确认，请生成数据适配逻辑。'
  }
  if (clarification?.mode === 'detail_review' && answers.detail_review) {
    const submission = answers.detail_review
    if (
      typeof submission === 'object' &&
      !Array.isArray(submission) &&
      'review_status' in submission
    ) {
      return '已整体审阅并确认全部应用页面和数据源设计，请合并本次结构化修改后继续。'
    }
  }
  const mode = clarification?.mode
  // 保存草稿与确认是不同操作，只有明确确认才推进下游。
  if (
    answers.planning_action &&
    typeof answers.planning_action === 'object' &&
    !Array.isArray(answers.planning_action)
  ) {
    const action = answers.planning_action as { action?: string; feedback?: string }
    if (action.action === 'save_requirements') return '保存需求规格说明书草稿。'
    // edit_requirements 不会作为工作流答案提交（AiChatPanel 拦截为右侧本机切换），无需续跑消息。
    // 重新生成携带用户真实意见时原样作为回复内容；默认话术也是合格的用户指令口吻。
    if (action.action === 'revise')
      return String(action.feedback || '').trim() || '请根据当前审阅内容重新生成技术规划方案。'
    if (action.action === 'retry') return '继续当前生成。'
    if (action.action === 'select_template') return '使用所选模板生成页面设计稿。'
    if (action.action === 'confirm')
      return mode === 'technical_plan_confirmation'
        ? '确认技术规划方案并生成模板。'
        : '确认需求规格说明书并生成 UI 设计。'
  }
  // 逐文件接受：确认动作由消息中的“文件改动”卡（接受按钮）承担，这里生成续跑消息。
  if (mode === 'file_acceptance') {
    return `已接受文件 ${String(answers.file_acceptance || '')} 的变更，请继续下一个文件。`
  }
  if (mode === 'development_entry_confirmation') {
    return '确认进入开发阶段。'
  }
  if (mode === 'planning_stage_entry') {
    return '确认进入计划阶段。'
  }
  // 澄清/修改意见卡：答案已在上方转为只读回看的卡片里，用户气泡只补一句确认话术，不重复罗列。
  if (mode === 'requirement_clarification') {
    return '需求信息已确认，请生成需求规格说明书。'
  }
  if (mode === 'requirement_revision') {
    return '需求修改意见已提交，请更新需求文档。'
  }
  if (mode === 'ui_design_confirmation') {
    const action = String(answers.ui_design_action || '')
    if (action === 'skip') return '跳过 UI 设计，进入计划阶段。'
    return '确认全部 UI 设计稿，进入计划阶段。'
  }
  if (
    mode === 'requirement_spec_confirmation' &&
    answers.confirm_requirement_spec !== undefined &&
    !answerConfirmsYes(answers.confirm_requirement_spec)
  ) {
    return '需求文档需要修改，请返回需求分析阶段补充调整意见。'
  }
  if (
    mode === 'project_plan_confirmation' &&
    answers.confirm_project_plan !== undefined &&
    !answerConfirmsYes(answers.confirm_project_plan)
  ) {
    return '项目计划需要修改，请继续补充调整意见。'
  }
  // 产物确认卡（需求/项目计划/构建任务）：无问答，直接授权推进，返回非空文案保证提交不早退。
  if (mode && ARTIFACT_CONFIRMATION_MAP[mode]) {
    return `已确认${ARTIFACT_CONFIRMATION_MAP[mode].title}，请继续下一步。`
  }
  const questions = clarification?.questions || []
  // originalRequest 历史上用于此处早退判断，但 continuation 最终仅由回答内容拼成（见下方 return）。
  // 冷启动澄清（工作台 autostart 触发）workflow 未携带 request 字段，不应据此早退导致确认无法提交。
  if (questions.length === 0) return ''

  const answerLines = questions
    .map((question, index) => {
      const key = clarificationQuestionKey(question, index)
      const value = answers[key]
      const answer = clarificationAnswerText(value)
      if (!answer || !String(answer).trim()) return ''
      // 用户回复气泡只保留「标题：答案」，问题原文由上方的澄清卡承载，不重复念一遍。
      return `${question.header || question.dimension || `问题${index + 1}`}：${answer}`
    })
    .filter(Boolean)

  if (answerLines.length === 0) return ''

  return answerLines.join('\n')
}

/** 读取需求/计划确认卡的 yes/no 答案，避免“否”被误当成继续推进。 */
function answerConfirmsYes(value: WorkflowClarificationAnswer | undefined): boolean {
  if (value && typeof value === 'object' && !Array.isArray(value) && 'selected' in value) {
    const selected = value.selected
    return (Array.isArray(selected) ? selected : [selected]).some((item) => String(item) === '是')
  }
  if (Array.isArray(value)) return value.some((item) => String(item) === '是')
  return value === '是'
}

/** 把结构化答案压成用户口吻的一行文本：选项直接列举，其他补充随后。 */
function clarificationAnswerText(value: WorkflowClarificationAnswer | undefined): string {
  if (typeof value === 'object' && value && !Array.isArray(value) && 'selected' in value) {
    const selected = selectedAnswerValues(value).filter((item) => item !== OTHER_OPTION_VALUE)
    const parts = selected.length > 0 ? [selected.join('、')] : []
    const other = answerOtherText(value).trim()
    if (other) parts.push(other)
    return parts.join('；')
  }
  if (Array.isArray(value)) return value.join('、')
  return typeof value === 'string' ? value : ''
}

// 从 Workflow payload 的多个位置读取待确认载荷，兼容流式快照、最终结果和自定义事件。
// eslint-disable-next-line react-refresh/only-export-components
export function workflowClarification(
  workflow: WorkflowRunPayload | undefined
): WorkflowClarification | undefined {
  // 历史会话可能残留不完整快照；读取确认信息时必须把它当作无交互工作流处理，不能让消息列表白屏。
  if (!workflow || !workflow.summary || typeof workflow.summary !== 'object') return undefined
  const fromSummary = workflow.summary.clarification
  if (fromSummary && typeof fromSummary === 'object') return fromSummary

  const stateClarification = workflow.state?.clarification
  if (stateClarification && typeof stateClarification === 'object') {
    return stateClarification as WorkflowClarification
  }

  const resultClarification = workflow.result?.clarification
  if (resultClarification && typeof resultClarification === 'object') {
    return resultClarification as WorkflowClarification
  }

  const clarificationEvent = (Array.isArray(workflow.events) ? workflow.events : [])
    .slice()
    .reverse()
    .find((event) => {
      const detail = event.data?.detail
      return Boolean(detail && typeof detail === 'object' && 'clarification' in detail)
    })
  const eventClarification = clarificationEvent?.data?.detail
  if (
    eventClarification &&
    typeof eventClarification === 'object' &&
    'clarification' in eventClarification
  ) {
    const clarification = (eventClarification as { clarification?: unknown }).clarification
    if (clarification && typeof clarification === 'object') {
      return clarification as WorkflowClarification
    }
  }

  return undefined
}

/** 将工作流状态映射为符合工作区语义色的标签颜色。 */
function workflowStatusColor(status: string): string {
  if (status === 'completed' || status === 'passed') return 'green'
  if (status === 'failed' || status === 'error') return 'red'
  if (status === 'requires_user_input') return 'gold'
  if (status === 'running') return 'purple'
  return 'default'
}

function workflowStatusText(status: string): string {
  /** 将工作流状态映射为中文标签。 */

  if (status === 'completed' || status === 'passed') return '完成'
  if (status === 'failed' || status === 'error') return '失败'
  if (status === 'requires_user_input') return '待确认'
  if (status === 'running') return '运行中'
  return status || '未知'
}
