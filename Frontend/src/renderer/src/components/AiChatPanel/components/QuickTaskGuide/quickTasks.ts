import type {
  DevelopmentArtifacts,
  DevelopmentArtifactProgress,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageOption
} from '../../../../typings'

type QuickTaskItemBase = {
  progress?: DevelopmentArtifactProgress
  description: string
  id: string
  meta: string
  title: string
  /** 本轮相对基线的变更类型；deleted 表示该产物本轮不再存在，不能进入开发。 */
  changeStatus?: 'added' | 'modified' | 'unchanged' | 'deleted'
}

export type PageQuickTaskItem = QuickTaskItemBase & {
  kind: 'page'
  pageId: string
  pageLabel: string
  hasDetailPlan: boolean
}

export type EndpointQuickTaskItem = QuickTaskItemBase & {
  kind: 'endpoint'
  apiContractId: string
  endpointId: string
  endpointLabel: string
  endpointPath: string
  hasDetailPlan: boolean
}

export type EntityQuickTaskItem = QuickTaskItemBase & {
  kind: 'entity'
  entityId: string
  entityLabel: string
  hasDetailPlan: boolean
}

export type QuickTaskItem = PageQuickTaskItem | EndpointQuickTaskItem | EntityQuickTaskItem

/**
 * 判断快捷任务本轮是否不可开发。
 *
 * 只有 `unchanged`（从基线继承、本轮没有任何改动）会被禁用。
 * `deleted` **可以点击**：和普通页面一样进入开发流程，由 workflow 删除它的
 * 代码与 UI 设计稿。
 *
 * 没有 `changeStatus`（拿不到本轮变更信息）时不拦截 —— 此时无法证明"没变"，
 * 误禁会让用户连正常入口都点不进去。
 */
export function quickTaskBlocked(item: Pick<QuickTaskItem, 'changeStatus'>): boolean {
  return item.changeStatus === 'unchanged'
}

/** 将项目计划投影为携带一次性工作流目标、但不定义会话归属的页面与接口快捷任务，实体仅保留在技术规划中。 */
export function buildQuickTasks(
  pages: DevelopmentPlanningPageOption[],
  apiContracts: DevelopmentPlanningApiContract[],
  _entities: DevelopmentPlanningEntityOption[],
  artifacts?: DevelopmentArtifacts
): QuickTaskItem[] {
  const pageTasks: PageQuickTaskItem[] = pages.map((page) => ({
    progress: artifacts?.pages[page.pageId],
    changeStatus: page.changeStatus,
    description: String(page.purpose || '从这个页面开始讨论和开发。').trim(),
    id: `page:${page.pageId}`,
    kind: 'page' as const,
    pageId: page.pageId,
    pageLabel: String(page.label || page.pageId || '未命名页面').trim(),
    hasDetailPlan: Boolean(page.hasDetailPlan),
    meta: String(page.path || '/').trim(),
    title: String(page.label || page.pageId || '未命名页面').trim()
  }))
  const endpointTasks: EndpointQuickTaskItem[] = apiContracts.flatMap((contract) =>
    contract.endpoints.map((endpoint, index) => {
      const apiContractId = String(endpoint.apiContractId || contract.id).trim()
      const endpointId = String(endpoint.id || index + 1).trim()
      const method = String(endpoint.method || 'API')
        .trim()
        .toUpperCase()
      const path = String(endpoint.path || '/').trim()
      return {
        progress: artifacts?.endpoints[apiContractId]?.[endpointId],
        changeStatus: endpoint.changeStatus,
        description: String(
          endpoint.summary || `来自 ${contract.label || contract.id || '当前接口契约'}`
        ).trim(),
        id: `endpoint:${apiContractId}:${endpointId}`,
        kind: 'endpoint' as const,
        apiContractId,
        endpointId,
        endpointLabel: `${method} ${path}`,
        endpointPath: path,
        hasDetailPlan: Boolean(endpoint.hasDetailPlan),
        meta: method,
        title: String(endpoint.name || path).trim() || path
      }
    })
  )
  return [...pageTasks, ...endpointTasks]
}
