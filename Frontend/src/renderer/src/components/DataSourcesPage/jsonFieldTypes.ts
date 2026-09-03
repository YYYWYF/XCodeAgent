import type { DataSourceFieldType } from '../../typings/dataSources'
import { jsonArrayItemPath, jsonPropertyPath } from './jsonStructure'

export const JSON_FIELD_TYPES: DataSourceFieldType[] = ['string', 'integer', 'number', 'boolean', 'object', 'array', 'null']
export const PATH_FIELD_TYPES: DataSourceFieldType[] = ['string', 'integer', 'number', 'boolean']
export const FIELD_TYPE_OPTIONS = JSON_FIELD_TYPES.map((type) => ({ label: type, value: type }))
const JSON_NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/

/** 判断完整样例值是否符合手动类型，整数不包含布尔值或不安全数值。 */
export function matchesJsonFieldType(value: unknown, type: DataSourceFieldType): boolean {
  switch (type) {
    case 'null': return value === null
    case 'object': return value !== null && typeof value === 'object' && !Array.isArray(value)
    case 'array': return Array.isArray(value)
    case 'integer': return typeof value === 'number' && Number.isSafeInteger(value)
    case 'number': return typeof value === 'number' && Number.isFinite(value)
    default: return typeof value === type
  }
}

/** 按 JSON 数字语法读取数值，不把空文本、十六进制或布尔值当成数字。 */
function numericValue(value: unknown): number {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0
  if (typeof value !== 'string' || !JSON_NUMBER.test(value.trim())) return 0
  const number = Number(value.trim())
  return Number.isFinite(number) ? number : 0
}

/** 根据目标类型尽量保留原值，无法转换时使用明确的 JSON 默认值。 */
export function convertJsonValue(value: unknown, type: DataSourceFieldType): unknown {
  if (matchesJsonFieldType(value, type)) return value
  switch (type) {
    case 'string': return typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''
    case 'number': return numericValue(value)
    case 'integer': {
      const integer = Math.trunc(numericValue(value))
      return Number.isSafeInteger(integer) ? integer : 0
    }
    case 'boolean': return value === 1 || (typeof value === 'string' && value.trim().toLowerCase() === 'true')
    case 'object': return {}
    case 'array': return []
    case 'null': return null
  }
}

/** 从完整样例收集共享路径上的全部值，不受结构预览的深度和数组数量限制。 */
function collectFieldValues(value: unknown, path = '$', values = new Map<string, unknown[]>()): Map<string, unknown[]> {
  const existing = values.get(path)
  if (existing) existing.push(value)
  else values.set(path, [value])
  if (Array.isArray(value)) {
    for (const item of value) collectFieldValues(item, jsonArrayItemPath(path), values)
  } else if (value !== null && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) collectFieldValues(item, jsonPropertyPath(path, key), values)
  }
  return values
}

/** 清理已失效或与手动样例不匹配的声明，number 允许整数样例。 */
export function normalizeJsonFieldTypes(value: unknown, fieldTypes: Record<string, DataSourceFieldType>): Record<string, DataSourceFieldType> {
  if (value === undefined || value === null) return {}
  const values = collectFieldValues(value)
  return Object.fromEntries(Object.entries(fieldTypes).filter(([path, type]) => {
    const matches = values.get(path)
    return JSON_FIELD_TYPES.includes(type) && path.length <= 4096 && matches?.every((item) => matchesJsonFieldType(item, type))
  }))
}

export type JsonTypeConversion = { value: unknown; matchedCount: number; changedCount: number; destructiveCount: number }

/** 使用转义后的规范路径匹配所有元素，以不可变方式预计算转换，供用户确认后提交。 */
export function convertJsonFieldType(sample: unknown, targetPath: string, type: DataSourceFieldType): JsonTypeConversion {
  const result: JsonTypeConversion = { value: sample, matchedCount: 0, changedCount: 0, destructiveCount: 0 }
  /** 遍历真实字段而非执行路径表达式，安全处理点号、括号和 __proto__ 等字段名。 */
  const visit = (value: unknown, path: string): unknown => {
    if (path === targetPath) {
      result.matchedCount += 1
      const converted = convertJsonValue(value, type)
      if (!Object.is(value, converted)) {
        result.changedCount += 1
        if (value !== null && typeof value === 'object' && Object.keys(value).length) result.destructiveCount += 1
      }
      return converted
    }
    if (Array.isArray(value)) return value.map((item) => visit(item, jsonArrayItemPath(path)))
    if (value !== null && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, visit(item, jsonPropertyPath(path, key))]))
    }
    return value
  }
  result.value = visit(sample, '$')
  return result
}
