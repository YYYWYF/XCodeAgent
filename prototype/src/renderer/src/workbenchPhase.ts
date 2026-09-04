import type { ApplicationLifecycle } from './typings'

/** 六阶段旅程的固定顺序；版本生成是验收后的终态动作，不属于阶段。 */
export const WORKBENCH_PHASE_ORDER = [
  'analysis',
  'planning',
  'development',
  'testing',
  'review',
  'acceptance'
] as const

/** 工作台六阶段；验收阶段由用户主责并由独立验收 Agent 承接反馈，其余阶段由对应 Agent 主责。 */
export type WorkbenchPhase = (typeof WORKBENCH_PHASE_ORDER)[number]

/** 阶段产物相对当前迭代的有效性。未到达阶段不能被当作已生成产物。 */
export type WorkbenchPhaseValidity = 'unreached' | 'valid' | 'invalid'

export type WorkbenchAgentIdentity = {
  key: WorkbenchPhase
  /** 短标签：需求分析 / 项目规划 / 开发 / 测试 / 审查 / 验收。 */
  label: string
  /** 主责角色：产品 / 项目 / 研发 / 测试 / 审查 Agent 或用户。 */
  role: string
  /** 职责一句话。 */
  responsibility: string
}

/** 每个阶段的 Agent 身份与职责。 */
export const WORKBENCH_PHASE_AGENTS: Record<WorkbenchPhase, WorkbenchAgentIdentity> = {
  analysis: {
    key: 'analysis',
    label: '需求分析',
    role: '产品 Agent',
    responsibility: '明确应用要解决的问题，维护需求文档'
  },
  planning: {
    key: 'planning',
    label: '项目规划',
    role: '项目 Agent',
    responsibility: '根据确认需求维护项目计划和开发产物清单'
  },
  development: {
    key: 'development',
    label: '开发',
    role: '研发 Agent',
    responsibility: '实现页面、接口、实体并交付代码产物'
  },
  testing: {
    key: 'testing',
    label: '测试',
    role: '测试 Agent',
    responsibility: '按业务用例执行完整应用测试'
  },
  review: {
    key: 'review',
    label: '审查',
    role: '审查 Agent',
    responsibility: '独立审查代码质量、安全与交付完整性'
  },
  acceptance: {
    key: 'acceptance',
    label: '验收',
    role: '产品 Agent',
    responsibility: '依据需求文档协助用户确认当前交付，并承接验收反馈'
  }
}

/** 顶部阶段旅程节点的展示文案：需求分析/项目规划本身即完整名称，其余短标签补充“阶段”后缀。 */
export function workbenchPhaseTabText(phase: WorkbenchPhase): string {
  const { label } = WORKBENCH_PHASE_AGENTS[phase]
  return phase === 'analysis' || phase === 'planning' ? label : `${label}阶段`
}

/** 需求分析阶段的初始化节点：产品 Agent 仍在整理和确认需求。 */
const ANALYSIS_INITIALIZATION_STAGES = new Set([
  'collecting_requirement',
  'analyzing_requirement',
  'awaiting_requirement_clarification',
  'generating_requirement_spec',
  'awaiting_requirement_confirmation'
])

/** 项目规划阶段的初始化节点：项目 Agent 正在生成或确认项目计划。 */
const PLANNING_INITIALIZATION_STAGES = new Set([
  'generating_project_plan',
  'awaiting_project_plan_confirmation',
  'generating_build_task_plan'
])

/** 应用是否仍处于需求分析或项目规划阶段——新应用自动开始规划对话的依据。 */
export function isInitialPlanningPhase(lifecycle?: ApplicationLifecycle): boolean {
  const stage = lifecycle?.initialization?.stage || ''
  return ANALYSIS_INITIALIZATION_STAGES.has(stage) || PLANNING_INITIALIZATION_STAGES.has(stage)
}

/** 开发阶段的工作流节点，只描述研发 Agent 的设计承接、代码编写和文件交付。 */
const DEVELOPMENT_PHASE_NODES = new Set([
  'detail_confirmation',
  'inspect_workspace',
  'inspect_database_context',
  'prepare_build_tasks',
  'build'
])

/** 测试阶段的工作流节点，覆盖启动、非功能和业务用例执行。 */
const TESTING_PHASE_NODES = new Set([
  'startup_test',
  'non_functional_test',
  'business_test',
  'application_test',
  'test',
  'testing'
])

/** 审查阶段的工作流节点；审查 Agent 默认不写开发代码。 */
const REVIEW_PHASE_NODES = new Set(['code_review', 'lint_check', 'security_scan', 'health_check'])

/** 验收阶段只等待用户对当前预览交付作出明确确认。 */
const ACCEPTANCE_PHASE_NODES = new Set(['acceptance'])

const TERMINAL_EXECUTION_STATUSES = new Set(['completed', 'stopped', 'failed'])

/** 根据初始化节点返回需求分析/项目规划阶段，未知节点交给 execution 推导。 */
function phaseForInitializationStage(stage: string): WorkbenchPhase | null {
  if (ANALYSIS_INITIALIZATION_STAGES.has(stage)) return 'analysis'
  if (PLANNING_INITIALIZATION_STAGES.has(stage)) return 'planning'
  return null
}

/** 根据工作流节点返回其所属阶段，避免用 test 等含混名称表达阶段。 */
function phaseForExecutionNode(node: string): WorkbenchPhase | null {
  if (DEVELOPMENT_PHASE_NODES.has(node)) return 'development'
  if (TESTING_PHASE_NODES.has(node)) return 'testing'
  if (REVIEW_PHASE_NODES.has(node)) return 'review'
  if (ACCEPTANCE_PHASE_NODES.has(node)) return 'acceptance'
  return null
}

/** 返回单向旅程中更靠后的阶段。 */
export function compareWorkbenchPhases(left: WorkbenchPhase, right: WorkbenchPhase): number {
  return WORKBENCH_PHASE_ORDER.indexOf(left) - WORKBENCH_PHASE_ORDER.indexOf(right)
}

/** 判断目标阶段是否已经到达，供阶段切换和产物权限共同使用。 */
export function isWorkbenchPhaseReached(
  phase: WorkbenchPhase,
  reachedPhase: WorkbenchPhase
): boolean {
  return compareWorkbenchPhases(phase, reachedPhase) <= 0
}

/** 从 lifecycle 的历史 execution 推导当前版本最高到达的阶段。 */
export function deriveWorkbenchReachedPhase(lifecycle?: ApplicationLifecycle): WorkbenchPhase {
  if (!lifecycle) return 'analysis'

  const initializationPhase = phaseForInitializationStage(lifecycle.initialization?.stage || '')
  if (initializationPhase) return initializationPhase

  const phases = Object.values(lifecycle.activeExecutions || {})
    .map((execution) => phaseForExecutionNode(execution.phase))
    .filter((phase): phase is WorkbenchPhase => Boolean(phase))
  // ready_for_workbench 表示开发目录已可用，即使还没有 execution 记录也已到达开发阶段。
  const executionReachedPhase = phases.reduce(
    (highest, phase) => (compareWorkbenchPhases(phase, highest) > 0 ? phase : highest),
    'development' as WorkbenchPhase
  )
  // 全部业务用例通过后只开放审查阶段入口；必须经过用户确认或已有审查 execution，才算真正到达审查阶段。
  const testExecutionStatus = String(lifecycle.extensions?.testExecutionStatus || '')
  const reviewEntryConfirmed = lifecycle.extensions?.reviewEntryConfirmed === true
  if (
    testExecutionStatus === 'passed' &&
    (reviewEntryConfirmed || compareWorkbenchPhases(executionReachedPhase, 'review') >= 0)
  ) {
    const reviewStatus = String(lifecycle.extensions?.reviewStatus || '')
    const reviewPassed = ['passed', 'approved', '通过'].includes(reviewStatus)
    // 审查通过后，正常旅程自动进入用户验收；验收是否通过仍由独立状态记录。
    return reviewPassed ? 'acceptance' : 'review'
  }
  return executionReachedPhase
}

/** 根据当前活跃 execution 推导 Agent 正在执行的阶段。 */
export function deriveWorkbenchExecutionPhase(lifecycle?: ApplicationLifecycle): WorkbenchPhase {
  if (!lifecycle) return 'analysis'

  const initializationPhase = phaseForInitializationStage(lifecycle.initialization?.stage || '')
  if (initializationPhase) return initializationPhase

  const activePhases = Object.values(lifecycle.activeExecutions || {})
    .filter((execution) => !TERMINAL_EXECUTION_STATUSES.has(execution.status))
    .map((execution) => phaseForExecutionNode(execution.phase))
    .filter((phase): phase is WorkbenchPhase => Boolean(phase))
  if (activePhases.length) {
    return activePhases.reduce(
      (latest, phase) => (compareWorkbenchPhases(phase, latest) > 0 ? phase : latest),
      'development' as WorkbenchPhase
    )
  }

  // 没有活跃任务时保留当前版本已经走到的阶段，而不是回落到旧的三阶段默认值。
  return deriveWorkbenchReachedPhase(lifecycle)
}

/**
 * 计算每个阶段的有效性；后续需求/计划变更可通过 lifecycle 扩展覆盖 invalid 状态。
 * 当前原型没有持久化失效清单，因此已到达阶段默认有效，未来阶段标记为 unreached。
 */
export function deriveWorkbenchPhaseValidity(
  lifecycle?: ApplicationLifecycle,
  reachedPhase = deriveWorkbenchReachedPhase(lifecycle)
): Record<WorkbenchPhase, WorkbenchPhaseValidity> {
  const validity = Object.fromEntries(
    WORKBENCH_PHASE_ORDER.map((phase) => [
      phase,
      isWorkbenchPhaseReached(phase, reachedPhase) ? 'valid' : 'unreached'
    ])
  ) as Record<WorkbenchPhase, WorkbenchPhaseValidity>
  const extensionValidity = lifecycle?.extensions?.phaseValidity
  if (extensionValidity && typeof extensionValidity === 'object') {
    for (const phase of WORKBENCH_PHASE_ORDER) {
      const value = (extensionValidity as Record<string, unknown>)[phase]
      if (value === 'valid' || value === 'invalid' || value === 'unreached') {
        validity[phase] = value
      }
    }
  }
  return validity
}

/** 兼容现有调用方的阶段推导入口，语义明确为当前执行阶段。 */
export function deriveWorkbenchPhase(lifecycle?: ApplicationLifecycle): WorkbenchPhase {
  return deriveWorkbenchExecutionPhase(lifecycle)
}


