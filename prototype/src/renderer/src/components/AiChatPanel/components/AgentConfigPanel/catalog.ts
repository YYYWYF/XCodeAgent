import type { DevelopmentPlanningAgent } from '../../../../agentDevelopment'
import type { AgentConfigResource, AgentConfigResourceKind } from './types'

export const AGENT_CONFIG_MODEL = 'minimax-m2p5-229b-w8a8'

export const AGENT_CONFIG_RESOURCE_KIND_LABELS: Record<AgentConfigResourceKind, string> = {
  skills: '技能',
  knowledge: '知识检索',
  tools: '工具'
}

const SKILL_AND_TOOL_CATALOG: AgentConfigResource[] = [
  {
    id: 'skill-creator',
    name: '技能创建器',
    description:
      'Guide for creating effective skills. This skill should be used when users want to create a new skill or update an existing skill.'
  },
  {
    id: 'collection-call-strategy',
    name: '催收外呼策略生成',
    description:
      '催收外呼策略生成SKILL。根据客户画像标签，从话术策略库JSON中匹配特征和策略，生成催收画像和策略。'
  },
  {
    id: 'skill-updater',
    name: '技能更新器',
    description:
      '技能更新助手。当用户要求更新、修改、编辑、重整、添加功能或重新打包现有技能时优先触发。'
  },
  {
    id: 'news',
    name: 'news',
    description:
      'Look up the latest news for the user from specified news sites. Provides authoritative URLs for politics, finance, society, world, tech, and more.'
  },
  {
    id: 'file-reader',
    name: 'file-reader',
    description:
      'Read and summarize text-based file types only. Prefer read_file for text formats and use shell detection when necessary.'
  },
  {
    id: 'pdf',
    name: 'pdf',
    description:
      'This guide covers essential PDF processing operations using Python libraries and command-line tools.'
  },
  {
    id: 'docx',
    name: 'docx',
    description: 'A .docx file is a ZIP archive containing XML files.'
  }
]

/** 根据当前智能体的已确认知识引用生成可添加的知识检索资源。 */
export function buildKnowledgeCatalog(agent: DevelopmentPlanningAgent): AgentConfigResource[] {
  return agent.knowledgeReferences.map((reference) => ({
    id: reference,
    name: reference.replace(/^knowledge:/, '') || reference,
    description: '当前项目已确认的知识引用，可用于回答时的知识检索。'
  }))
}

/** 返回配置页某个资源类型的目录；技能和工具复用截图中的可选项。 */
export function getAgentConfigCatalog(
  kind: AgentConfigResourceKind,
  agent: DevelopmentPlanningAgent
): AgentConfigResource[] {
  if (kind === 'knowledge') return buildKnowledgeCatalog(agent)
  return SKILL_AND_TOOL_CATALOG
}

/** 按名称和描述过滤配置资源，保持弹窗搜索行为可测试且无副作用。 */
export function filterAgentConfigResources(
  resources: AgentConfigResource[],
  query: string
): AgentConfigResource[] {
  const normalizedQuery = query.trim().toLocaleLowerCase()
  if (!normalizedQuery) return resources
  return resources.filter((resource) =>
    `${resource.name}\n${resource.description}`.toLocaleLowerCase().includes(normalizedQuery)
  )
}
