import type {
  DevelopmentArtifactProgress, DevelopmentArtifacts, TestEntryGate,
  DevelopmentPlanningPageOption, DevelopmentPlanningApiContract
} from './typings'
import type { WorkbenchPhase } from './workbenchPhase'
import type { BusinessAgentOption } from '../../shared/businessAgent'

/** 根据权威门禁约束测试及后续视图，覆盖手动选择、冷启动恢复与自动阶段推导。 */
export function gateWorkbenchPhase(phase: WorkbenchPhase, gate?: TestEntryGate): WorkbenchPhase {
  return ['test', 'review', 'acceptance'].includes(phase) && gate?.allowed !== true
    ? 'development'
    : phase
}

/** 为尚未加载及被门禁阻断的测试入口提供一致说明。 */
export function testEntryGateReason(gate?: TestEntryGate): string {
  return gate?.reason || (gate ? '' : '正在读取开发产物状态…')
}

/** 为圆点提供可访问的状态说明，未登记产物一律不视为完成。 */
export function developmentStatusLabel(progress?: DevelopmentArtifactProgress): string {
  if (progress?.initialDevelopmentStatus === 'completed' && progress.changeStatus === 'changed') return `有变更：${progress.changeReason || '需要按最新正式产物重新开发'}`
  if (progress?.initialDevelopmentStatus === 'completed' && progress.changeStatus === 'unavailable') return `待检查：${progress.changeReason || '暂无法判断变更'}`
  if (progress?.initialDevelopmentStatus === 'skipped') return '已按用户决定跳过实体开发'
  if (progress?.initialDevelopmentStatus === 'completed') return '初次开发已完成'
  if (progress?.initialDevelopmentStatus === 'in_progress') return '初次开发中（可能正在等待确认）'
  return '初次开发未完成'
}

/** 为新会话产物卡片提供简短且统一的开发状态文案。 */
export function developmentStatusText(progress?: DevelopmentArtifactProgress): string {
  if (progress?.initialDevelopmentStatus === 'completed' && progress.changeStatus === 'changed') return '有变更'
  if (progress?.initialDevelopmentStatus === 'completed' && progress.changeStatus === 'unavailable') return '待检查'
  if (progress?.initialDevelopmentStatus === 'skipped') return '已跳过'
  if (progress?.initialDevelopmentStatus === 'completed') return '已初次完成'
  if (progress?.initialDevelopmentStatus === 'in_progress') return '开发中'
  return '未开发'
}

/** 统计完整分组中的完成项，不使用搜索或折叠后的可见集合。 */
export function developmentCompletedCount(
  records: (DevelopmentArtifactProgress | undefined)[]
): number {
  return records.filter((record) => record?.initialDevelopmentStatus === 'completed'
    && (!record.changeStatus || record.changeStatus === 'unchanged')).length
}

/** 优先展示当前变更结果，首次开发事实仍保留在原记录中。 */
export function developmentDisplayStatus(progress?: DevelopmentArtifactProgress): string {
  return progress?.initialDevelopmentStatus === 'completed' && progress.changeStatus
    && progress.changeStatus !== 'unchanged' ? progress.changeStatus : progress?.initialDevelopmentStatus || 'pending'
}

/** Agent 运行入口沿用显式 agent_id 的开发状态，普通接口读取自身 Endpoint 状态。 */
export function endpointDevelopmentProgress(
  artifacts: import('./typings').DevelopmentArtifacts | undefined,
  contractId: string,
  endpoint: { id: string; apiContractId?: string; kind?: string; agent_id?: string }
): DevelopmentArtifactProgress | undefined {
  if (endpoint.kind === 'agent_runtime') return endpoint.agent_id ? artifacts?.agents?.[endpoint.agent_id] : undefined
  return artifacts?.endpoints[endpoint.apiContractId || contractId]?.[endpoint.id]
}

/** 调用智能体的业务入口与派生入口使用机器人图标，普通 Tool 保留业务接口身份。 */
export function isAgentInvocationEndpoint(endpoint: { kind?: string; agentCall?: { agentId: string } }): boolean {
  return endpoint.kind === 'agent_runtime' || Boolean(endpoint.agentCall)
}

/** 区分能力完成与业务入口待开发，避免将尚未实现的调用适配显示为绿色。 */
export function endpointDevelopmentStatusLabel(
  artifacts: DevelopmentArtifacts | undefined,
  contractId: string,
  endpoint: DevelopmentPlanningApiContract['endpoints'][number]
): string {
  const progress = endpointDevelopmentProgress(artifacts, contractId, endpoint)
  if (endpoint.agentCall && developmentCompletedCount([artifacts?.agents?.[endpoint.agentCall.agentId]])
    && developmentDisplayStatus(progress) === 'pending') return '智能体已完成，接口待开发'
  return developmentStatusLabel(progress)
}

/** 统计页面、Endpoint 和业务智能体；规划实体不参与完成比例或阶段门禁。 */
export function developmentArtifactTotals(artifacts: DevelopmentArtifacts): {
  completed: number
  total: number
} {
  const records = [
    ...Object.values(artifacts.pages),
    ...Object.values(artifacts.agents || {}),
    ...Object.values(artifacts.endpoints).flatMap((endpoints) => Object.values(endpoints))
  ]
  const required = records.filter((record) => record.initialDevelopmentStatus !== 'skipped')
  return { completed: developmentCompletedCount(required), total: required.length }
}

/** 按当前正式目录统计页面、接口及智能体，运行入口与目录圆点读取同一开发记录。 */
export function developmentPlanningTotals(
  pages: DevelopmentPlanningPageOption[],
  contracts: DevelopmentPlanningApiContract[],
  artifacts?: DevelopmentArtifacts,
  agents: BusinessAgentOption[] = []
): { completed: number; total: number } {
  const records = [
    ...pages.map((page) => artifacts?.pages[page.pageId]),
    ...agents.map((agent) => artifacts?.agents?.[agent.agentId]),
    ...contracts.flatMap((contract) => contract.endpoints.map(
      (endpoint) => endpointDevelopmentProgress(artifacts, contract.id, endpoint)
    ))
  ]
  return { completed: developmentCompletedCount(records), total: records.length }
}
