import type { WorkflowApiDesignDraft, WorkflowApiSourceField } from '../../../../typings'
import { previewInputKey, staticPreviewInputs } from './staticPreviewInputs'

export type StaticData = NonNullable<WorkflowApiDesignDraft['staticData']>

/** 校验静态 JSON 并推断字段，与服务端保持同一结构和大小限制。 */
export function staticFields(data: unknown): WorkflowApiSourceField[] {
  if (!data || typeof data !== 'object' || Array.isArray(data) && data.some((item) => !item || typeof item !== 'object' || Array.isArray(item))) throw new Error('静态数据必须是 JSON 对象或对象列表。')
  if (new TextEncoder().encode(JSON.stringify(data)).length > 262144) throw new Error('静态数据不能超过 256 KiB。')
  /** 递归枚举叶子，数组使用 [] 保留每项对应关系。 */
  const visit = (value: unknown, path: string, depth: number): Record<string, string> => {
    if (depth > 12) throw new Error('静态数据最多嵌套 12 层。')
    if (Array.isArray(value)) {
      if (!value.length || value.length > 1000) throw new Error('静态列表需包含 1 至 1000 项，空列表无法推断字段。')
      const first = visit(value[0], `${path}[]`, depth + 1)
      for (const item of value.slice(1)) {
        const other = visit(item, `${path}[]`, depth + 1)
        if (Object.keys(first).length !== Object.keys(other).length || Object.entries(first).some(([key, type]) => other[key] !== type)) throw new Error('同一静态列表中的字段结构和类型必须一致。')
      }
      return first
    }
    if (value && typeof value === 'object') {
      if (!Object.keys(value).length) throw new Error('请填写静态数据，空对象无法推断字段。')
      return Object.fromEntries(Object.entries(value).flatMap(([key, item]) => {
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) || ['__proto__', 'prototype', 'constructor'].includes(key)) throw new Error('字段名须使用字母、数字和下划线，且不能以数字开头或使用保留名称。')
        return Object.entries(visit(item, path ? `${path}.${key}` : key, depth + 1))
      }))
    }
    if (value === null) throw new Error('静态字段暂不支持 null，请填写明确类型的值。')
    if (typeof value === 'number' && (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER)) throw new Error('静态数值必须为有限数，且不能超过安全数值范围。')
    return { [path]: typeof value === 'number' ? Number.isInteger(value) ? 'integer' : 'number' : typeof value }
  }
  return Object.entries(visit(data, '', 0)).map(([path, type]) => ({ sourceType: 'static', path, type, description: '' }))
}

/** 校验当前静态映射，数据变化不会静默清空或重选已配置的字段。 */
export function staticDraftError(draft: WorkflowApiDesignDraft): string {
  return Object.values(staticDraftErrors(draft))[0] || ''
}

/** 按返回字段收集静态映射错误，JSON 本身的问题归属数据内容卡片。 */
export function staticDraftErrors(draft: WorkflowApiDesignDraft): Record<string, string> {
  const errors: Record<string, string> = {}
  try {
    const fields = staticFields(draft.staticData)
    for (const mapping of draft.fieldMappings) {
      if (mapping.mappingType !== 'source_mapping') continue
      /** 校验当前映射，保留每个字段的首个明确错误。 */
      const validateMapping = (): string => {
      for (const source of mapping.sourceFields) {
        if (source.sourceType !== 'static' || !fields.some((field) => field.sourceType === 'static' && field.path === source.path && field.type === source.type)) return '静态来源字段已失效，请重新选择字段并确认类型。'
        if (mapping.processingType === 'direct') {
          const sourceDepth = source.path.match(/\[\]/g)?.length || 0
          const targetDepth = mapping.endpointField.path.match(/\[\]/g)?.length || 0
          if (sourceDepth !== targetDepth) return sourceDepth > 0 && targetDepth === 0
            ? `返回字段「${mapping.endpointField.path}」是单值，静态字段「${source.path}」来自列表，列表层级不一致。若接口返回单个对象，请将数据内容改为 JSON 对象后重新选择字段；若需从列表中筛选或取一条，请配置业务规则。`
            : `返回字段「${mapping.endpointField.path}」与静态字段「${source.path}」的列表层级不一致，请选择层级一致的字段，或配置业务规则说明转换方式。`
        }
        if (mapping.processingType === 'direct' && source.type === 'number' && mapping.endpointField.type === 'integer') return '小数不能直接映射到整数返回字段。'
      }
      return ''
      }
      const message = validateMapping()
      const field = mapping.endpointField
      if (message) errors[`${field.side}:${field.location}:${field.path}`] = message
    }
  } catch (error) { errors.__staticData = error instanceof Error ? error.message : '静态数据无效。' }
  return errors
}

/** 预览静态直连、固定值及用户试填的接口参数；业务规则不在浏览器执行。 */
export function previewStaticResponse(draft: WorkflowApiDesignDraft, inputs: Record<string, unknown> = {}): unknown {
  staticPreviewInputs(draft)
  const error = staticDraftError(draft)
  if (error) throw new Error(error)
  const result: Record<string, unknown> = {}
  const data = draft.staticData!
  /** 按字段路径读取叶子，保留数组结构。 */
  const read = (value: unknown, parts: string[]): unknown => {
    if (!parts.length) return value
    const [part, ...rest] = parts
    if (part === '[]') {
      if (!Array.isArray(value)) throw new Error('列表路径与数据不一致。')
      return value.map((item) => read(item, rest))
    }
    return read((value as Record<string, unknown>)[part], rest)
  }
  /** 解析只包含字段名和列表标记的映射路径。 */
  const tokens = (path: string): string[] => path.replace(/\[\]/g, '.[]').split('.').filter(Boolean)
  /** 写入嵌套响应，拒绝无法确定列表长度的固定值映射。 */
  const write = (object: Record<string, unknown>, parts: string[], value: unknown): void => {
    const [key, ...rest] = parts
    if (['__proto__', 'prototype', 'constructor'].includes(key)) throw new Error('返回字段名不受支持。')
    if (!rest.length) { object[key] = value; return }
    if (rest[0] === '[]') {
      if (!Array.isArray(value)) throw new Error('列表固定值预览需要明确的列表来源，请先使用静态字段映射。')
      const rows = (object[key] ||= []) as Record<string, unknown>[]
      value.forEach((item, index) => {
        if (rest.length === 1) rows[index] = item
        else write(rows[index] ||= {}, rest.slice(1), item)
      })
    } else write((object[key] ||= {}) as Record<string, unknown>, rest, value)
  }
  const mappings = draft.fieldMappings.filter((item) => item.endpointField.side === 'response')
  /** 固定值复用已映射数组的长度；根对象列表也能驱动全部为固定值的列表响应。 */
  const fixedShape = (object: unknown, parts: string[], value: unknown, arrayDepth = 0): unknown => {
    if (!parts.length) return value
    const [key, ...rest] = parts
    if (key === '[]') {
      const rows = Array.isArray(object) ? object : arrayDepth === 0 && Array.isArray(data) ? data : undefined
      if (!rows) throw new Error('无法确定返回列表长度，请先映射同一列表中的静态字段。')
      return rows.map((row) => fixedShape(row, rest, value, arrayDepth + 1))
    }
    return fixedShape(object && typeof object === 'object' ? (object as Record<string, unknown>)[key] : undefined, rest, value, arrayDepth)
  }
  for (const mapping of [...mappings.filter((item) => item.mappingType === 'source_mapping'), ...mappings.filter((item) => item.mappingType !== 'source_mapping')]) {
    const path = ['response', ...tokens(mapping.endpointField.path)]
    let value: unknown
    if (mapping.mappingType === 'source_mapping' && mapping.processingType === 'direct' && mapping.sourceFields[0].sourceType === 'static') value = read(data, tokens(mapping.sourceFields[0].path))
    else if (mapping.mappingType === 'value_mapping' && mapping.right.kind === 'fixed') value = fixedShape(result, path, mapping.right.value)
    else if (mapping.mappingType === 'value_mapping' && mapping.right.kind === 'endpoint' && mapping.right.endpointField) {
      const key = previewInputKey(mapping.right.endpointField)
      if (!Object.prototype.hasOwnProperty.call(inputs, key)) throw new Error(`请填写接口参数「${mapping.right.endpointField.path}」后预览。`)
      value = fixedShape(result, path, inputs[key])
    } else throw new Error('请先完成返回字段映射。')
    write(result, path, value)
  }
  return result.response
}
