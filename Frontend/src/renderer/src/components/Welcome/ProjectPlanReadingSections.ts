import { projectPlanPageTreeNodes } from '../ProjectPlanPageTreePreview/pageTreeNodes'

export const PROJECT_PLAN_READING_SECTION_IDS = {
  acceptance: 'project-plan-reading-acceptance',
  architecture: 'project-plan-reading-architecture',
  data: 'project-plan-reading-data',
  experience: 'project-plan-reading-experience',
  overview: 'project-plan-reading-overview'
} as const

export type ProjectPlanReadingSection = {
  id: string
  label: string
}

// 将未知对象安全收窄为 ProjectPlan 的局部字段对象。
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

// 判断数组中是否存在可参与章节展示的对象或字符串项。
function hasItems(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.some((item) => {
      if (typeof item === 'string') return Boolean(item.trim())
      return Boolean(item && typeof item === 'object' && !Array.isArray(item))
    })
  )
}

// 根据 ProjectPlan 中实际存在的内容生成左侧阅读章节，避免显示空章节或概念化标题。
export function projectPlanReadingSections(
  plan: Record<string, unknown>
): ProjectPlanReadingSection[] {
  const overview = asRecord(plan.requirements_overview)
  const hasBusinessFlows = hasItems(overview.business_flows || plan.business_flows)
  const hasPages = projectPlanPageTreeNodes(plan.frontend_pages).length > 0
  const hasApiContracts = hasItems(plan.api_contracts)
  const hasDataSources = hasItems(plan.data_sources)
  const datasourceType = Array.isArray(plan.data_sources)
    ? asRecord(plan.data_sources[0]).type
    : undefined

  return [
    {
      id: PROJECT_PLAN_READING_SECTION_IDS.overview,
      label: '项目概述'
    },
    {
      id: PROJECT_PLAN_READING_SECTION_IDS.architecture,
      label: '技术底座'
    },
    ...(hasBusinessFlows || hasPages
      ? [
          {
            id: PROJECT_PLAN_READING_SECTION_IDS.experience,
            label: '核心流程与页面地图'
          }
        ]
      : []),
    ...(hasApiContracts || hasDataSources
      ? [
          {
            id: PROJECT_PLAN_READING_SECTION_IDS.data,
            label:
              datasourceType === 'static'
                ? '前端 Mock 契约与数据关系'
                : 'HTTP API 契约与数据关系'
          }
        ]
      : []),
    {
      id: PROJECT_PLAN_READING_SECTION_IDS.acceptance,
      label: '权限与验收'
    }
  ]
}
