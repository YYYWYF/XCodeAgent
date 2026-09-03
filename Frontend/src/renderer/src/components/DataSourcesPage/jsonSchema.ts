import type { DataSourceFieldType, DataSourceJsonStructure } from '../../typings/dataSources'
import { jsonArrayItemPath, jsonPropertyPath, normalizeJsonFieldDescriptions } from './jsonStructure'
import { matchesJsonFieldType, normalizeJsonFieldTypes } from './jsonFieldTypes'

/** 从标准 properties/items 读取编辑草稿；路径索引仅用于输入状态，不进入持久化对象。 */
export function readJsonSchemaMetadata(structure: DataSourceJsonStructure | null): {
  descriptions: Record<string, string>
  fieldTypes: Record<string, DataSourceFieldType>
} {
  const descriptions: Record<string, string> = Object.create(null)
  const fieldTypes: Record<string, DataSourceFieldType> = Object.create(null)
  /** 递归读取对象属性和数组元素，特殊字段名始终转义后用于本地标识。 */
  const visit = (node: DataSourceJsonStructure, path: string): void => {
    if (node.description) descriptions[path] = node.description
    if (typeof node.type === 'string') fieldTypes[path] = node.type
    for (const [key, child] of Object.entries(node.properties || {})) visit(child, jsonPropertyPath(path, key))
    if (node.items) visit(node.items, jsonArrayItemPath(path))
  }
  if (structure) visit(structure, '$')
  return { descriptions, fieldTypes }
}

/** 保存时根据完整样例生成标准结构，类型与说明直接写入对应 properties/items 节点。 */
export function buildJsonSchema(sample: unknown, descriptions: Record<string, string>, fieldTypes: Record<string, DataSourceFieldType>): DataSourceJsonStructure | null {
  if (sample === null || sample === undefined) return null
  const validDescriptions = normalizeJsonFieldDescriptions(sample, descriptions)
  const validTypes = normalizeJsonFieldTypes(sample, fieldTypes)
  /** 按共享路径合并所有数组元素，不截断任何深层字段或数组后部字段。 */
  const visit = (values: unknown[], path: string): DataSourceJsonStructure => {
    const inferred = new Set<DataSourceFieldType>()
    for (const value of values) {
      const kind = (['null', 'boolean', 'string', 'integer', 'number', 'object', 'array'] as const).find((type) => matchesJsonFieldType(value, type))
      if (!kind) throw new Error('JSON 样例包含不支持的字段值。')
      inferred.add(kind)
    }
    const types = [...inferred]
    const schema: DataSourceJsonStructure = { type: validTypes[path] || (types.length === 1 ? types[0] : types) }
    if (validDescriptions[path]) schema.description = validDescriptions[path]
    const objects = values.filter((value): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value))
    if (objects.length) {
      const grouped = new Map<string, unknown[]>()
      for (const value of objects) {
        for (const [key, child] of Object.entries(value)) {
          const group = grouped.get(key)
          if (group) group.push(child)
          else grouped.set(key, [child])
        }
      }
      schema.properties = Object.fromEntries([...grouped].map(([key, children]) => [key, visit(children, jsonPropertyPath(path, key))]))
    }
    const items = values.flatMap((value) => Array.isArray(value) ? value : [])
    if (items.length) schema.items = visit(items, jsonArrayItemPath(path))
    return schema
  }
  return visit([sample], '$')
}
