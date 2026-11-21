import { asRecord, recordItems, textValue, type JsonRecord } from './TechnicalPlanDocPanelData'
import { isRequiredRenameOnly, pairEntries } from './technicalPlanRename'

export type Difference = {
  path: string
  field: string
  before: unknown
  after: unknown
  kind?: 'rename'
  unchanged?: boolean
}
export type ChangedObject = {
  id: string
  name: string
  kind: '新增' | '修改' | '删除'
  differences: Difference[]
  /** 独立于业务内容的带外结论（例如产物上的 `_change_status` 级联删除）。优先级最高。 */
  declaredKind?: '新增' | '修改' | '删除'
}
export type ChangeGroup = { name: string; objects: ChangedObject[] }

/** 从产物条目上读取 `_change_status`，映射成中文变更类型；缺失或未知返回 undefined。 */
function declaredKind(item: unknown): ChangedObject['declaredKind'] {
  const declared = textValue(asRecord(item)._change_status)
  if (declared === 'added') return '新增'
  if (declared === 'modified') return '修改'
  if (declared === 'deleted') return '删除'
  return undefined
}

const labels: Record<string, string> = {
  frontend: '前端',
  backend: '后端',
  data: '数据库',
  name: '名称',
  description: '说明',
  fields: '字段',
  type: '类型',
  required: '必填',
  nullable: '允许为空',
  default: '默认值',
  parameters: '请求参数',
  request_schema_ref: '请求体引用',
  response_schema_ref: '响应体引用',
  schema: '类型定义',
  requestBody: '请求体',
  responseBody: '响应体',
  properties: '字段',
  items: '数组元素',
  enum: '枚举取值',
  minimum: '最小值',
  maximum: '最大值',
  minLength: '最短长度',
  maxLength: '最长长度',
  pattern: '匹配规则',
  summary: '业务说明',
  authentication: '登录要求',
  method: '请求方法',
  path: '路径',
  base_path: '基础路径',
  entity_ids: '关联实体',
  error_codes: '错误码',
  references: '页面引用',
  endpoint_dependencies: '接口依赖',
  action_implementations: '操作实现',
  permission: '权限',
  bindings: '绑定',
  endpoints: '接口',
  resources: '资源',
  operationResourceKeys: '操作资源',
  entity_field_ref: '关联实体字段',
  additionalProperties: '额外字段',
  schemas: '类型定义'
}
const unordered = new Set([
  'enum',
  'required',
  'entity_ids',
  'operationResourceKeys',
  'error_codes'
])

// 比较时忽略的元数据键：它们描述"本轮相对基线的状态"，不是业务内容本身。
// 正式产物会保留 `_change_status`，基线通常没有，两边直接比必然不等。
const COMPARISON_IGNORED_KEYS = new Set(['_change_status'])

/** 中文解释与原始属性名并列，便于将修改指令逐项对应到差异内容。 */
function differenceLabel(key: string): string {
  return labels[key] ? `${labels[key]}（${key}）` : key
}

/** 为列表项选取当前合同中的稳定身份；参数必须同时匹配位置和名称。 */
function itemIdentity(value: unknown): string | undefined {
  const item = asRecord(value)
  if (item.in && item.name) return `${item.in}.${item.name}`
  for (const key of [
    'id',
    'pageId',
    'actionId',
    'endpoint_id',
    'endpointId',
    'resourceKey',
    'name'
  ]) {
    if (typeof item[key] === 'string' && item[key]) return String(item[key])
  }
  return undefined
}

/** 只在每项身份明确且唯一时按身份建立索引，避免重复名称覆盖内容。 */
function keyedItems(values: unknown[]): Map<string, unknown> | undefined {
  const result = new Map<string, unknown>()
  for (const value of values) {
    const key = itemIdentity(value)
    if (!key || result.has(key)) return undefined
    result.set(key, value)
  }
  return result
}

/** 递归规范对象键与集合顺序，忽略纯排版变化，保留有序列表的业务顺序。 */
function normalized(value: unknown, field = ''): unknown {
  if (Array.isArray(value)) {
    const keyed = keyedItems(value)
    if (keyed)
      return [...keyed]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, item]) => [key, normalized(item)])
    const items = value.map((item) => normalized(item))
    return unordered.has(field)
      ? items.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
      : items
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        // `_change_status` 是平台写入的变更标记（正式产物刻意保留它供开发阶段展示），
        // 不是业务内容；基线通常没有这个字段。若不剔除，每个对象都会因"多了一个键"
        // 被判成"修改"——那会让整份技术规划看起来全变了。
        .filter(([key]) => !COMPARISON_IGNORED_KEYS.has(key))
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, child]) => [key, normalized(child, key)])
    )
  }
  return value
}

/** 字段仅名称变化时合并展示；存在其他变化或多个候选时仍保留真实增删。 */
function entryDifferences(
  before: Map<string, unknown>,
  after: Map<string, unknown>,
  path: string,
  field: string
): Difference[] {
  const canRename = field === 'fields' || field === 'properties'
  return pairEntries(before, after, canRename ? normalized : undefined, field === 'fields').flatMap(
    ([oldKey, newKey]) => {
      if (oldKey !== undefined && newKey !== undefined && oldKey !== newKey) {
        const remaining = differences(
          before.get(oldKey),
          after.get(newKey),
          `${path} → ${newKey}`
        ).filter((change) => field !== 'fields' || change.field !== 'name')
        return [
          {
            path: `${path} → 名称变更`,
            field: 'name',
            before: oldKey,
            after: newKey,
            kind: 'rename' as const,
            unchanged: remaining.length === 0
          },
          ...remaining
        ]
      }
      const key = newKey ?? oldKey!
      return differences(
        before.get(oldKey!),
        after.get(newKey!),
        [path, key].filter(Boolean).join(' → '),
        field === 'properties' ? key : ''
      )
    }
  )
}

/** 从结构化值提取叶子差异，不把确认状态、时间或模型总结混入业务差异。 */
function differences(before: unknown, after: unknown, path = '', field = ''): Difference[] {
  if (JSON.stringify(normalized(before, field)) === JSON.stringify(normalized(after, field)))
    return []
  const objectBefore = before && !Array.isArray(before) && typeof before === 'object'
  const objectAfter = after && !Array.isArray(after) && typeof after === 'object'
  if (
    (objectBefore || before === undefined) &&
    (objectAfter || after === undefined) &&
    (objectBefore || objectAfter)
  ) {
    const old = asRecord(before),
      next = asRecord(after)
    if (field === 'properties')
      return entryDifferences(
        new Map(Object.entries(old)),
        new Map(Object.entries(next)),
        path,
        field
      )
    const keys = [...new Set([...Object.keys(old), ...Object.keys(next)])]
    if (keys.length)
      return keys.flatMap((key) =>
        // 只合并同一 Schema 内随已识别改名同步替换的 required，真实必填变化继续展示。
        key === 'required' && isRequiredRenameOnly(old, next, normalized)
          ? []
          : differences(
              old[key],
              next[key],
              [path, differenceLabel(key)].filter(Boolean).join(' → '),
              key
            )
      )
  }
  if (
    (Array.isArray(before) || before === undefined) &&
    (Array.isArray(after) || after === undefined)
  ) {
    const old = keyedItems(Array.isArray(before) ? before : [])
    const next = keyedItems(Array.isArray(after) ? after : [])
    if (old && next && (old.size || next.size)) {
      return entryDifferences(old, next, path, field)
    }
  }
  return [{ path: path || '内容', field, before, after }]
}

type Comparable = { id: string; name: string; value: unknown }

/** 判断一个比较值是否"空"：无内容的对象/数组，或空字符串。 */
function isEmptyValue(value: unknown): boolean {
  if (value === undefined || value === null) return true
  if (typeof value === 'string') return !value.trim()
  if (Array.isArray(value)) return !value.length
  if (typeof value === 'object') return !Object.keys(value as object).length
  return false
}

/** 按业务对象汇总差异，一个接口修改多个字段仍只计为一个对象。 */
function changedObjects(before: Comparable[], after: Comparable[]): ChangedObject[] {
  const old = new Map(before.map((item) => [item.id, item]))
  const next = new Map(after.map((item) => [item.id, item]))
  return [...new Set([...next.keys(), ...old.keys()])].flatMap((id) => {
    const previous = old.get(id),
      current = next.get(id)
    const changes = differences(previous?.value, current?.value)
    // 首轮创建的基线是空壳（如 architecture 给 `{}`），此时两边都"存在"但基线无内容，
    // 语义上是新增而非修改。架构 / 接口权限绑定这类单对象场景全靠这条判断。
    const previousEmpty = !previous || isEmptyValue(previous.value)
    return changes.length
      ? [
          {
            id,
            name: (current || previous)!.name,
            kind: previousEmpty && current
              ? ('新增' as const)
              : !current
                ? ('删除' as const)
                : ('修改' as const),
            differences: changes,
            // 内容差异之外还要带上产物自带的变更类型：架构这类"每轮都会被重写叙事"
            // 的单对象，diff 会把它标成修改，而后端才是"本轮是否真的改了架构"的权威。
            declaredKind: declaredKind(current?.value)
          }
        ]
      : []
  })
}

/** 收集请求或响应真正引用的类型，使用访问集保留循环引用而不无限展开。 */
function referencedSchemas(
  reference: unknown,
  schemas: JsonRecord,
  visited: Set<string>
): JsonRecord {
  const found: JsonRecord = {}
  const pending = typeof reference === 'string' ? [reference] : []
  while (pending.length) {
    const name = pending.pop()!.split('/').pop()!
    if (name in found || !(name in schemas)) continue
    found[name] = schemas[name]
    visited.add(name)
    const queue: unknown[] = [schemas[name]]
    while (queue.length) {
      const item = queue.pop()
      if (!item || typeof item !== 'object') continue
      const record = asRecord(item)
      if (typeof record.$ref === 'string') pending.push(record.$ref)
      queue.push(...Object.values(item))
    }
  }
  return found
}

/** 将接口的实际请求响应类型纳入该接口比较，共享类型变化会标出每个受影响接口。 */
function contractObjects(plan: JsonRecord, otherPlan: JsonRecord): Comparable[] {
  return recordItems(plan.api_contracts).flatMap((contract) => {
    const { endpoints, schemas: rawSchemas, ...configuration } = contract
    const id = textValue(contract.id)
    const schemas = asRecord(rawSchemas),
      visited = new Set<string>()
    const objects: Comparable[] = [
      {
        id: `contract:${id}`,
        name: `接口分组 · ${textValue(contract.name, id)}`,
        value: configuration
      }
    ]
    for (const endpoint of recordItems(endpoints)) {
      objects.push({
        id: `endpoint:${id}:${textValue(endpoint.id)}`,
        name: `${textValue(endpoint.name, textValue(endpoint.id))} · ${textValue(endpoint.method)} ${textValue(endpoint.path)}`,
        value: {
          ...endpoint,
          requestBody: referencedSchemas(endpoint.request_schema_ref, schemas, visited),
          responseBody: referencedSchemas(endpoint.response_schema_ref, schemas, visited)
        }
      })
    }
    // 另一版本已引用的类型归对应接口审阅，引用方式变化不能伪装成类型被删除。
    const other = recordItems(otherPlan.api_contracts).find((item) => item.id === contract.id)
    for (const endpoint of recordItems(other?.endpoints)) {
      referencedSchemas(endpoint.request_schema_ref, asRecord(other?.schemas), visited)
      referencedSchemas(endpoint.response_schema_ref, asRecord(other?.schemas), visited)
    }
    // 两版都未被接口引用的类型仍属于规划内容，单独呈现其变化。
    for (const [name, schema] of Object.entries(schemas)) {
      if (!visited.has(name))
        objects.push({ id: `schema:${id}:${name}`, name: `未引用类型 · ${name}`, value: schema })
    }
    return objects
  })
}

/** 映射实体或页面对象的身份和可读名称，页面名称来自当前已确认 ProductPlan。 */
function planObjects(plan: JsonRecord, key: string, productPlan: JsonRecord): Comparable[] {
  const pages = new Map(
    recordItems(productPlan.pages).map((page) => [textValue(page.pageId), textValue(page.name)])
  )
  return recordItems(plan[key]).map((item) => {
    const id = textValue(key === 'pages' ? item.pageId : item.id)
    return {
      id: `${key}:${id}`,
      name: key === 'pages' ? pages.get(id) || id : textValue(item.name, id),
      value: item
    }
  })
}

/** 按当前五个阅读分区计算差异，仅比较产品内容，排除根级确认与派生摘要。 */
export function technicalPlanDiff(
  before: JsonRecord,
  after: JsonRecord,
  productPlan: JsonRecord
): ChangeGroup[] {
  const oldPermissions = { ...asRecord(before.authorization_manifest) }
  const newPermissions = { ...asRecord(after.authorization_manifest) }
  delete oldPermissions.fingerprint
  delete newPermissions.fingerprint
  return [
    {
      name: '架构',
      objects: changedObjects(
        [{ id: 'architecture', name: '技术架构', value: before.architecture }],
        [{ id: 'architecture', name: '技术架构', value: after.architecture }]
      )
    },
    {
      name: '实体',
      objects: changedObjects(
        planObjects(before, 'entities', productPlan),
        planObjects(after, 'entities', productPlan)
      )
    },
    {
      name: 'API 契约',
      objects: changedObjects(contractObjects(before, after), contractObjects(after, before))
    },
    {
      name: '页面绑定',
      objects: changedObjects(
        planObjects(before, 'pages', productPlan),
        planObjects(after, 'pages', productPlan)
      )
    },
    {
      name: '接口权限绑定',
      objects: changedObjects(
        [{ id: 'authorization', name: '接口权限绑定', value: oldPermissions }],
        [{ id: 'authorization', name: '接口权限绑定', value: newPermissions }]
      )
    }
  ]
}

/** 按原始 JSON 属性和值展示单项变化，使说明下方的内容能直接对应修改指令。 */
export function differenceCode(field: string, value: unknown): string {
  const content = JSON.stringify(value, null, 2)
  return field ? `${JSON.stringify(field)}: ${content}` : content
}
