import type { ApplicationLifecycle } from './typings'

const DEVELOPMENT_ENTRY_STORAGE_PREFIX = 'xcodeagent:enter-dev-confirmed:'
const DEVELOPMENT_ENTRY_EVENT = 'xcodeagent:development-entered'

/** 生成应用进入开发阶段的持久化键。 */
function developmentEntryStorageKey(applicationId: string): string {
  return `${DEVELOPMENT_ENTRY_STORAGE_PREFIX}${applicationId}`
}

/** 判断用户是否已明确让指定新应用进入开发阶段。 */
export function hasApplicationEnteredDevelopment(applicationId: string): boolean {
  return window.localStorage.getItem(developmentEntryStorageKey(applicationId)) === '1'
}

/** 持久化进入开发阶段的决定，并通知当前窗口内依赖该门禁的功能。 */
export function markApplicationEnteredDevelopment(applicationId: string): void {
  window.localStorage.setItem(developmentEntryStorageKey(applicationId), '1')
  window.dispatchEvent(
    new CustomEvent(DEVELOPMENT_ENTRY_EVENT, { detail: { applicationId } })
  )
}

/** 监听指定应用进入开发阶段的决定，兼顾当前窗口操作与其他窗口同步。 */
export function subscribeApplicationDevelopmentEntry(
  applicationId: string,
  listener: () => void
): () => void {
  const handleDevelopmentEntry = (event: Event): void => {
    const enteredApplicationId = (event as CustomEvent<{ applicationId?: string }>).detail
      ?.applicationId
    if (enteredApplicationId === applicationId) listener()
  }
  const handleStorage = (event: StorageEvent): void => {
    if (event.key === developmentEntryStorageKey(applicationId) && event.newValue === '1') {
      listener()
    }
  }
  window.addEventListener(DEVELOPMENT_ENTRY_EVENT, handleDevelopmentEntry)
  window.addEventListener('storage', handleStorage)
  return () => {
    window.removeEventListener(DEVELOPMENT_ENTRY_EVENT, handleDevelopmentEntry)
    window.removeEventListener('storage', handleStorage)
  }
}

/**
 * 工作台四大阶段，每个阶段对应一个 Agent。
 * 阶段是主开关：先切到对应阶段，才能编辑该阶段的对象；点文件不会自动切阶段。
 * 旅程1（从零建）与旅程2（增量）共用这条阶段模型。
 */
export type WorkbenchPhase = 'product' | 'development' | 'test' | 'review'

export type WorkbenchAgentIdentity = {
  key: WorkbenchPhase
  /** 短标签：设计 / 开发 / 测试 / 审查。 */
  label: string
  /** Agent 身份：产品 Agent / 研发 Agent / 测试 Agent / 审查 Agent。 */
  role: string
  /** 职责一句话。 */
  responsibility: string
}

/** 每个阶段的 Agent 身份与职责。 */
export const WORKBENCH_PHASE_AGENTS: Record<WorkbenchPhase, WorkbenchAgentIdentity> = {
  product: {
    key: 'product',
    label: '设计',
    role: '产品 Agent',
    responsibility: '定 WHAT：需求文档、产品规划、UI 设计和技术规划'
  },
  development: {
    key: 'development',
    label: '开发',
    role: '研发 Agent',
    responsibility: 'spec → code：详细设计、构建'
  },
  test: {
    key: 'test',
    label: '测试',
    role: '测试 Agent',
    responsibility: '构建检查、单元/集成测试、失败修复与项目启动'
  },
  review: {
    key: 'review',
    label: '审查',
    role: '审查 Agent',
    responsibility: '代码审查、规范检测与交付验收'
  }
}

/**
 * 工作台里可被编辑/确认的对象类型，阶段门禁按它判定。
 * 阶段决定当前可编辑哪些对象，其余对象在该阶段只读。
 */
export type EditableObjectType =
  | 'requirement_doc'
  | 'project_plan'
  | 'page_spec'
  | 'endpoint_spec'
  | 'code'
  | 'acceptance'

/** 各阶段可编辑的对象集合；不在集合里的对象在该阶段只读。 */
const PHASE_EDITABLE_OBJECTS: Record<WorkbenchPhase, EditableObjectType[]> = {
  // 产品阶段：app 级 spec（需求文档、项目计划）。详细设计 spec 归研发。
  product: ['requirement_doc', 'project_plan'],
  // 研发阶段：页面 spec、接口 spec、代码。
  development: ['page_spec', 'endpoint_spec', 'code'],
  // 测试阶段：以跑+看+确认为主，仅验收可确认。
  test: [],
  review: ['acceptance']
}

/** 阶段门禁：某对象在指定阶段是否可编辑。 */
export function isObjectEditableInPhase(
  objectType: EditableObjectType,
  phase: WorkbenchPhase
): boolean {
  return PHASE_EDITABLE_OBJECTS[phase].includes(objectType)
}

/** 规划期(设计阶段初始)的 lifecycle stage：应用还没完成需求确认/项目规划。 */
const PLANNING_STAGES = new Set([
  'collecting_requirement',
  'analyzing_requirement',
  'awaiting_requirement_clarification',
  'generating_requirement_spec',
  'awaiting_requirement_confirmation',
  'generating_project_plan',
  'awaiting_project_plan_confirmation',
  'generating_build_task_plan'
])

/** 应用是否仍处于初始设计(规划)阶段——新应用自动开始澄清的依据。 */
export function isInitialPlanningPhase(lifecycle?: ApplicationLifecycle): boolean {
  return Boolean(lifecycle && PLANNING_STAGES.has(lifecycle.initialization?.stage || ''))
}

/** 开发阶段的工作流节点 phase（开发前置检查 → 工作区检查 → DAG → Build → 测试确认）。 */
const DEVELOPMENT_PHASE_NODES = new Set([
  'development_readiness_gate',
  'entity_source_binding',
  'inspect_workspace',
  'inspect_database_context',
  'prepare_build_tasks',
  'build',
  'test_phase_confirmation'
])

/** 测试阶段的工作流节点 phase（集成测试 → 失败修复 → 启动预览）。 */
const TEST_PHASE_NODES = new Set(['integration_test', 'small_task_repair', 'launch_project'])
const REVIEW_PHASE_NODES = new Set(['acceptance', 'finalize_project'])

/** 根据 Workflow 节点归属选择消息应显示的 Agent 阶段。 */
export function workbenchPhaseForNode(
  nodeName: string | undefined,
  fallback: WorkbenchPhase
): WorkbenchPhase {
  const node = String(nodeName || '').trim()
  if (REVIEW_PHASE_NODES.has(node)) return 'review'
  if (TEST_PHASE_NODES.has(node)) return 'test'
  if (DEVELOPMENT_PHASE_NODES.has(node)) return 'development'
  return fallback
}

const TERMINAL_EXECUTION_STATUSES = new Set(['completed', 'stopped', 'failed'])

/**
 * 根据后端权威 lifecycle 推导当前阶段（旅程驱动的自动值，不受手动覆盖影响）。
 * 规划期（进工作台之前）= 设计；进工作台后按活跃 execution 的节点归属开发/测试/审查。
 */
export function deriveWorkbenchPhase(lifecycle?: ApplicationLifecycle): WorkbenchPhase {
  // 工作台里 lifecycle 尚未加载时默认研发（工作台本就是研发领地；产品阶段由手动切回触发）。
  if (!lifecycle) return 'development'

  const stage = lifecycle.initialization?.stage
  if (
    stage &&
    stage !== 'ready_for_workbench' &&
    stage !== 'application_template_generation_failed'
  ) {
    // 还在规划期（澄清/需求文档/项目计划），属产品阶段。
    return 'product'
  }

  const executions = Object.values(lifecycle.activeExecutions || {})
  const activeIn = (nodes: Set<string>): boolean =>
    executions.some(
      (execution) =>
        !TERMINAL_EXECUTION_STATUSES.has(execution.status) && nodes.has(execution.phase)
    )
  // 研发和测试同时在跑时，以更靠后的测试为当前阶段。
  if (activeIn(REVIEW_PHASE_NODES)) return 'review'
  if (activeIn(TEST_PHASE_NODES)) return 'test'
  if (activeIn(DEVELOPMENT_PHASE_NODES)) return 'development'

  // 已进工作台但当前空闲：默认研发（准备做详细设计/构建；增量迭代也从这里切回产品）。
  return 'development'
}

/** 规划期 initialization.stage 的中文节点标签。 */
const INITIALIZATION_STAGE_LABELS: Record<string, string> = {
  collecting_requirement: '收集需求',
  analyzing_requirement: '分析需求',
  awaiting_requirement_clarification: '需求澄清',
  generating_requirement_spec: '生成需求文档',
  awaiting_requirement_confirmation: '确认需求文档',
  generating_project_plan: '生成项目计划',
  awaiting_project_plan_confirmation: '确认项目计划',
  generating_application_template_files: '生成应用模板',
  application_template_generation_failed: '生成失败',
  ready_for_workbench: '已就绪'
}

/** 研发/测试期 execution.phase 的中文节点标签。 */
const EXECUTION_PHASE_LABELS: Record<string, string> = {
  development_readiness_gate: '检查开发前置',
  entity_source_binding: '实体数据源绑定',
  inspect_workspace: '检查工作区',
  inspect_database_context: '获取数据库信息',
  prepare_build_tasks: '生成执行计划',
  build: '开发实现',
  test_phase_confirmation: '开发完成确认',
  integration_test: '集成测试',
  launch_project: '启动预览',
  acceptance: '预览验收',
  finalize_project: '完成交付'
}

const TERMINAL_NODE_STATUSES = new Set(['completed', 'stopped', 'failed'])

/**
 * 当前旅程节点的可读标签（ribbon 副标用）。
 * 规划期取 initialization.stage；工作台期取最近活跃 execution 的 phase；空闲取"待设计"。
 */
export function workbenchPhaseNodeLabel(lifecycle?: ApplicationLifecycle): string {
  if (!lifecycle) return '加载中'

  const stage = lifecycle.initialization?.stage
  if (
    stage &&
    stage !== 'ready_for_workbench' &&
    stage !== 'application_template_generation_failed'
  ) {
    return INITIALIZATION_STAGE_LABELS[stage] || '规划中'
  }

  const activeExecutions = Object.values(lifecycle.activeExecutions || {}).filter(
    (execution) => !TERMINAL_NODE_STATUSES.has(execution.status)
  )
  if (activeExecutions.length) {
    const latest = activeExecutions.reduce((a, b) => (a.updatedAt > b.updatedAt ? a : b))
    return EXECUTION_PHASE_LABELS[latest.phase] || latest.phase || '进行中'
  }

  return '待设计'
}
