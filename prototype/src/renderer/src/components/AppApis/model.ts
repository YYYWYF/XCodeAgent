import {
  bindingKey,
  flattenTargets,
  readDataSources,
  type BindableTarget,
  type DataSource,
  type ExternalApiParamLocation
} from '../DataSources/catalog'

/**
 * 应用API数据层模型 v6：接口即产物，绑定方式按来源分流。
 * API 契约是扁平的接口清单——每个接口就是一个应用API（方法、路径、出入参、用途），
 * 不再有「资源 → 方法」的两级分组。开发应用API就是把契约出入参接到数据来源上：
 * 数据库表套用固定的增删查改模板（操作由接口功能自动判定，槽位由 AI 按契约填好），
 * 外部 API 只做出入参参数适配（语义不一致处用函数表达式做简单加工）。
 * 计划阶段只记录“实现意向”（来源级选择）；开发阶段完成“绑定映射”（表/接口粒度 +
 * 模板槽位 / 适配行 + 字段映射 + 确认），两段状态共用同一 Implementation 结构。
 */

export type ImplementationKind = '数据库' | '外部服务' | '本地实现' | '多来源组合'

/** 契约入参：需求文档定稿的接口输入参数（查询条件、写入字段、路径参数等）。 */
export type ContractParam = {
  /** 英文标识：契约定稿的唯一参数名，映射卡、调试视图与生成代码统一使用。 */
  code: string
  name: string
  /** 是否必填。 */
  required: boolean
  /** 业务含义说明。 */
  summary: string
}

/** 契约出参：接口返回的业务字段，英文标识 + 中文名成对声明。 */
export type ContractOutput = {
  code: string
  name: string
}

/** 契约字段的展示标签：有英文标识时读作「code（中文名）」，否则仅中文名。 */
export function contractFieldLabel(code: string, name: string): string {
  return code ? `${code}（${name}）` : name
}

/** 一个已绑定的来源目标：库表或服务接口；计划意向阶段仅填来源级信息。 */
export type SourceBinding = {
  sourceId: string
  sourceName: string
  /** 表名 / "GET /users/{id}" 接口签名；意向阶段为空。 */
  targetName: string
  targetComment: string
}

/** 一个返回字段与来源字段的对应关系；matched=false 表示待人工确认。 */
export type FieldMapping = {
  field: string
  matched: boolean
  matchType: 'auto' | 'manual'
  sourceLabel: string
}

/** 数据库绑定套用的增删查改模板类型：由接口功能自动判定，不需要人工挑选。 */
export type TableOpKind = '查询' | '新增' | '修改' | '删除'

/**
 * 模板条件的全量比较操作符：等值、数值比较、模糊匹配、集合与空值判断。
 * 不含「介于」等多取值操作符——单取值槽位的行模型放不下，需要时再扩展行结构。
 */
export type ConditionOperator =
  | '等于'
  | '不等于'
  | '大于'
  | '大于等于'
  | '小于'
  | '小于等于'
  | '包含'
  | '不包含'
  | '开头是'
  | '结尾是'
  | '属于'
  | '为空'
  | '不为空'

/** 操作符下拉的完整选项次序：等值 → 区间比较 → 模糊 → 集合 → 空值。 */
export const CONDITION_OPERATORS: ConditionOperator[] = [
  '等于',
  '不等于',
  '大于',
  '大于等于',
  '小于',
  '小于等于',
  '包含',
  '不包含',
  '开头是',
  '结尾是',
  '属于',
  '为空',
  '不为空'
]

/** 空值类操作符不取值：条件行隐藏取值来源。 */
export function operatorNeedsValue(operator: ConditionOperator): boolean {
  return operator !== '为空' && operator !== '不为空'
}

/**
 * 条件操作符 → SQL 片段：卡片 SQL 预览与适配代码生成器共用同一实现，
 * 保证预览与最终 Diff 完全一致。valueExpr 为取值占位符，空值类操作符忽略。
 */
export function conditionSqlFragment(
  column: string,
  operator: ConditionOperator,
  valueExpr: string
): string {
  switch (operator) {
    case '等于':
      return `${column} = ${valueExpr}`
    case '不等于':
      return `${column} <> ${valueExpr}`
    case '大于':
      return `${column} > ${valueExpr}`
    case '大于等于':
      return `${column} >= ${valueExpr}`
    case '小于':
      return `${column} < ${valueExpr}`
    case '小于等于':
      return `${column} <= ${valueExpr}`
    case '包含':
      return `${column} LIKE CONCAT('%', ${valueExpr}, '%')`
    case '不包含':
      return `${column} NOT LIKE CONCAT('%', ${valueExpr}, '%')`
    case '开头是':
      return `${column} LIKE CONCAT(${valueExpr}, '%')`
    case '结尾是':
      return `${column} LIKE CONCAT('%', ${valueExpr})`
    case '属于':
      return `${column} IN (${valueExpr})`
    case '为空':
      return `${column} IS NULL`
    case '不为空':
      return `${column} IS NOT NULL`
  }
}

/** 模板条件行：固定条件（登录用户）或契约入参按操作符落到列上，查询/删除模板使用。 */
export type TemplateConditionRow = {
  /** 条件来源：契约入参名；固定条件为「当前登录用户」这类登录用户语义。 */
  param: string
  /** 契约语义推导的固定条件（如「我的」→ 登录用户圈定数据范围），不来自契约入参。 */
  fixed: boolean
  column: string
  columnComment: string
  operator: ConditionOperator
}

/** 模板写入行：契约入参落到列，新增/修改模板使用。 */
export type TemplateSetterRow = {
  param: string
  column: string
  columnComment: string
  required: boolean
}

export type Implementation = {
  kind: ImplementationKind
  /** 计划阶段的来源级意向（显示名，如“RECHECK_DB”）。 */
  intentSources: string[]
  /** 开发阶段的结构化绑定；意向阶段为空数组。 */
  bindings: SourceBinding[]
  /** 字段级映射；AI 匹配后生成，人工调整覆盖。 */
  mappings: FieldMapping[]
  /** 数据库绑定套用的固定模板（增删查改）；外部服务/本地实现无此项。 */
  tableOp?: TableOpKind
  /** 模板条件行：查询模板的筛选条件 / 删除模板的定位条件。 */
  conditions: TemplateConditionRow[]
  /** 模板写入行：新增/修改模板的写入字段。 */
  setters: TemplateSetterRow[]
  /** 查询模板自动建议的排序（如 created_at DESC），为空表示无自动排序。 */
  orderBy: string
  /** 外部接口参数适配的函数表达式：键见 adaptationExpressionKey，为空表示直接透传。 */
  expressions: Record<string, string>
  /** 外部绑定的入参对齐：契约入参名 → 外部接口入参名；未登记时按名称推导。 */
  requestParamMap: Record<string, string>
  /** 本地实现/多来源组合的规则说明。 */
  rule: string
  matched: boolean
  confirmed: boolean
}

/** 一个应用API = 契约中的一条接口，自带出入参与数据绑定。 */
export type AppApi = {
  id: string
  /** 接口名（如 查询我的回检）。 */
  name: string
  /** 用途说明。 */
  description: string
  /** 契约方法与完整路径。 */
  method: string
  path: string
  /** 契约入参：与返回字段同为需求文档定稿，是模板槽位与参数适配的契约侧。 */
  request: ContractParam[]
  /** 契约返回字段：数据绑定映射的映射目标。 */
  response: ContractOutput[]
  /** 调用该接口的应用页面。 */
  pages: string[]
  implementation: Implementation
}

/** 生成应用API在开发产物目录中的稳定身份。 */
export function appApiArtifactId(objectId: string): string {
  return `app-api:${objectId}`
}

export const localImplementationName = '本项目逻辑'
export const localSourceBinding: SourceBinding = {
  sourceId: 'local',
  sourceName: localImplementationName,
  targetName: '',
  targetComment: '校验输入 → 执行业务规则 → 返回结果'
}

/** 未配置任何来源时的空实现。 */
export function emptyImplementation(kind: ImplementationKind = '数据库'): Implementation {
  return {
    kind,
    intentSources: [],
    bindings: [],
    mappings: [],
    conditions: [],
    setters: [],
    orderBy: '',
    expressions: {},
    requestParamMap: {},
    rule: '',
    matched: false,
    confirmed: false
  }
}

/** 按意向来源名归类实现类型：命中外部服务目录为外部服务，否则按数据库处理。 */
function kindForIntents(intentSources: string[], sources: DataSource[]): ImplementationKind {
  const externalNames = new Set(
    sources.filter((source) => source.type === 'external_service').map((source) => source.name)
  )
  if (intentSources.some((intent) => externalNames.has(intent))) return '外部服务'
  return '数据库'
}

/**
 * 从需求说明书的 API 契约投影计划草稿；技术规划（可选）为每个接口补上数据实现意向。
 * 不在需求之外自动创建接口。
 */
export function createAppApis(
  spec: Record<string, unknown>,
  technicalPlan?: Record<string, unknown>
): AppApi[] {
  const apis = Array.isArray(spec.apis) ? (spec.apis as Array<Record<string, unknown>>) : []
  const pageNameById = new Map<string, string>(
    (Array.isArray(spec.pages) ? (spec.pages as Array<Record<string, unknown>>) : []).map(
      (page) => [String(page.pageId || ''), String(page.name || '')]
    )
  )
  const intentsById = new Map<string, string>(
    (Array.isArray(technicalPlan?.apis)
      ? (technicalPlan.apis as Array<Record<string, unknown>>)
      : []
    ).map((item) => [String(item.id || ''), String(item.data_intent || '')])
  )
  const sources = readDataSources()
  return apis.map((api, index) => {
    const id = String(api.id || `api-${index + 1}`)
    // 契约出参：新契约声明 code+name 对；历史记录里的纯中文名条目按无标识归一。
    const response = Array.isArray(api.response)
      ? (api.response as unknown[])
          .map((item) =>
            item && typeof item === 'object'
              ? {
                  code: String((item as Record<string, unknown>).code || ''),
                  name: String((item as Record<string, unknown>).name || '')
                }
              : { code: '', name: String(item) }
          )
          .filter((item) => item.name)
      : []
    // 契约入参：需求文档定稿后即为只读契约面，开发阶段的模板槽位与参数适配都以它为起点。
    const request = Array.isArray(api.request)
      ? (api.request as Array<Record<string, unknown>>)
          .map((item) => ({
            code: String(item.code || ''),
            name: String(item.name || ''),
            required: Boolean(item.required),
            summary: String(item.summary || '')
          }))
          .filter((item) => item.name)
      : []
    const pages = (Array.isArray(api.used_by_pages) ? api.used_by_pages : [])
      .map((pageId) => pageNameById.get(String(pageId)) || String(pageId))
      .filter(Boolean)
    const intent = intentsById.get(id) || ''
    const intentSources = intent ? [intent] : []
    return {
      id,
      name: String(api.name || `接口 ${index + 1}`),
      description: String(api.summary || ''),
      method: String(api.method || 'GET').toUpperCase(),
      path: String(api.path || ''),
      request,
      response,
      pages,
      implementation: {
        ...emptyImplementation(kindForIntents(intentSources, sources)),
        intentSources
      }
    }
  })
}

/** 一个契约返回字段与绑定表列的对应结果；matched=false 表示需人工确认。 */
export type ObjectFieldMatch = {
  field: string
  columnName: string
  columnComment: string
  matched: boolean
}

/** 返回字段与表列的相似度评分：注释与字段名同义自动确认，前后缀关系视为疑似。 */
function scoreFieldToColumn(field: string, column: { name: string; comment: string }): number {
  if (!column.comment) return 0
  if (column.comment === field) return 100
  if (column.comment.includes(field) || field.includes(column.comment)) return 70
  if (field.length >= 2 && column.comment.startsWith(field)) return 60
  return 0
}

/** 把契约返回字段映射到绑定表的列：最高分列作为对应列，低于疑似阈值的保持待确认。 */
export function matchObjectFields(
  fields: Array<{ name: string }>,
  columns: Array<{ name: string; comment: string }>
): ObjectFieldMatch[] {
  return fields.map((field) => {
    const ranked = columns
      .map((column) => ({ column, value: scoreFieldToColumn(field.name, column) }))
      .sort((left, right) => right.value - left.value)
    const best = ranked[0]
    return {
      field: field.name,
      columnName: best && best.value > 0 ? best.column.name : '',
      columnComment: best && best.value > 0 ? best.column.comment : '',
      matched: (best?.value ?? 0) >= 60
    }
  })
}

/* ------------------------------ 实现摘要与匹配推导 ------------------------------ */

/** 目录中仍存在的绑定目标；返回被移除来源的显示名，供“来源已删除”警示。 */
export function missingBindingSources(
  implementation: Implementation,
  sources: DataSource[]
): string[] {
  if (!implementation.bindings.length) return []
  const targets = new Set(flattenTargets(sources).map((target) => target.key))
  return implementation.bindings
    .filter(
      (binding) =>
        binding.sourceId !== 'local' &&
        !targets.has(bindingKey(binding.sourceId, binding.targetName))
    )
    .map((binding) => binding.sourceName)
}

/** 生成实现状态的简短摘要，用于接口详情、产物树与详情卡。 */
export function implementationSummary(implementation: Implementation): string {
  if (implementation.kind === '本地实现') return localImplementationName
  if (implementation.bindings.length) {
    return implementation.bindings
      .map((binding) =>
        binding.targetName ? `${binding.sourceName} · ${binding.targetName}` : binding.sourceName
      )
      .join(' + ')
  }
  if (implementation.intentSources.length) {
    return `意向 · ${implementation.intentSources.join(' + ')}`
  }
  return ''
}

/** 字段级映射的单个候选：结构化信息 + 下拉显示文本。 */
export type FieldCandidate = {
  label: string
  name: string
  comment: string
  sourceName: string
}

/** 汇总绑定目标下的全部候选字段，字段级映射下拉与 AI 匹配共用这一来源。 */
export function fieldCandidates(
  implementation: Implementation,
  sources: DataSource[]
): FieldCandidate[] {
  if (implementation.kind === '本地实现') return []
  const targetByKey = new Map(flattenTargets(sources).map((target) => [target.key, target]))
  return implementation.bindings.flatMap((binding) => {
    const target =
      binding.sourceId === 'local'
        ? undefined
        : targetByKey.get(bindingKey(binding.sourceId, binding.targetName))
    if (!target) return []
    return target.fields.map((field) => ({
      label: `${target.sourceName} · ${field.name}（${field.comment}）`,
      name: field.name,
      comment: field.comment,
      sourceName: target.sourceName
    }))
  })
}

/** 字段与候选说明的相似度评分：完全相等 > 一方包含对方 > 字面无关。 */
function scoreCandidate(field: string, candidateComment: string, candidateName: string): number {
  if (!candidateComment) return 0
  if (candidateComment === field) return 100
  if (candidateComment.includes(field) || field.includes(candidateComment)) return 70
  // “申请人”与“申请人工号”这类前缀关系视为疑似匹配，交给人工确认。
  if (field.length >= 2 && candidateComment.startsWith(field)) return 60
  if (candidateName === field) return 50
  return 0
}

/** 模拟 AI 自动匹配：按候选评分填充映射；多候选疑似冲突时保留待确认状态。 */
export function autoMatchFields(
  outputs: string[],
  implementation: Implementation,
  sources: DataSource[]
): FieldMapping[] {
  if (implementation.kind === '本地实现') {
    return outputs.map((field) => ({
      field,
      matched: true,
      matchType: 'auto' as const,
      sourceLabel: `业务规则 · ${field}`
    }))
  }
  const candidates = fieldCandidates(implementation, sources)
  return outputs.map((field) => {
    const scored = candidates
      .map((candidate) => ({
        candidate,
        score: scoreCandidate(field, candidate.comment, candidate.name)
      }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score)
    if (!scored.length) {
      return { field, matched: false, matchType: 'auto' as const, sourceLabel: '' }
    }
    // 最高分出现并列（疑似歧义）时保持待确认，由用户在候选中手动选择。
    const ambiguous = scored.length > 1 && scored[0].score === scored[1].score
    return {
      field,
      matched: !ambiguous && scored[0].score >= 70,
      matchType: 'auto' as const,
      sourceLabel: ambiguous ? '' : scored[0].candidate.label
    }
  })
}

/* ------------------------------ 开发阶段绑定确认 ------------------------------ */

/** 按实现类型为接口落实结构化绑定：已有绑定保留，意向按目录来源名落位。 */
function resolveInterfaceBindings(object: AppApi, sources: DataSource[]): SourceBinding[] {
  const existing = object.implementation.bindings
  if (existing.length) return existing
  if (object.implementation.kind === '本地实现') return [{ ...localSourceBinding }]
  const targets = flattenTargets(sources)
  const resolved = object.implementation.intentSources
    .map((intent) => targets.find((target) => target.sourceName === intent))
    .filter((target): target is BindableTarget => Boolean(target))
    .map((target) => ({
      sourceId: target.sourceId,
      sourceName: target.sourceName,
      targetName: target.targetName,
      targetComment: target.targetComment
    }))
  if (resolved.length) return resolved
  // 目录缺失时的兜底：按本地实现交付。
  return [{ ...localSourceBinding }]
}

/** 两个说明文本的公共前缀长度：「回检单号」与「回检编号」共享「回检」，用于确认时的推荐回填。 */
function commonPrefixLength(left: string, right: string): number {
  let length = 0
  while (length < left.length && length < right.length && left[length] === right[length]) {
    length += 1
  }
  return length
}

/** 两个说明文本的公共后缀长度：「创建时间」与「提交时间」共享「时间」，避免推荐回填偏向编号列。 */
function commonSuffixLength(left: string, right: string): number {
  let length = 0
  while (
    length < left.length &&
    length < right.length &&
    left[left.length - 1 - length] === right[right.length - 1 - length]
  ) {
    length += 1
  }
  return length
}

/** 确认绑定时的字段映射定稿：AI 自动匹配为基础，人工已选结果原样保留，疑似与无关字段按公共前缀推荐回填，保证确认后全部字段就绪。 */
function confirmedFieldMappings(
  outputs: string[],
  implementation: Implementation,
  sources: DataSource[],
  existing: FieldMapping[]
): FieldMapping[] {
  const mappings = autoMatchFields(outputs, implementation, sources)
  const candidates = fieldCandidates(implementation, sources)
  return mappings.map((mapping) => {
    // 用户在配置面板已人工选定的来源优先：确认只收口，不覆盖人工调整。
    const manual = existing.find((item) => item.field === mapping.field && item.matched)
    if (manual) return manual
    if (mapping.matched) return mapping
    const recommended =
      candidates
        .map((candidate) => ({
          candidate,
          overlap: Math.max(
            commonPrefixLength(mapping.field, candidate.comment),
            commonSuffixLength(mapping.field, candidate.comment)
          )
        }))
        .sort((left, right) => right.overlap - left.overlap)
        .find((item) => item.overlap >= 2)?.candidate || candidates[0]
    if (!recommended) {
      return {
        ...mapping,
        matched: true,
        matchType: 'manual' as const,
        sourceLabel: `业务规则 · ${mapping.field}`
      }
    }
    return {
      field: mapping.field,
      matched: true,
      matchType: 'manual' as const,
      sourceLabel: `${recommended.sourceName} · ${recommended.name}（${recommended.comment}）`
    }
  })
}

/** 来源选定后的绑定落位：写入目标绑定并按契约填好模板槽位，字段映射先由 AI 初步匹配，精调留给配置面板。 */
export function withSelectedSource(
  object: AppApi,
  target: BindableTarget,
  sources: DataSource[]
): AppApi {
  const kind: ImplementationKind = target.sourceKind === 'external_service' ? '外部服务' : '数据库'
  const binding: SourceBinding = {
    sourceId: target.sourceId,
    sourceName: target.sourceName,
    targetName: target.targetName,
    targetComment: target.targetComment
  }
  const implementation: Implementation = {
    ...emptyImplementation(kind),
    kind,
    intentSources: [target.sourceName],
    bindings: [binding],
    matched: true
  }
  const draft = { ...object, implementation }
  const detail = bindingDetailForImplementation(draft, sources)
  const withDetail: Implementation = { ...implementation, ...detail }
  return {
    ...object,
    implementation: {
      ...withDetail,
      mappings: autoMatchFields(
        object.response.map((output) => output.name),
        withDetail,
        sources
      ),
      expressions: recommendAdaptationExpressions(draft, sources)
    }
  }
}

/** 映射绑定草稿：对话卡「配置映射绑定」节点承载的可编辑配置，确认时一次性写回实现结构。 */
export type BindingDraft = {
  /** 模板操作类型（数据库绑定）。 */
  op: TableOpKind | ''
  conditions: TemplateConditionRow[]
  setters: TemplateSetterRow[]
  orderBy: string
  mappings: FieldMapping[]
  expressions: Record<string, string>
  requestParamMap: Record<string, string>
}

/** 从当前实现提取映射绑定草稿：对话卡初始值与历史回放预置应答共用同一来源。 */
export function bindingDraftFrom(object: AppApi): BindingDraft {
  const implementation = object.implementation
  return {
    op: implementation.tableOp || '',
    conditions: implementation.conditions.map((row) => ({ ...row })),
    setters: implementation.setters.map((row) => ({ ...row })),
    orderBy: implementation.orderBy,
    mappings: implementation.mappings.map((row) => ({ ...row })),
    expressions: { ...implementation.expressions },
    requestParamMap: { ...implementation.requestParamMap }
  }
}

/** 把对话卡确认的映射草稿并入实现并收口：已人工确定的映射原样保留，缺口由 AI 推荐回填。 */
export function withAppliedBindingDraft(
  object: AppApi,
  draft: BindingDraft,
  sources: DataSource[]
): AppApi {
  const merged = withSavedBindingDraft(object, draft)
  return withConfirmedBindings(merged, sources)
}

/**
 * 已确认绑定的映射视图：字段映射面板在绑定完成后以只读态常驻展示配置，
 * 不随工作流结束而消失。结构上与「配置映射绑定」澄清载荷同形，草稿从实现提取。
 */
export function confirmedBindingView(
  object: AppApi,
  sources: DataSource[]
): {
  kind: ImplementationKind
  sourceName: string
  targetName: string
  op: TableOpKind | ''
  columns: Array<{ name: string; comment: string }>
  requestParams: Array<{ name: string; comment: string; required?: boolean; location?: ExternalApiParamLocation }>
  inputParams: ContractParam[]
  outputs: ContractOutput[]
  draft: BindingDraft
} | null {
  const implementation = object.implementation
  const binding = implementation.bindings[0]
  if (!binding || binding.sourceId === 'local') return null
  const target = flattenTargets(sources).find(
    (item) => item.sourceId === binding.sourceId && item.targetName === binding.targetName
  )
  return {
    kind: implementation.kind,
    sourceName: binding.sourceName,
    targetName: binding.targetName,
    op: implementation.tableOp || '',
    columns: target ? target.fields : [],
    requestParams: target ? target.requestParams : [],
    inputParams: contractRequestParams(object),
    outputs: object.response,
    draft: bindingDraftFrom(object)
  }
}

/** 面板「保存」：把当前草稿写回实现但不收口确认——工作流仍停在待确认，重开面板读到的就是这份草稿。 */
export function withSavedBindingDraft(object: AppApi, draft: BindingDraft): AppApi {
  return {
    ...object,
    implementation: {
      ...object.implementation,
      tableOp: draft.op || undefined,
      conditions: draft.conditions,
      setters: draft.setters,
      orderBy: draft.orderBy,
      mappings: draft.mappings,
      expressions: draft.expressions,
      requestParamMap: draft.requestParamMap
    }
  }
}

/**
 * 应用API开发工作流的绑定确认：把计划意向一次性落实为结构化绑定与字段映射。
 * 由对话区「确认绑定」卡触发；确认后接口交付就绪，右侧面板按确认结果静态呈现。
 */
export function withConfirmedBindings(object: AppApi, sources: DataSource[]): AppApi {
  if (object.implementation.confirmed) return object
  const bindings = resolveInterfaceBindings(object, sources)
  const implementation: Implementation = {
    ...emptyImplementation(object.implementation.kind),
    kind: object.implementation.kind,
    intentSources: object.implementation.intentSources,
    rule: object.implementation.rule,
    expressions: object.implementation.expressions,
    requestParamMap: object.implementation.requestParamMap,
    bindings,
    matched: true
  }
  const merged = { ...object, implementation }
  // 模板槽位若已由对话卡草稿带入（人工调整过的条件/写入行），确认时原样保留，不再重新推导。
  const detail = bindingDetailForImplementation(merged, sources)
  const hasDraftTemplate =
    merged.implementation.conditions.length > 0 ||
    merged.implementation.setters.length > 0 ||
    Boolean(merged.implementation.orderBy) ||
    Boolean(merged.implementation.tableOp)
  const template = hasDraftTemplate
    ? {
        tableOp: merged.implementation.tableOp,
        conditions: merged.implementation.conditions,
        setters: merged.implementation.setters,
        orderBy: merged.implementation.orderBy
      }
    : detail
  return {
    ...object,
    implementation: {
      ...implementation,
      ...template,
      mappings: confirmedFieldMappings(
        object.response.map((output) => output.name),
        implementation,
        sources,
        object.implementation.mappings
      ),
      expressions: recommendAdaptationExpressions(merged, sources),
      confirmed: true
    }
  }
}

/* ------------------------------ 模板与参数适配推导 ------------------------------ */

/** 提取契约路径中的占位参数：`/api/rechecks/{id}/reviewer` → ['id']。 */
export function contractPathParams(path: string): string[] {
  const params: string[] = []
  const pattern = /\{([^}]+)\}/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(path))) params.push(match[1].trim())
  return params.filter(Boolean)
}

/** 契约入参全集：需求文档登记的入参 + 路径占位参数（未登记时按路径合成）。 */
export function contractRequestParams(object: AppApi): ContractParam[] {
  const registered = new Set(object.request.map((param) => param.name))
  const synthesized = contractPathParams(object.path)
    .filter((name) => !registered.has(name))
    .map((name) => ({ code: name, name, required: true, summary: `路径参数 {${name}}` }))
  return [...object.request, ...synthesized]
}

/**
 * 按接口功能自动判定数据库绑定套用的增删查改模板：
 * HTTP 方法是强信号，名称/用途中的动词做补充；查询类措辞优先于“提交”等背景词。
 */
export function deriveTableOp(object: AppApi): TableOpKind {
  const text = `${object.name}${object.description}`
  if (object.method === 'DELETE' || /删除|移除/.test(text)) return '删除'
  if (object.method === 'PUT' || object.method === 'PATCH' || /修改|更新|变更/.test(text)) {
    return '修改'
  }
  if (object.method === 'POST' && !/查询|查看|列表|搜索/.test(text)) return '新增'
  if (/新增|创建|提交|登记/.test(text) && !/查询|查看|列表/.test(text)) return '新增'
  return '查询'
}

/** 按业务术语挑列：复用注释相似度评分，返回得分最高的列。 */
function columnForTerm(
  term: string,
  columns: Array<{ name: string; comment: string }>
): { column: string; columnComment: string } | undefined {
  const ranked = columns
    .map((column) => ({ column, value: scoreFieldToColumn(term, column) }))
    .sort((left, right) => right.value - left.value)
  const best = ranked[0]
  return best && best.value > 0
    ? { column: best.column.name, columnComment: best.column.comment }
    : undefined
}

/** 「我的/本人/自己」类契约语义 → 登录态固定条件的触发词。 */
const LOGIN_SCOPE_PATTERN = /我的|本人|自己/

/** 表绑定模板推导结果：操作类型 + 槽位行 + 自动排序。 */
export type TableTemplate = {
  op: TableOpKind
  conditions: TemplateConditionRow[]
  setters: TemplateSetterRow[]
  orderBy: string
}

/**
 * 数据库绑定的固定增删查改模板：把契约出入参按语义填进模板槽位——
 * 条件/写入行由列注释匹配自动生成，「我的」类接口追加登录态固定条件，
 * 查询模板再按出参中的时间列建议倒序排序。槽位填好后人工只需逐项确认。
 */
export function buildTableTemplate(object: AppApi, sources: DataSource[]): TableTemplate {
  const op = deriveTableOp(object)
  const empty: TableTemplate = { op, conditions: [], setters: [], orderBy: '' }
  const binding = object.implementation.bindings[0]
  if (!binding || binding.sourceId === 'local') return empty
  const target = flattenTargets(sources).find(
    (item) => item.sourceId === binding.sourceId && item.targetName === binding.targetName
  )
  if (!target) return empty
  if (op === '查询' || op === '删除') {
    const conditions: TemplateConditionRow[] = []
    // 「我的回检」这类接口：数据范围由登录态圈定，模板追加一条当前用户固定条件。
    if (op === '查询' && LOGIN_SCOPE_PATTERN.test(`${object.name}${object.description}`)) {
      const scope = columnForTerm('申请人', target.fields) || columnForTerm('创建', target.fields)
      if (scope) {
        conditions.push({ param: '当前登录用户', fixed: true, ...scope, operator: '等于' })
      }
    }
    contractRequestParams(object).forEach((param) => {
      // 未匹配到列的契约入参也进模板（落列留空待人工选择），保证出入参映射完整可配。
      const matched = columnForTerm(param.name, target.fields)
      conditions.push({
        param: param.name,
        fixed: false,
        column: matched?.column || '',
        columnComment: matched?.columnComment || '',
        operator: '等于'
      })
    })
    return {
      op,
      conditions,
      setters: [],
      orderBy: op === '查询' ? suggestOrderBy(object, target.fields) : ''
    }
  }
  // 新增/修改模板：契约入参逐个落到写入列，未匹配到列的留空待人工选择。
  const setters = object.request.map((param) => {
    const matched = columnForTerm(param.name, target.fields)
    return {
      param: param.name,
      column: matched?.column || '',
      columnComment: matched?.columnComment || '',
      required: param.required
    }
  })
  return { op, conditions: [], setters, orderBy: '' }
}

/** 查询模板的自动排序：契约出参对应列中的时间列倒序，让最新记录排在最前。 */
function suggestOrderBy(
  object: AppApi,
  columns: Array<{ name: string; comment: string }>
): string {
  const timeColumn = object.response
    .map((output) => columnForTerm(output.name, columns))
    .find((item) => item && /时间/.test(item.columnComment))
  return timeColumn ? `${timeColumn.column} DESC` : ''
}

/** 一条参数适配行：契约侧与外部侧如何互译；出参适配待人工确认时 external 为空。 */
export type AdaptationRow = {
  direction: '入参适配' | '出参适配'
  /** 契约侧名称：入参适配为契约入参，出参适配为契约出参。 */
  param: string
  /** 契约侧业务含义（仅入参适配提供）。 */
  paramSummary: string
  required: boolean
  /** 外部侧名称；出参适配未匹配时为空字符串。 */
  external: string
  externalComment: string
  /** 入参适配的外部请求部位：路径参数/查询参数/请求体；出参适配为空。 */
  location: ExternalApiParamLocation | ''
  /** 该行是否已确定来源；入参适配随对齐结果，出参适配随字段映射状态。 */
  matched: boolean
}

/** 适配行的表达式存储键：入参/出参分开前缀，避免同名互相覆盖。 */
export function adaptationExpressionKey(row: AdaptationRow): string {
  return `${row.direction === '入参适配' ? 'in' : 'out'}:${row.param}`
}

/** 从字段映射标签还原来源字段名：兼容「来源 · name（说明）」与对话卡草稿的简写「name（说明）」。 */
function externalNameFromMapping(sourceLabel: string): string {
  const prefixed = sourceLabel.match(/· (.+?)（/)
  if (prefixed) return prefixed[1]
  const short = sourceLabel.match(/^(.+?)（/)
  return short ? short[1] : ''
}

/**
 * 外部服务绑定的参数适配视图：入参适配把契约入参对齐到外部接口入参
 * （人工在 requestParamMap 登记的对齐优先，其次按名称匹配；语义对不上就留空待人工
 * 选择，不做“唯一入参硬凑”——部位与含义都由用户判断）；出参适配直接读取字段映射
 * 的确认结果，保证与「确认绑定」状态和生成的适配代码一致。
 */
export function externalAdaptations(object: AppApi, sources: DataSource[]): AdaptationRow[] {
  if (object.implementation.kind !== '外部服务') return []
  const target = flattenTargets(sources).find(
    (item) =>
      item.sourceKind === 'external_service' &&
      object.implementation.bindings.some(
        (binding) => binding.sourceId === item.sourceId && binding.targetName === item.targetName
      )
  )
  if (!target) return []
  const requestRows: AdaptationRow[] = contractRequestParams(object).map((param) => {
    const manual = object.implementation.requestParamMap?.[param.name]
    const matched =
      (manual ? target.requestParams.find((candidate) => candidate.name === manual) : undefined) ||
      target.requestParams.find((candidate) => candidate.name === param.name) ||
      undefined
    return {
      direction: '入参适配',
      param: param.name,
      paramSummary: param.summary,
      required: param.required || Boolean(matched?.required),
      external: matched ? matched.name : '',
      externalComment: matched
        ? matched.comment
        : '尚未对齐外部入参，请在字段映射面板选择',
      location: matched?.location || '',
      matched: Boolean(matched)
    }
  })
  const responseRows: AdaptationRow[] = object.response.map((output) => {
    const mapping = object.implementation.mappings.find((item) => item.field === output.name)
    const externalField = mapping?.matched
      ? target.fields.find((item) => item.name === externalNameFromMapping(mapping.sourceLabel))
      : undefined
    return {
      direction: '出参适配',
      param: output.name,
      paramSummary: '',
      required: false,
      external: externalField?.name || '',
      externalComment: externalField?.comment || '',
      location: '',
      matched: Boolean(externalField)
    }
  })
  return [...requestRows, ...responseRows]
}

/** 判断两段业务含义是否指同一件事：去掉“路径参数”等套话后看有无二字片段重合。 */
function termsOverlap(left: string, right: string): boolean {
  const clean = (text: string): string =>
    text.replace(/路径参数|请求参数|入参|出参|参数|必填|可选|[，,。·()（）\s]/g, '')
  const source = clean(left)
  const target = clean(right)
  if (!source || !target) return true
  for (let index = 0; index + 2 <= source.length; index += 1) {
    if (target.includes(source.slice(index, index + 2))) return true
  }
  return false
}

/**
 * 语义不一致的适配由 AI 推荐一条函数表达式做简单加工。演示启发两类：
 * 入参「回检单号 → 员工工号」这类跨域取数——按回检单号在审核轨迹表定位审核人工号；
 * 出参「申请人」拿到的是工号——按工号回查员工表译成姓名。已有人工表达式时不覆盖。
 */
export function recommendAdaptationExpressions(
  object: AppApi,
  sources: DataSource[]
): Record<string, string> {
  const seeded: Record<string, string> = { ...object.implementation.expressions }
  externalAdaptations(object, sources).forEach((row) => {
    const key = adaptationExpressionKey(row)
    if (seeded[key]) return
    if (row.direction === '入参适配') {
      if (termsOverlap(row.paramSummary, row.externalComment)) return
      if (/回检/.test(row.paramSummary) && /工号|员工/.test(row.externalComment)) {
        seeded[key] = 'LOOKUP(recheck_audit, recheck_id, reviewer_id)'
      }
      return
    }
    // 出参适配：来源是工号而契约要姓名/申请人时，回查员工表翻译。
    if (!row.matched) return
    if (/申请人|姓名/.test(row.param) && /工号/.test(row.externalComment)) {
      seeded[key] = 'LOOKUP(user, id, name)'
    }
  })
  return seeded
}

/** 按绑定结果补齐结构化细节：数据库填增删查改模板槽位，外部服务由渲染期适配推导。 */
export function bindingDetailForImplementation(
  object: AppApi,
  sources: DataSource[]
): Pick<Implementation, 'tableOp' | 'conditions' | 'setters' | 'orderBy'> {
  if (object.implementation.kind !== '数据库') {
    return { tableOp: undefined, conditions: [], setters: [], orderBy: '' }
  }
  const template = buildTableTemplate(object, sources)
  return {
    tableOp: template.op,
    conditions: template.conditions,
    setters: template.setters,
    orderBy: template.orderBy
  }
}
