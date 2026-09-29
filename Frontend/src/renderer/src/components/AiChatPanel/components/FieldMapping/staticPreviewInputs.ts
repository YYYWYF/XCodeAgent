import type { WorkflowApiDesignDraft, WorkflowApiEndpointFieldSnapshot } from '../../../../typings'

/** 使用完整参数身份区分不同请求区域的同名参数。 */
export function previewInputKey(field: WorkflowApiEndpointFieldSnapshot): string {
  return `${field.side}:${field.location}:${field.path}`
}

/** 收集直接引用的接口参数；包含业务规则时不尝试执行或生成部分结果。 */
export function staticPreviewInputs(draft: WorkflowApiDesignDraft): WorkflowApiEndpointFieldSnapshot[] {
  const inputs = new Map<string, WorkflowApiEndpointFieldSnapshot>()
  for (const mapping of draft.fieldMappings.filter((item) => item.endpointField.side === 'response')) {
    if (mapping.mappingType === 'business_description' || mapping.mappingType === 'source_mapping' && mapping.processingType !== 'direct' || mapping.mappingType === 'value_mapping' && mapping.right.kind === 'business') {
      throw new Error('返回映射包含业务规则，暂不支持本地预览。请在生成后的接口中验证返回结果。')
    }
    if (mapping.mappingType === 'value_mapping' && mapping.right.kind === 'endpoint' && mapping.right.endpointField) {
      inputs.set(previewInputKey(mapping.right.endpointField), mapping.right.endpointField)
    }
  }
  return [...inputs.values()]
}

/** 将试填文本转换为契约类型；空字符串需显式填写 JSON 字符串，避免误当未填写。 */
export function parsePreviewInput(field: WorkflowApiEndpointFieldSnapshot, text: string): unknown {
  if (!text.trim()) throw new Error('请填写用于预览的参数值。')
  const type = field.type.toLowerCase()
  if (type === 'string') return text === '""' ? '' : text
  let value: unknown
  try { value = JSON.parse(text) } catch { throw new Error(`请填写有效的 ${field.type} 值。`) }
  const valid = type === 'integer' ? Number.isSafeInteger(value)
    : type === 'number' ? typeof value === 'number' && Number.isFinite(value)
      : type === 'boolean' ? typeof value === 'boolean'
        : type === 'array' ? Array.isArray(value)
          : type === 'object' ? value !== null && typeof value === 'object' && !Array.isArray(value) : false
  if (!valid) throw new Error(`参数值须符合 ${field.type} 类型。`)
  return value
}
