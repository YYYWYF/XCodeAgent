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

/** 数组的联合元素类型加括号，避免把 nullable 元素误读成 nullable 数组。 */
function arrayType(schema: JsonRecord): string {
  const itemType = schemaType(asRecord(schema.items))
  return `${/[|&]/.test(itemType) ? `(${itemType})` : itemType}[]`
}

/** 展示 API 契约类型，并明确标记枚举约束，避免与数据库列类型混淆。 */
export function schemaType(schema: JsonRecord): string {
  if (Array.isArray(schema.enum) && schema.enum.length) {
    return `enum [${schema.enum.map((value) => JSON.stringify(value)).join(', ')}]`
  }
  const reference = textValue(schema.$ref).replace(/^#\/components\/schemas\//, '')
  const types = Array.isArray(schema.type)
    ? stringItems(schema.type)
    : [textValue(schema.type)].filter(Boolean)
  const composition = ['oneOf', 'anyOf', 'allOf'].find((key) => recordItems(schema[key]).length)
  let label =
    reference || types.map((type) => (type === 'array' ? arrayType(schema) : type)).join(' | ')
  if (!label && composition) {
    label = recordItems(schema[composition])
      .map(schemaType)
      .join(composition === 'allOf' ? ' & ' : ' | ')
  }
  if (!label) label = schema.properties || schema.additionalProperties ? 'object' : '未限定类型'
  const typeLabel = schema.nullable === true && !types.includes('null') ? `${label} | null` : label
  return typeLabel
}

/** 为共享的接口摘要汇总全部参数，不按数量截断。 */
export function parameterSummary(endpoint: JsonRecord): string {
  const parameters = recordItems(endpoint.parameters)
  return parameters.length ? parameters.map((parameter) =>
    `${textValue(parameter.name, 'param')} · ${textValue(parameter.in, 'query')} · ${schemaType(asRecord(parameter.schema))}${parameter.required ? ' · 必填' : ''}`
  ).join('；') : '无'
}

/** 读取当前 Schema 的可展开属性，保留必填和字段血缘信息。 */
export function schemaProperties(schema: JsonRecord): Array<[string, JsonRecord]> {
  const properties = asRecord(schema.properties)
  return Object.entries(properties)
    .map(([name, value]) => [name, asRecord(value)] as [string, JsonRecord])
    .filter(([, value]) => Object.keys(value).length > 0)
}
