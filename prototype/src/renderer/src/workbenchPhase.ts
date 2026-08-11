import type { ApplicationLifecycle } from './typings'

/**
 * 工作台三大阶段，每个阶段对应一个 Agent。
 * 阶段是主开关：先切到对应阶段，才能编辑该阶段的对象；点文件不会自动切阶段。
 * 旅程1（从零建）与旅程2（增量）共用这条阶段模型。
 */
export type WorkbenchPhase = 'product' | 'development' | 'test'

export type WorkbenchAgentIdentity = {
  key: WorkbenchPhase
  /** 短标签：产品 / 研发 / 测试。 */
  label: string
  /** Agent 身份：产品 Agent / 研发 Agent / 测试 Agent。 */
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
    responsibility: '定 WHAT：需求文档、项目计划'
  },
  development: {
    key: 'development',
    label: '开发',
    role: '研发 Agent',
    responsibility: 'spec → code：详细设计、构建'
  },
  test: {
    key: 'test',
    label: '审查',
    role: '审查 Agent',
    responsibility: '代码审查、规范检测等非功能检查(功能验收已前置完成)'
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
  test: ['acceptance']
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

/** 研发期的工作流节点 phase（详细设计 → 构建 → 集成测试 → 启动预览）。 */
const DEVELOPMENT_PHASE_NODES = new Set([
  'detail_confirmation',
  'inspect_workspace',
  'inspect_database_context',
  'prepare_build_tasks',
  'build',
  'integration_test',
  'launch_project',
  'acceptance',
  'finalize_project'
])

/**
 * 审查阶段的工作流节点 phase（非功能检查：代码审查 / 规范检测 / 健康度）。
 * 所有页面/接口模块开发完成后自动进入审查阶段(code_review),审查通过即发布。
 * 集成测试归开发阶段：开发以页面 / 接口为单元，集成测试是开发对话链的一环。
 */
const TEST_PHASE_NODES = new Set([
  'code_review',
  'lint_check',
  'security_scan',
  'health_check'
])

const TERMINAL_EXECUTION_STATUSES = new Set(['completed', 'stopped', 'failed'])

/**
 * 根据后端权威 lifecycle 推导当前阶段（旅程驱动的自动值，不受手动覆盖影响）。
 * 规划期（进工作台之前）= 产品；进工作台后按活跃 execution 的节点归属研发/测试。
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
  if (activeIn(TEST_PHASE_NODES)) return 'test'
  if (activeIn(DEVELOPMENT_PHASE_NODES)) return 'development'

  // 应用级审查完成（code_review/finalize completed）→ 保持审查阶段直到发布。
  // 审查 = 非功能检查，所有页面/接口模块开发完成后自动进入审查阶段。
  const appReviewed = executions.some(
    (execution) =>
      execution.scope === 'application' &&
      (execution.phase === 'code_review' || execution.phase === 'finalize_project') &&
      execution.status === 'completed'
  )
  if (appReviewed) return 'test'

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
  detail_confirmation: '详细设计',
  inspect_workspace: '检查工作区',
  inspect_database_context: '获取数据库信息',
  prepare_build_tasks: '生成执行计划',
  build: '开发实现',
  integration_test: '集成测试',
  launch_project: '启动预览',
  acceptance: '预览验收',
  code_review: '代码审查',
  lint_check: '代码规范检测',
  security_scan: '安全扫描',
  health_check: '健康度评估',
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
