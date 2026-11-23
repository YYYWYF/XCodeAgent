import type { MappingAssistance } from '../../../../typings/endpointDesignAssist'
import { apiDesignFieldKey } from '../WorkflowRunCard/apiDesignSerialization'

export type MappingAssistIssue = {
  key: string; group: string; label: string; path?: string; unconfigured: boolean; questions: string[]
}

const TECHNICAL_FIELD_REFERENCE = /(?<![\w$])(?:response:response_body:|(?:request|external):(?:path|query|header|request_body|response_body):|(?:column|static):)([\w$-]+(?:\[\])*(?:\.[\w$-]+(?:\[\])*)*)/g

/** 展示问题时优先使用字段说明，缺少说明时保留可读的字段路径。 */
export function readableMappingQuestion(text: string, fieldNames?: ReadonlyMap<string, string>): string {
  return text.replace(TECHNICAL_FIELD_REFERENCE, (reference, path: string) => fieldNames?.get(reference) || path)
}

const TARGET_LABELS: Record<string, string> = {
  request: '接口入参', response: '返回字段', column: '数据表字段', external: '外部入参', static: '静态字段'
}

/** 直接按后端核验过的完整字段身份归组，问题正文仅用于展示。 */
export function mappingAssistIssues(result: MappingAssistance): MappingAssistIssue[] {
  const fields = result.mapping?.fieldMappings.filter((item) => item.endpointField.side === 'response') || []
  const issues = new Map<string, MappingAssistIssue>()
  const requests = result.mapping?.externalApiBindings || []
  const fieldNames = new Map<string, string>()
  const targets = new Map<string, MappingAssistIssue>()

  /** 建立已知字段的标题和取值状态，未配置的字段始终可见。 */
  function registerTarget(key: string, path: string, description: string | undefined, group: string, unconfigured: boolean): void {
    const issue: MappingAssistIssue = { key, path, group, label: `${group} ${path}`, unconfigured, questions: [] }
    targets.set(key, issue)
    fieldNames.set(key, description?.trim() || path)
    if (unconfigured) issues.set(key, issue)
  }

  for (const { externalField: { section, path, description }, right } of requests) {
    registerTarget(`external:${section}:${path}`, `${section}.${path}`, description, '外部入参', !right)
  }
  for (const { endpointField, mappingType } of fields) {
    registerTarget(apiDesignFieldKey(endpointField), endpointField.path, endpointField.description, '返回字段', mappingType === 'unconfigured')
  }
  for (const { target, text } of result.questions) {
    const key = target ?? `question:${text}`
    const path = target?.replace(/^(?:response:response_body:|column:|static:)/, '').replace(/^(?:request|external):([^:]+):/, '$1.')
    const group = !target ? '通用问题' : target.startsWith('external:response_body:') ? '外部响应字段' : TARGET_LABELS[target.split(':', 1)[0]] || '字段'
    const label = path ? `${group} ${path}` : group
    const issue: MappingAssistIssue = issues.get(key) ?? targets.get(key) ?? { key, group, label, path, unconfigured: false, questions: [] }
    const readable = readableMappingQuestion(text, fieldNames)
    if (!issue.questions.includes(readable)) issue.questions.push(readable)
    issues.set(key, issue)
  }
  return [...issues.values()].map((issue) => issue.questions.length ? issue
    : { ...issue, questions: ['请补充这个字段的取值或业务规则。'] })
}
