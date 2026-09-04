// 工作台阶段剧本：应用验收 / 业务测试 / 代码审查三个阶段的 AG-UI 回放。
// 与开发剧本（workbench.ts）分离：阶段回放不依赖开发对话上下文，只共享快照与生命周期构造。
import type {
  ApplicationLifecycle,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
import { buildReviewReport } from '../../workbenchArtifacts'
import { getBackgroundTasks, readTestCaseTaskStatus } from '../../backgroundTasks'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { appPath, WORKSPACE_DOC_PATHS } from '../workspaceFiles'
import { TEST_CASE_BLUEPRINTS, type TestCaseDefect } from '../../testCasePreparation'
import { nextLifecycleRevision } from './revision'
import {
  MOCK_APPLICATION_PREVIEW_URL,
  addedFileChange,
  delay,
  makeBaseLifecycle,
  makeEmitLifecycle,
  wf,
  withProcessStepTotal,
  type BuildFileTarget,
  type ReplayCallbacks,
  type WorkbenchExecutionLike
} from './workbenchShared'

export const MOCK_TEST_CASES = TEST_CASE_BLUEPRINTS

export const MOCK_BUSINESS_TEST_CASE_TOTAL = MOCK_TEST_CASES.length

/** 六条业务用例的缺陷剧本：覆盖无缺陷、单缺陷和多缺陷三种演示复杂度。 */

export const MOCK_TEST_CASE_DEFECTS: Record<string, Array<Omit<TestCaseDefect, 'status'>>> = {
  'introduction-1': [],
  'my-rechecks-1': [
    {
      id: 'DEF-001',
      severity: '一般',
      target: '我的回检页面',
      title: '列表加载时缺少空状态反馈',
      summary: '接口返回空列表时页面持续显示加载态，用户无法判断当前没有回检单。'
    }
  ],
  'my-rechecks-2': [
    {
      id: 'DEF-002',
      severity: '严重',
      target: '回检单提交表单',
      title: '必填项校验未阻止提交',
      summary: '未填写需求达成情况时仍然发起提交请求，与需求文档中的必填规则不一致。'
    },
    {
      id: 'DEF-003',
      severity: '一般',
      target: '我的回检列表',
      title: '提交成功后列表未自动刷新',
      summary: '提交回检单成功后列表未同步新增记录，用户无法立即确认提交结果。'
    }
  ],
  'my-rechecks-3': [
    {
      id: 'DEF-004',
      severity: '一般',
      target: '状态筛选组件',
      title: '切换筛选条件后页码未重置',
      summary: '用户在后续分页切换状态条件时仍保留旧页码，导致列表错误显示为空。'
    }
  ],
  'query-api-1': [
    {
      id: 'DEF-005',
      severity: '严重',
      target: 'GET /api/rechecks/my',
      title: '分页总数未按当前用户统计',
      summary: 'total 字段统计了全部用户数据，与接口仅查询当前用户回检单的契约冲突。'
    }
  ],
  'query-api-2': []
}

export function confirmationIsYes(value: unknown): boolean {
  if (value && typeof value === 'object' && !Array.isArray(value) && 'selected' in value) {
    const selected = (value as { selected?: unknown }).selected
    return (Array.isArray(selected) ? selected : [selected]).some((item) => String(item) === '是')
  }
  if (Array.isArray(value)) return value.some((item) => String(item) === '是')
  return value === '是'
}

// 严格递增的 lifecycle revision：与 planning.ts 共用 revision.ts 的单调计数器，
// 前端 latestApplicationLifecycle 只在 revision 更大时替换；统一计数避免任何后发
// lifecycle（设计/工作台）被按 revision 拒绝合并导致 stage 冻住。

export function reviewChecks(): unknown[] {
  return [
    {
      id: 'check-lint',
      name: '代码规范检测',
      status: 'passed',
      required: true,
      evidence: '命名约定、模块结构与重复代码扫描通过。'
    },
    {
      id: 'check-security',
      name: '安全扫描',
      status: 'passed',
      required: true,
      evidence: '未发现硬编码密钥、越权访问与注入风险。'
    },
    {
      id: 'check-health',
      name: '健康度评估',
      status: 'passed',
      required: true,
      evidence: '圈复杂度正常 / 重复率 0.8% / 单测覆盖 82%。'
    }
  ]
}

// —— 构建节点的代码变更集（审查阶段报告写入仍复用单文件 Diff 载荷）——

// 开发阶段的代码 Diff 由后台任务承载后，工作台剧本只剩审查报告使用单文件变更集。

export function singleFileChangeSet(
  runId: string,
  target: BuildFileTarget,
  content: string
): WorkspaceCodeChangeSet {
  const change = addedFileChange(
    `cc-${runId}-${target.key}`,
    target.path,
    content,
    target.sourceTool
  )
  return {
    id: `cc-${runId}-${target.key}-${change.additions}`,
    status: 'applied',
    workspaceRoot: appDataByWorkspace().workspaceRoot,
    summary: { files: 1, additions: change.additions, deletions: 0 },
    files: [change]
  }
}

/**
 * 生成同步执行的代码交付目标：页面任务一次交付页面源码（含依赖接口时一并交付后端源码）。
 * 页面文件排在首位，右侧源码区默认展示页面 Diff。
 */

export async function replayApplicationAcceptance(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-application-acceptance-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const appName =
    options.application?.appName || options.application?.name || '武汉分行需求回检系统'

  /** 生成应用级验收执行状态，供阶段门禁识别。 */
  const appExecution = (
    status: string,
    pending?: Record<string, unknown>
  ): WorkbenchExecutionLike => {
    const now = new Date().toISOString()
    return {
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'acceptance',
      status,
      startedAt: now,
      updatedAt: now,
      ...(pending ? { pendingInteraction: pending } : {})
    }
  }

  /** 发布应用级 lifecycle，并注册为浏览器 mock 的最新事实。 */
  const emitLifecycle = (
    status: string,
    pending?: Record<string, unknown>
  ): ApplicationLifecycle => {
    // 验收阶段快照必须携带前置测试/审查门禁，验收通过后生成版本才能连续放行。
    const lifecycle = {
      schemaVersion: '1.2.0',
      application: { id: appId, name: appName },
      updatedAt: new Date().toISOString(),
      revision: nextLifecycleRevision(),
      initialization: { stage: 'ready_for_workbench', status: 'completed' },
      extensions: {
        testExecutionStatus: 'passed',
        testCasesCompleted: MOCK_BUSINESS_TEST_CASE_TOTAL,
        testCasesTotal: MOCK_BUSINESS_TEST_CASE_TOTAL,
        reviewStatus: 'passed',
        acceptanceStatus: status === 'completed' ? 'passed' : 'pending',
        phaseValidity: {
          analysis: 'valid',
          planning: 'valid',
          development: 'valid',
          testing: 'valid',
          review: 'valid',
          acceptance: 'valid'
        }
      },
      activeExecutions: { [runId]: appExecution(status, pending) }
    } as ApplicationLifecycle
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }

  /** 同步应用验收 Workflow 投影到对话消息。 */
  const emit = (
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, 'acceptance', status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  // 返回对话后的验收反馈只复刻到验收会话，先由产品 Agent 提示用户继续补充问题。
  if (options.acceptanceFeedback) {
    onContent?.('请告诉我验收中发现的问题，或补充具体验收意见。')
    return emit(
      'completed',
      resume?.summary?.lifecycle as ApplicationLifecycle | undefined,
      {},
      {
        summary: {
          phase: 'acceptance',
          status: 'completed',
          message: '已进入验收任务，等待补充意见'
        }
      }
    )
  }

  if (answers.application_acceptance) {
    onContent?.('应用已依据需求文档基线验收通过，可以继续生成版本。')
    const completed = emitLifecycle('completed')
    return emit(
      'completed',
      completed,
      {},
      {
        summary: { phase: 'acceptance', status: 'completed', message: '应用验收通过' }
      }
    )
  }

  const pending = {
    id: `pi-application-acceptance-${Date.now()}`,
    type: 'application_acceptance',
    basedOnRevision: 1,
    payload: { message: '请根据需求文档基线在右侧应用预览中完成验收。' },
    createdAt: new Date().toISOString()
  }
  const lifecycle = emitLifecycle('awaiting_user', pending)
  onContent?.('请根据需求文档基线完成应用验收。')
  return emit(
    'requires_user_input',
    lifecycle,
    {
      clarification: {
        mode: 'application_acceptance',
        status: 'requires_user_input',
        message: '请根据需求文档基线完成应用验收。',
        questions: []
      }
    },
    {
      result: { preview_url: MOCK_APPLICATION_PREVIEW_URL },
      summary: { phase: 'acceptance', status: 'requires_user_input', message: '等待应用验收' }
    }
  )
}

/** 测试阶段剧本：先完成非功测试，再按用例顺序确认并执行对应的用例测试 Workflow。 */

export async function replayApplicationTesting(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const clarification = (resume?.state?.clarification ?? resume?.result?.clarification ?? {}) as {
    mode?: string
  }
  const runId = `mock-application-testing-${Date.now()}`
  const appId = options.application?.id || 'app-pms-new'
  const baseLifecycle = {
    ...makeBaseLifecycle(options.application),
    extensions: {
      testExecutionStatus: 'running',
      testCasesCompleted: 0,
      testCasesTotal: MOCK_BUSINESS_TEST_CASE_TOTAL
    }
  } as ApplicationLifecycle
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, phase, status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  /** 发送当前子 Workflow 的独立步骤，避免非功测试与用例测试互相复制。 */
  const publishWorkflowSteps = (steps: ProcessStepRecord[]): void => {
    onProcessSteps?.(withProcessStepTotal(steps, steps.length))
  }
  let latestCaseWorkflow: WorkflowRunPayload | undefined

  /** 读取用例在后台生成队列中的状态；镜像缺失时返回 unknown，由调用方退化为直接执行。 */
  const readGenerationStatus = (caseId: string): 'ready' | 'waiting' | 'unknown' => {
    // 生成状态由统一后台任务流水承载；队列缺失时退化为直接执行，避免演示卡死。
    return readTestCaseTaskStatus(caseId)
  }

  /**
   * 等待指定用例在后台生成完成。已测完的工作流必须先落定，等待也不能是空窗：
   * 以一条独立的“用例生成检查”工作流承接等待过程，首个节点即检查用例生成情况，
   * 用例就绪后该工作流结束，随后才允许出现执行确认节点。
   */
  const waitForCaseGeneration = async (
    testCase: (typeof MOCK_TEST_CASES)[number],
    executionState: {
      completed: number
      defects: Record<string, TestCaseDefect[]>
      results: Record<string, 'pending' | 'running' | 'passed' | 'failed'>
    }
  ): Promise<ProcessStepRecord> => {
    const initialStep: ProcessStepRecord = {
      id: `case-generation-check-${testCase.id}`,
      kind: 'workflow',
      status: 'running',
      title: '检查用例生成情况',
      detail: '正在检查当前用例是否已由后台生成完成。',
      sequence: 1
    }
    // 生成检查是当前用例 Workflow 的第一个节点，不能另起“测试验证工作流”。
    // 这样检查、授权和执行始终留在同一张用例工作流卡里。
    if (readGenerationStatus(testCase.id) !== 'waiting') {
      return {
        ...initialStep,
        status: 'completed',
        detail: '用例已生成完成，可以进入执行确认。'
      }
    }
    emitCaseWorkflow(
      testCase,
      executionState.completed,
      executionState.results,
      executionState.defects,
      'running',
      undefined,
      { activeCaseId: null, summaryMessage: '正在检查测试用例生成情况' }
    )
    let checkStep = initialStep
    publishWorkflowSteps([checkStep])
    for (let attempt = 0; attempt < 600; attempt += 1) {
      if (readGenerationStatus(testCase.id) !== 'waiting') break
      await delay(800)
      if (readGenerationStatus(testCase.id) !== 'waiting') break
      // 每约 1.6 秒刷新一次后台生成进度，让等待节点保持持续推进的观感。
      if (attempt % 2 === 1) {
        const caseTasks = getBackgroundTasks().filter(
          (item) => item.kind === 'test_case_generation'
        )
        const readyCount = caseTasks.filter((item) => item.status === 'completed').length
        checkStep = {
          ...checkStep,
          detail: `后台已生成 ${readyCount}/${caseTasks.length || readyCount} 条，当前用例仍在生成队列中。`
        }
        publishWorkflowSteps([checkStep])
      }
    }
    return {
      ...checkStep,
      status: 'completed',
      detail: '用例已生成完成，进入执行确认。'
    }
  }

  /** 发布用例测试 DAG 的进度；所有用例、缺陷、修复和复测均投影到同一条 Workflow。 */
  const emitCaseWorkflow = (
    testCase: (typeof MOCK_TEST_CASES)[number],
    completed: number,
    results: Record<string, 'pending' | 'running' | 'passed' | 'failed'>,
    defects: Record<string, TestCaseDefect[]>,
    status: 'running' | 'completed' | 'requires_user_input',
    clarificationState?: Record<string, unknown>,
    display?: { activeCaseId?: string | null; summaryMessage?: string }
  ): WorkflowRunPayload => {
    const activeCaseId =
      display?.activeCaseId === undefined
        ? status === 'completed'
          ? undefined
          : testCase.id
        : display.activeCaseId || undefined
    const lifecycleBase = {
      ...baseLifecycle,
      extensions: {
        ...(baseLifecycle.extensions || {}),
        // 测试阶段进入当前用例的授权节点后即算进行中；“待确认”只描述当前节点。
        testExecutionStatus:
          status === 'completed' && completed === MOCK_BUSINESS_TEST_CASE_TOTAL
            ? 'passed'
            : 'running',
        testCasesCompleted: completed,
        testCasesTotal: MOCK_BUSINESS_TEST_CASE_TOTAL,
        activeCaseId,
        testCaseResults: results,
        testCaseDefects: defects
      }
    } as ApplicationLifecycle
    const lifecycle = makeEmitLifecycle(
      lifecycleBase,
      runId,
      onApplicationLifecycle
    )({
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'business_test',
      status: status === 'requires_user_input' ? 'awaiting_user' : status,
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      ...(status === 'requires_user_input'
        ? {
            // 对话卡和生命周期共享同一待处理交互，卡片才会被判定为当前可操作节点。
            pendingInteraction: {
              id: `test-case-confirm-${testCase.id}`,
              type: 'test_case_execute',
              basedOnRevision: 1,
              payload: { message: `请确认是否执行用例：${testCase.title}` },
              artifactRefs: [{ testCaseId: testCase.id }],
              createdAt: new Date().toISOString()
            }
          }
        : {})
    })
    latestCaseWorkflow = emit(
      'business_test',
      status,
      lifecycle,
      {
        testWorkflowKey: `case:${testCase.id}`,
        testWorkflowType: 'case',
        testCaseId: testCase.id,
        testCaseLabel: testCase.title,
        ...(clarificationState ? { clarification: clarificationState } : {}),
        testExecution: {
          completed,
          defects,
          results,
          status: lifecycle.extensions?.testExecutionStatus,
          total: MOCK_BUSINESS_TEST_CASE_TOTAL
        }
      },
      {
        summary: {
          phase: 'business_test',
          status,
          message:
            display?.summaryMessage ||
            (status === 'requires_user_input'
              ? `请确认是否执行用例：${testCase.title}`
              : status === 'running'
                ? `正在执行用例：${testCase.title}`
                : '该用例测试工作流已完成。')
        }
      }
    )
    return latestCaseWorkflow as WorkflowRunPayload
  }

  // 第一条 Workflow：启动与非功能检查，不与具体业务用例混在一起；续跑用例确认时不重复执行。
  const nonFunctionalSteps: ProcessStepRecord[] = [
    {
      id: 'startup-test',
      kind: 'workflow',
      status: 'running',
      title: '启动测试',
      detail: '启动应用并检查主路由、页面入口和基础运行环境。',
      sequence: 1
    },
    {
      id: 'non-functional-test',
      kind: 'workflow',
      status: 'pending',
      title: '非功能测试',
      detail: '检查异常反馈、响应稳定性和恢复路径。',
      sequence: 2
    }
  ]
  if (!resume) {
    const nonFunctionalLifecycle = emitLifecycle({
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'application_test',
      status: 'running',
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    })
    emit(
      'application_test',
      'running',
      nonFunctionalLifecycle,
      {
        testWorkflowKey: 'non-functional',
        testWorkflowType: 'non-functional'
      },
      {
        summary: { phase: 'application_test', status: 'running', message: '正在执行非功测试工作流' }
      }
    )
    publishWorkflowSteps(nonFunctionalSteps)
    await delay(700)
    nonFunctionalSteps[0] = {
      ...nonFunctionalSteps[0],
      status: 'completed',
      detail: '应用已启动，主路由和页面入口可以访问。'
    }
    nonFunctionalSteps[1] = { ...nonFunctionalSteps[1], status: 'running' }
    publishWorkflowSteps(nonFunctionalSteps)
    await delay(700)
    nonFunctionalSteps[1] = {
      ...nonFunctionalSteps[1],
      status: 'completed',
      detail: '异常反馈、响应稳定性和恢复路径均通过。'
    }
    publishWorkflowSteps(nonFunctionalSteps)
    // 节点完成后必须额外发送 Workflow 终态，否则前端会继续把整张卡识别为运行中。
    const completedNonFunctionalLifecycle = emitLifecycle({
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'application_test',
      status: 'completed',
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    })
    emit(
      'application_test',
      'completed',
      completedNonFunctionalLifecycle,
      {
        testWorkflowKey: 'non-functional',
        testWorkflowType: 'non-functional'
      },
      {
        summary: {
          phase: 'application_test',
          status: 'completed',
          message: '非功测试工作流已完成。'
        }
      }
    )
  }

  // 第二类 Workflow：每条用例对应一条用例测试 Workflow，按目录顺序串行推进。
  const caseResults: Record<string, 'pending' | 'running' | 'passed' | 'failed'> = {}
  for (const testCase of MOCK_TEST_CASES) caseResults[testCase.id] = 'pending'
  const defects: Record<string, TestCaseDefect[]> = {}
  const persistedExecution = (resume?.state?.testExecution || resume?.result?.testExecution) as
    | {
        completed?: unknown
        defects?: unknown
        results?: unknown
      }
    | undefined
  if (persistedExecution?.results && typeof persistedExecution.results === 'object') {
    Object.entries(persistedExecution.results as Record<string, unknown>).forEach(
      ([caseId, result]) => {
        if (['pending', 'running', 'passed', 'failed'].includes(String(result))) {
          caseResults[caseId] = result as (typeof caseResults)[string]
        }
      }
    )
  }
  if (persistedExecution?.defects && typeof persistedExecution.defects === 'object') {
    Object.entries(persistedExecution.defects as Record<string, unknown>).forEach(
      ([caseId, value]) => {
        if (!Array.isArray(value)) return
        const normalized = value.flatMap((item): TestCaseDefect[] => {
          if (!item || typeof item !== 'object') return []
          const defect = item as Record<string, unknown>
          if (!defect.id) return []
          return [
            {
              id: String(defect.id),
              severity: String(defect.severity) === '严重' ? '严重' : '一般',
              target: String(defect.target || '当前用例关联产物'),
              title: String(defect.title || '测试缺陷'),
              summary: String(defect.summary || ''),
              status: ['open', 'repairing', 'resolved'].includes(String(defect.status))
                ? (String(defect.status) as TestCaseDefect['status'])
                : 'open'
            }
          ]
        })
        if (normalized.length > 0) defects[caseId] = normalized
      }
    )
  }
  let completed = Number(persistedExecution?.completed || 0)
  if (!Number.isFinite(completed) || completed < 0)
    completed = Object.values(caseResults).filter((result) => result === 'passed').length

  /** 展示单条用例的执行确认节点，确认后才启动该条用例测试 Workflow。 */
  const showCaseConfirmation = async (
    testCase: (typeof MOCK_TEST_CASES)[number]
  ): Promise<WorkflowRunPayload> => {
    // 用例尚未生成完成时不能执行：由“检查用例生成情况”工作流承接等待，就绪后才进入确认。
    const generationCheckStep = await waitForCaseGeneration(testCase, {
      completed,
      defects: { ...defects },
      results: { ...caseResults }
    })
    // 授权卡出现即表示该条用例 Workflow 已启动，目录状态先进入紫色“进行中”。
    caseResults[testCase.id] = 'running'
    const workflow = emitCaseWorkflow(
      testCase,
      completed,
      { ...caseResults },
      { ...defects },
      'requires_user_input',
      {
        mode: 'test_case_execute',
        status: 'requires_user_input',
        questions: [
          {
            id: 'confirm_test_case',
            header: '测试用例确认',
            type: 'yesno',
            question: `用例“${testCase.title}”已准备完成，是否执行？`,
            allowOther: false
          }
        ]
      }
    )
    onProcessSteps?.(
      withProcessStepTotal(
        [
          generationCheckStep,
          {
            id: `case-confirm-${testCase.id}`,
            kind: 'workflow',
            status: 'requires_user_input',
            title: '确认执行用例',
            detail: '请查看当前用例内容，并确认是否执行。',
            sequence: 2
          }
        ],
        2
      )
    )
    return workflow
  }

  const resumeCaseId = String(resume?.state?.testCaseId || resume?.result?.testCaseId || '')
  const requestedCaseIndex = MOCK_TEST_CASES.findIndex((testCase) => testCase.id === resumeCaseId)
  const currentCaseIndex =
    requestedCaseIndex >= 0
      ? requestedCaseIndex
      : Math.min(MOCK_TEST_CASES.length - 1, Math.max(0, completed))
  const currentCase = MOCK_TEST_CASES[currentCaseIndex]
  const confirmedCurrentCase = confirmationIsYes(answers.confirm_test_case)
  // 当前测试契约只允许从“本条用例是否执行”的确认节点续跑；
  // 不再兼容旧的“汇总用例后统一确认”模式，避免把六条用例压成一条 Workflow。
  if (!resume || clarification.mode === 'test_case_execute') {
    if (!confirmedCurrentCase) return showCaseConfirmation(currentCase)
  }

  const caseWorkflowSteps: ProcessStepRecord[] = [
    {
      id: `case-confirm-${currentCase.id}`,
      kind: 'workflow',
      status: 'completed',
      title: '确认执行用例',
      detail: '用户已确认执行当前用例。',
      sequence: 1
    },
    {
      id: `case-test-${currentCase.id}`,
      kind: 'workflow',
      status: 'running',
      title: `执行用例：${currentCase.title}`,
      detail: `正在执行 ${currentCase.id.toUpperCase()}。`,
      sequence: 2
    },
    {
      id: `case-defects-${currentCase.id}`,
      kind: 'workflow',
      status: 'pending',
      title: '生成缺陷清单',
      detail: '等待测试脚本执行完成后汇总缺陷。',
      sequence: 3
    }
  ]
  caseResults[currentCase.id] = 'running'
  emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'running')
  publishWorkflowSteps([...caseWorkflowSteps])
  await delay(520)

  const scriptedDefects = (MOCK_TEST_CASE_DEFECTS[currentCase.id] || []).map<TestCaseDefect>(
    (defect) => ({ ...defect, status: 'open' })
  )
  caseWorkflowSteps[1] = {
    ...caseWorkflowSteps[1],
    status: 'completed',
    detail:
      scriptedDefects.length > 0
        ? `测试脚本执行完成，发现 ${scriptedDefects.length} 条缺陷。`
        : '测试脚本执行完成，未发现异常。'
  }
  caseWorkflowSteps[2] = {
    ...caseWorkflowSteps[2],
    status: 'running',
    detail: '正在整理执行证据并关联受影响产物。'
  }
  if (scriptedDefects.length > 0) defects[currentCase.id] = scriptedDefects
  publishWorkflowSteps([...caseWorkflowSteps])
  emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'running')
  await delay(420)

  caseWorkflowSteps[2] = {
    ...caseWorkflowSteps[2],
    status: 'completed',
    detail:
      scriptedDefects.length > 0
        ? `已生成 ${scriptedDefects.length} 条缺陷并同步到右侧用例详情。`
        : '缺陷清单为空，本轮无需返修。'
  }

  // 有缺陷的用例继续完成“返修 → 回归”；无缺陷用例在缺陷清单节点后直接结束。
  if (scriptedDefects.length > 0) {
    defects[currentCase.id] = scriptedDefects.map((defect) => ({
      ...defect,
      status: 'repairing'
    }))
    caseWorkflowSteps.push({
      id: `case-repair-${currentCase.id}`,
      kind: 'workflow',
      status: 'running',
      title: `修复缺陷（${scriptedDefects.length}）`,
      detail: '正在调度开发返修节点并逐项处理缺陷。',
      sequence: 4
    })
    publishWorkflowSteps([...caseWorkflowSteps])
    emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'running')
    await delay(520)

    defects[currentCase.id] = scriptedDefects.map((defect) => ({
      ...defect,
      status: 'resolved'
    }))
    caseWorkflowSteps[3] = {
      ...caseWorkflowSteps[3],
      status: 'completed',
      detail: `${scriptedDefects.length} 条缺陷均已修复，准备回归验证。`
    }
    caseWorkflowSteps.push({
      id: `case-retest-${currentCase.id}`,
      kind: 'workflow',
      status: 'running',
      title: '回归验证',
      detail: '重新执行当前用例，确认缺陷关闭且未引入回归问题。',
      sequence: 5
    })
    publishWorkflowSteps([...caseWorkflowSteps])
    emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'running')
    await delay(420)
    caseWorkflowSteps[4] = {
      ...caseWorkflowSteps[4],
      status: 'completed',
      detail: '回归验证通过，关联缺陷已关闭。'
    }
  }

  caseResults[currentCase.id] = 'passed'
  completed += 1
  publishWorkflowSteps([...caseWorkflowSteps])
  emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'completed')
  const nextCase = MOCK_TEST_CASES[currentCaseIndex + 1]
  if (nextCase) return showCaseConfirmation(nextCase)
  return latestCaseWorkflow as WorkflowRunPayload
}

// 应用级审查阶段剧本(复用应用概览会话):
// 审查阶段剧本：审查 Agent 做非功能检查(代码审查/规范检测/健康度)，通过后进入用户验收。

export async function replayCodeReview(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-review-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const baseLifecycle = {
    ...makeBaseLifecycle(options.application),
    extensions: {
      testExecutionStatus: 'passed',
      testCasesCompleted: MOCK_BUSINESS_TEST_CASE_TOTAL,
      testCasesTotal: MOCK_BUSINESS_TEST_CASE_TOTAL,
      phaseValidity: {
        analysis: 'valid',
        planning: 'valid',
        development: 'valid',
        testing: 'valid',
        review: 'valid'
      }
    }
  } as ApplicationLifecycle

  const appExec = (
    phase: string,
    status: string,
    pending?: Record<string, unknown>
  ): WorkbenchExecutionLike => {
    const now = new Date().toISOString()
    return {
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase,
      status,
      startedAt: now,
      updatedAt: now,
      ...(pending ? { pendingInteraction: pending } : {})
    }
  }
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, phase, status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  // 审查报告写入确认后，一次性提交完整审查通过快照，避免同 revision 的补充状态被生命周期 store 丢弃。
  if (
    answers.code_review ||
    (answers.file_acceptance && resume?.summary?.phase === 'code_review')
  ) {
    await delay(300)
    const now = new Date().toISOString()
    const reviewed = {
      ...baseLifecycle,
      updatedAt: now,
      revision: nextLifecycleRevision(),
      extensions: {
        ...baseLifecycle.extensions,
        reviewStatus: 'passed',
        acceptanceStatus: 'pending',
        phaseValidity: {
          analysis: 'valid',
          planning: 'valid',
          development: 'valid',
          testing: 'valid',
          review: 'valid',
          acceptance: 'valid'
        }
      },
      activeExecutions: {
        [runId]: appExec('code_review', 'completed')
      }
    } as ApplicationLifecycle
    onApplicationLifecycle?.(reviewed)
    registerWorkbenchLifecycle(reviewed)
    return emit(
      'code_review',
      'completed',
      reviewed,
      {},
      {
        summary: {
          phase: 'code_review',
          status: 'completed',
          message: '审查阶段已完成，已进入验收阶段'
        }
      }
    )
  }

  // 2. 进入审查后直接走子图(规范检测 → 安全扫描 → 健康度)，不再询问是否启动。
  //    子节点 emit 复用 code_review running 的 lifecycle,不调 emitLifecycle 更新 applicationLifecycle,
  //    避免 lint/security/health 进入 activeExecutions 后全部 completed(terminal)导致
  //    executionPhase 跌回 development（审查中途回到开发）。workflow payload 的 phase 仍逐节点切换（节点卡动态）。
  const reviewRunning = emitLifecycle(appExec('code_review', 'running'))
    emit('code_review', 'running', reviewRunning)
    const steps: Record<string, unknown>[] = []
    const reviewStepTotal = 4
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.(withProcessStepTotal(steps as ProcessStepRecord[], reviewStepTotal))
    }

    // 规范检测
    emit('lint_check', 'running', reviewRunning)
    await delay(1000)
    emit('lint_check', 'completed', reviewRunning)
    pushStep({
      id: 'step-lint',
      kind: 'workflow',
      status: 'completed',
      title: '代码规范检测',
      detail: '命名约定、模块结构与重复代码扫描通过。',
      sequence: 1
    })

    // 安全扫描
    emit('security_scan', 'running', reviewRunning)
    await delay(1000)
    emit('security_scan', 'completed', reviewRunning)
    pushStep({
      id: 'step-security',
      kind: 'workflow',
      status: 'completed',
      title: '安全扫描',
      detail: '未发现硬编码密钥、越权访问与注入风险。',
      sequence: 2
    })

    // 健康度评估
    emit('health_check', 'running', reviewRunning)
    await delay(1000)
    emit('health_check', 'completed', reviewRunning)
    pushStep({
      id: 'step-health',
      kind: 'workflow',
      status: 'completed',
      title: '健康度评估',
      detail: '圈复杂度正常 / 重复率 0.8% / 单测覆盖 82%。',
      sequence: 3,
      checks: reviewChecks()
    })

    const report = buildReviewReport({
      completed: MOCK_BUSINESS_TEST_CASE_TOTAL,
      status: 'passed',
      total: MOCK_BUSINESS_TEST_CASE_TOTAL
    })
    const reportTarget: BuildFileTarget = {
      key: 'code-review',
      name: 'code-review.md',
      path: appPath(WORKSPACE_DOC_PATHS.codeReview),
      content: report,
      sourceTool: 'review_agent'
    }
    const reportLines = report.split('\n')
    for (let visible = 12; ; visible += 12) {
      await delay(260)
      emit('code_review', 'running', reviewRunning, {
        codeChanges: singleFileChangeSet(
          runId,
          reportTarget,
          reportLines.slice(0, visible).join('\n')
        )
      })
      if (visible >= reportLines.length) break
    }
    pushStep({
      id: 'step-report',
      kind: 'workflow',
      status: 'completed',
      title: '生成代码审查报告',
      detail: '报告已生成，等待确认写入工作区。',
      sequence: 4
    })
    const reviewPending = {
      id: `pi-code-review-file-${Date.now()}`,
      type: 'file_acceptance',
      basedOnRevision: 1,
      payload: { message: '代码审查报告已生成，请在右侧确认 Diff 后接受。' },
      createdAt: new Date().toISOString()
    }
    const reviewLifecycle = emitLifecycle(appExec('code_review', 'awaiting_user', reviewPending))
    return emit(
      'code_review',
      'requires_user_input',
      reviewLifecycle,
      {
        clarification: {
          mode: 'file_acceptance',
          status: 'requires_user_input',
          message: '代码审查报告已生成，请确认右侧 Diff。'
        },
        codeChanges: singleFileChangeSet(runId, reportTarget, report)
      },
      {
        summary: {
          phase: 'code_review',
          status: 'requires_user_input',
          message: '等待接受代码审查报告'
        }
      }
    )
}
