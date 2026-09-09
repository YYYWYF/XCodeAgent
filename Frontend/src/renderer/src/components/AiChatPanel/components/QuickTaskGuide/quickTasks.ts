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
  hasDetailPlan: boolean
}

export type EntityQuickTaskItem = QuickTaskItemBase & {
  kind: 'entity'
  entityId: string
  entityLabel: string
  hasDetailPlan: boolean
}

export type QuickTaskItem = PageQuickTaskItem | EndpointQuickTaskItem | EntityQuickTaskItem

/** 将项目计划投影为携带一次性工作流目标、但不定义会话归属的页面、接口与实体快捷任务。 */
export function buildQuickTasks(
  pages: DevelopmentPlanningPageOption[],
  apiContracts: DevelopmentPlanningApiContract[],
  entities: DevelopmentPlanningEntityOption[],
  artifacts?: DevelopmentArtifacts
): QuickTaskItem[] {
  const pageTasks: PageQuickTaskItem[] = pages.map((page) => ({
    progress: artifacts?.pages[page.pageId],
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
        description: String(
          endpoint.summary || `来自 ${contract.label || contract.id || '当前接口契约'}`
        ).trim(),
        id: `endpoint:${apiContractId}:${endpointId}`,
        kind: 'endpoint' as const,
        apiContractId,
        endpointId,
        endpointLabel: `${method} ${path}`,
        hasDetailPlan: Boolean(endpoint.hasDetailPlan),
        meta: method,
        title: path
      }
    })
  )
  const entityTasks: EntityQuickTaskItem[] = entities.map((entity) => ({
    progress: artifacts?.entities[entity.id],
    description: String(entity.purpose || '从这个实体开始配置数据来源。').trim(),
    entityId: entity.id,
    entityLabel: String(entity.label || entity.id || '未命名实体').trim(),
    hasDetailPlan: Boolean(entity.hasDetailPlan),
    id: `entity:${entity.id}`,
    kind: 'entity' as const,
    meta: String(entity.dataSourceType || '待配置').trim(),
    title: String(entity.label || entity.id || '未命名实体').trim()
  }))
  return [...pageTasks, ...endpointTasks, ...entityTasks]
}
