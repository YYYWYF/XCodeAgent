import type { DataSourceJsonStructure } from '../../typings/dataSources'

/** 描述 JSON 结构推导过程中使用的节点形状。 */
export type JsonShape = {
  kind: string
  children: Record<string, JsonShape>
  childOrder: string[]
  arrayItem?: JsonShape
}

/** 限制大样例的推导成本，避免详情弹窗因深层数据卡顿。 */
type ShapeContext = {
  depthLimit: number
  nodeLimit: number
  nodes: number
  truncated: boolean
}

/** 创建一个空的 JSON 形状节点。 */
function emptyShape(kind: string): JsonShape {
  return { kind, children: Object.create(null), childOrder: [] }
}

/** 将多个形状合并为一个稳定、可读的联合形状。 */
function mergeShapes(left: JsonShape, right: JsonShape): JsonShape {
  const kind = left.kind === right.kind ? left.kind : `${left.kind} | ${right.kind}`
  const merged = emptyShape(kind)
  const keys = [...left.childOrder, ...right.childOrder].filter((key, index, all) => all.indexOf(key) === index)
  merged.childOrder = keys
  for (const key of keys) {
    const leftChild = left.children[key]
    const rightChild = right.children[key]
    if (leftChild && rightChild) merged.children[key] = mergeShapes(leftChild, rightChild)
    else merged.children[key] = leftChild || rightChild
  }
  if (left.arrayItem || right.arrayItem) {
    merged.arrayItem = left.arrayItem && right.arrayItem
      ? mergeShapes(left.arrayItem, right.arrayItem)
      : left.arrayItem || right.arrayItem
  }
  return merged
}

/** 从 JSON 值递归推导结构，数组元素会合并为一个代表性结构。 */
function inferShape(value: unknown, context: ShapeContext, depth: number): JsonShape {
  context.nodes += 1
  if (context.nodes > context.nodeLimit) {
    context.truncated = true
    return emptyShape('…')
  }
  if (depth > context.depthLimit) {
    context.truncated = true
    return emptyShape('…')
  }
  if (value === null) return emptyShape('null')
  if (Array.isArray(value)) {
    if (!value.length) return emptyShape('array（空）')
    const itemShapes = value.slice(0, 20).map((item) => inferShape(item, context, depth + 1))
    const itemShape = itemShapes.slice(1).reduce((result, item) => mergeShapes(result, item), itemShapes[0])
    const shape = emptyShape(`array<${itemShape.kind}>`)
    shape.arrayItem = itemShape
    return shape
  }
  if (typeof value === 'object') {
    const shape = emptyShape('object')
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      shape.childOrder.push(key)
      shape.children[key] = inferShape(child, context, depth + 1)
    }
    return shape
  }
  return emptyShape(typeof value === 'number' && Number.isSafeInteger(value) ? 'integer' : typeof value)
}

/** 将 JSON 对象字段编码为不会与嵌套层级混淆的稳定路径。 */
export function jsonPropertyPath(parentPath: string, fieldName: string): string {
  return `${parentPath}[${JSON.stringify(fieldName)}]`
}

/** 将数组元素编码为不包含数组下标的共享路径。 */
export function jsonArrayItemPath(parentPath: string): string {
  return `${parentPath}[]`
}

/** 收集完整 JSON 样例中的字段路径，供保存时清理说明映射。 */
export function collectJsonFieldPaths(value: unknown, parentPath = '$', paths = new Set<string>()): Set<string> {
  paths.add(parentPath)
  if (Array.isArray(value)) {
    for (const item of value) collectJsonFieldPaths(item, jsonArrayItemPath(parentPath), paths)
  } else if (value !== null && typeof value === 'object') {
    for (const [fieldName, child] of Object.entries(value as Record<string, unknown>)) {
      collectJsonFieldPaths(child, jsonPropertyPath(parentPath, fieldName), paths)
    }
  }
  return paths
}

/** 清理字段说明，只保留当前完整样例仍存在的非空说明。 */
export function normalizeJsonFieldDescriptions(value: unknown, descriptions: Record<string, string>): Record<string, string> {
  if (value === undefined || value === null || !descriptions) return {}
  const validPaths = collectJsonFieldPaths(value)
  return Object.fromEntries(Object.entries(descriptions).flatMap(([path, description]) => {
    const normalized = description.trim()
    return validPaths.has(path) && normalized ? [[path, normalized]] : []
  }))
}

/** 将 JSON 值转换为可渲染的结构树及截断状态。 */
export function inferJsonStructure(value: unknown): { shape: JsonShape; truncated: boolean } {
  const context: ShapeContext = { depthLimit: 8, nodeLimit: 300, nodes: 0, truncated: false }
  return { shape: inferShape(value, context, 0), truncated: context.truncated }
}

/** 把已保存的完整结构投影到只读树，显示截断不会修改或重新推导持久化内容。 */
export function projectStoredJsonStructure(structure: DataSourceJsonStructure): { shape: JsonShape; truncated: boolean; descriptions: Record<string, string> } {
  const descriptions: Record<string, string> = Object.create(null)
  const context: ShapeContext = { depthLimit: 8, nodeLimit: 300, nodes: 0, truncated: false }
  /** 只读取结构节点自身的类型和说明，并在界面层限制可见节点数。 */
  const visit = (node: DataSourceJsonStructure, path: string, depth: number): JsonShape => {
    if (context.nodes >= context.nodeLimit) {
      context.truncated = true
      return emptyShape('…')
    }
    context.nodes += 1
    if (depth > context.depthLimit) {
      context.truncated = true
      return emptyShape('…')
    }
    descriptions[path] = node.description || ''
    const types = Array.isArray(node.type) ? node.type : [node.type]
    const shape = emptyShape(types.join(' | '))
    if (node.items) shape.arrayItem = visit(node.items, jsonArrayItemPath(path), depth + 1)
    for (const [name, child] of Object.entries(node.properties || {})) {
      if (context.nodes >= context.nodeLimit) { context.truncated = true; break }
      shape.childOrder.push(name)
      shape.children[name] = visit(child, jsonPropertyPath(path, name), depth + 1)
    }
    shape.kind = types.map((type) => type === 'array' ? shape.arrayItem ? `array<${shape.arrayItem.kind}>` : 'array（空）' : type).join(' | ')
    return shape
  }
  const shape = visit(structure, '$', 0)
  return { shape, truncated: context.truncated, descriptions }
}

/** 安全格式化 JSON 样例，兼容非对象值和无法序列化的运行时值。 */
export function formatJsonSample(value: unknown): string {
  if (value === undefined) return ''
  try {
    const formatted = JSON.stringify(value, null, 2)
    return formatted === undefined ? String(value) : formatted
  } catch {
    return String(value)
  }
}

/** 解析文本样例，返回结构预览所需的值或错误信息。 */
export function parseJsonSampleText(text: string): { value?: unknown; error?: string } {
  if (!text.trim()) return {}
  try {
    return { value: JSON.parse(text) }
  } catch {
    return { error: 'JSON 格式有误，修正后即可查看结构。' }
  }
}
