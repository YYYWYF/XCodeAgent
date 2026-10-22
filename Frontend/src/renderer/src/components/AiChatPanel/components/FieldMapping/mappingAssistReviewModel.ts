import type { MappingAssistance, MappingAssistArea } from '../../../../typings/endpointDesignAssist'
import { apiDesignFieldKey } from '../WorkflowRunCard/apiDesignSerialization'

export type MappingAssistIssue = {
  key: string; label: string; path?: string; unconfigured: boolean; questions: string[]
}
export type MappingAssistSection = { key: MappingAssistArea; label: string; count?: number }

/** 按完整字段词匹配，避免 total 命中 subtotal 或父路径误认子字段。 */
function mentionsField(text: string, path: string): boolean {
  const escaped = path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`(?<![\\w$.\\[\\]])${escaped}(?![\\w$\\[\\]]|\\.(?=[\\w$\\[]))`).test(text)
}

/** 同一返回字段的问题合并展示；无法唯一关联的问题原样保留，不能猜测目标。 */
export function mappingAssistIssues(result: MappingAssistance): MappingAssistIssue[] {
  const fields = result.mapping?.fieldMappings.filter((item) => item.endpointField.side === 'response') || []
  const issues = new Map<string, MappingAssistIssue>()
  const requests = result.mapping?.externalApiBindings || []
  for (const item of requests) if (!item.right) {
    const key = `external:${item.externalField.section}:${item.externalField.path}`
    issues.set(key, { key, path: `${item.externalField.section}.${item.externalField.path}`, label: `外部入参 ${key}`, unconfigured: true, questions: [] })
  }
  const leafCounts = new Map<string, number>()
  for (const item of fields) {
    const leaf = item.endpointField.path.split('.').pop()!
    leafCounts.set(leaf, (leafCounts.get(leaf) || 0) + 1)
  }
  /** 为真实契约字段建立唯一审阅目标，不影响配置本身。 */
  const fieldIssue = (item: typeof fields[number]): MappingAssistIssue => {
    const key = apiDesignFieldKey(item.endpointField)
    if (!issues.has(key)) issues.set(key, { key, path: item.endpointField.path,
      label: `返回字段 ${item.endpointField.path}`, unconfigured: item.mappingType === 'unconfigured', questions: [] })
    return issues.get(key)!
  }
  for (const item of fields) if (item.mappingType === 'unconfigured') fieldIssue(item)
  for (const text of new Set(result.questions.map((question) => question.trim()).filter(Boolean))) {
    // 外部入参只按完整标识关联，避免与应用返回的同名字段混淆。
    const requestMatches = requests.filter((item) => mentionsField(text, `external:${item.externalField.section}:${item.externalField.path}`))
    if (requestMatches.length) {
      for (const item of requestMatches) {
        const key = `external:${item.externalField.section}:${item.externalField.path}`
        if (!issues.has(key)) issues.set(key, { key, path: `${item.externalField.section}.${item.externalField.path}`, label: `外部入参 ${key}`, unconfigured: !item.right, questions: [] })
        issues.get(key)!.questions.push(text)
      }
      continue
    }
    // 模型可能省略 list[]. 等前缀；只有当前契约中叶名称唯一时才关联。
    const matches = fields.filter((item) => {
      const leaf = item.endpointField.path.split('.').pop()!
      return mentionsField(text, item.endpointField.path) || leafCounts.get(leaf) === 1 && mentionsField(text, leaf)
    })
    if (matches.length) for (const item of matches) fieldIssue(item).questions.push(text)
    else issues.set(`question:${text}`, { key: `question:${text}`, label: result.mapping ? '其他待明确信息' : '数据库操作待明确', unconfigured: false, questions: [text] })
  }
  return [...issues.values()].map((issue) => {
    // 后端统一追加的“未配置”提醒与模型具体问题共用一项，保留所有具体业务问题。
    const generic = issue.key.startsWith('external:') ? `外部入参 ${issue.key} 尚未配置，请补充取值或按需移除可选参数。` : `返回字段 ${issue.path} 尚未配置，请补充其取值规则。`
    const questions = issue.questions.length > 1 ? issue.questions.filter((text) => text !== generic) : issue.questions
    return { ...issue, questions: questions.length ? questions : ['取值尚未配置，请补充真实的取值或业务规则。'] }
  })
}

/** 按本轮识别出的操作生成审阅页签，仅显示可配置的区域。 */
export function mappingAssistSections(result: MappingAssistance): MappingAssistSection[] {
  const mapping = result.mapping
  if (!mapping) return []
  const sections: MappingAssistSection[] = []
  if (mapping.externalApiBindings) sections.push({ key: 'request', label: '外部入参', count: mapping.externalApiBindings.length })
  if (mapping.databaseOperation && mapping.databaseOperation !== 'create') sections.push({ key: 'query', label: '查询条件',
    count: mapping.databaseQuery?.items.reduce((count, item) => count + (item.kind === 'group' ? item.items.length : 1), 0) || 0 })
  if (mapping.databaseOperation === 'create' || mapping.databaseOperation === 'update') sections.push({ key: 'write', label: '写入字段', count: mapping.databaseWrites?.length || 0 })
  sections.push({ key: 'return', label: '返回字段', count: mapping.fieldMappings.filter((item) => item.endpointField.side === 'response').length },
    { key: 'description', label: '映射说明' })
  return sections
}
