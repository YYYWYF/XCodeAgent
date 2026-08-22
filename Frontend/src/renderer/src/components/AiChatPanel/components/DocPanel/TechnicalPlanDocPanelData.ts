export type JsonRecord = Record<string, unknown>

/** 把未知值收窄为普通对象，避免右侧面板因异常数据崩溃。 */
export function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as JsonRecord) : {}
}

/** 只保留数组中的对象项，供列表和计数使用。 */
export function recordItems(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.map(asRecord).filter((item) => Object.keys(item).length > 0)
    : []
}

/** 将结构化值转换成稳定的单行文案。 */
export function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback
}

/** 将数组中的简单值转换成短标签列表。 */
export function stringItems(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => textValue(item).trim()).filter(Boolean) : []
}

/** 读取字段集合中的可读标签，兼容当前实体字段的 name/label 结构。 */
export function fieldLabel(field: JsonRecord, fallback: string): string {
  return textValue(field.label) || textValue(field.name) || fallback
}

/** 将 Endpoint 的 method 映射为稳定的样式类名。 */
export function methodClass(method: string): string {
  return `is-${method.toLowerCase()}`
}

/** 读取 Contract 的鉴权短标签，避免在窄栏中展示过长角色列表。 */
export function authenticationLabel(value: unknown): string {
  const authentication = asRecord(value)
  return authentication.required === false ? '无需认证' : 'Bearer'
}

/** 从 Contract 的 Schema 字典中读取指定 Schema。 */
export function schemaFor(contract: JsonRecord, schemaRef: string): JsonRecord {
  return asRecord(asRecord(contract.schemas)[schemaRef])
}

/** 从 Schema 或数组元素中读取规范化后的引用类型名称。 */
export function schemaReferenceName(schema: JsonRecord): string {
  const target = textValue(schema.type) === 'array' ? asRecord(schema.items) : schema
  return textValue(target.$ref).replace(/^#\/components\/schemas\//, '')
}

/** 将 schema type、数组和引用信息压缩成可读类型标签。 */
export function schemaType(schema: JsonRecord): string {
  const type = textValue(schema.type)
  if (type === 'array') {
    const items = asRecord(schema.items)
    const itemType = schemaReferenceName(schema) || textValue(items.type)
    return itemType ? `${itemType}[]` : 'array[]'
  }
  if (type) return type
  return schemaReferenceName(schema) || 'object'
}

/** 读取 Endpoint 的参数摘要，供右侧检查器使用。 */
export function parameterSummary(endpoint: JsonRecord): string {
  const parameters = recordItems(endpoint.parameters)
  if (!parameters.length) return '无'
  return parameters
    .slice(0, 2)
    .map((parameter) => {
      const required = parameter.required ? ' · 必填' : ''
      return `${textValue(parameter.name, 'param')} · ${textValue(parameter.in, 'query')} · ${schemaType(asRecord(parameter.schema))}${required}`
    })
    .join('；')
}

/** 读取当前 Schema 的可展开属性，保留必填和字段血缘信息。 */
export function schemaProperties(schema: JsonRecord): Array<[string, JsonRecord]> {
  const properties = asRecord(schema.properties)
  return Object.entries(properties)
    .map(([name, value]) => [name, asRecord(value)] as [string, JsonRecord])
    .filter(([, value]) => Object.keys(value).length > 0)
}
