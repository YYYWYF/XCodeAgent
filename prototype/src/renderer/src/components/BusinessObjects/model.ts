import {
  bindingKey,
  flattenTargets,
  type BindableTarget,
  type DataSource
} from '../DataSources/catalog'

/**
 * 实体数据层模型 v3。
 * 计划阶段只记录“实现意向”（类型 + 来源级选择）；开发阶段完成“绑定映射”
 * （库表/服务接口粒度 + 字段级映射 + 确认），两段状态共用同一 Implementation 结构。
 */

export type ImplementationKind = '数据库' | '外部服务' | '本地实现' | '多来源组合'
export type BusinessField = { name: string; type: string; required: boolean }

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

export type Implementation = {
  kind: ImplementationKind
  /** 计划阶段的来源级意向（显示名，如“武汉回检数据库”）。 */
  intentSources: string[]
  /** 开发阶段的结构化绑定；意向阶段为空数组。 */
  bindings: SourceBinding[]
  /** 字段级映射；AI 匹配后生成，人工调整覆盖。 */
  mappings: FieldMapping[]
  /** 本地实现/多来源组合的规则说明。 */
  rule: string
  matched: boolean
  confirmed: boolean
}

export type BusinessOperation = {
  id: string
  name: string
  description: string
  inputs: string[]
  outputs: string[]
  pages: string[]
  /** 内置操作由平台为每个对象提供；自定义操作来自需求与业务流程。 */
  operationType: 'builtin' | 'custom'
  implementation: Implementation
}

/**
 * 对象的数据来源类别（实体开发第一步）：
 * 纯数据库（单表）→ 绑表后模板化生成内置方法；纯外部 API → 按业务自定义方法逐个绑定固定契约；
 * 混合 / 多表联查 → 高级自定义（SQL/Java 自由编排），平台不做可视化映射。
 */
export type SourceCategory = 'database' | 'external_api' | 'mixed'

export type BusinessObject = {
  id: string
  name: string
  description: string
  fields: BusinessField[]
  operations: BusinessOperation[]
  /** 数据来源类别；决定内置方法是否存在以及方法页的绑定形态。 */
  sourceCategory: SourceCategory
  /** 类别为数据库时的实体级表绑定；外部 API 不做实体级绑定，在方法级逐个绑定接口。 */
  tableBinding?: SourceBinding
}

/** 生成实体在开发产物目录中的稳定身份。 */
export function businessObjectArtifactId(objectId: string): string {
  return `business-object:${objectId}`
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
    rule: '',
    matched: false,
    confirmed: false
  }
}

/** 生成演示操作；每个操作独立持有数据实现，不在对象上绑定唯一来源。 */
export function makeOperation(
  id: string,
  name: string,
  kind: ImplementationKind,
  bindings: SourceBinding[],
  outputs: string[],
  pages: string[] = [],
  pending = false,
  operationType: BusinessOperation['operationType'] = 'custom'
): BusinessOperation {
  return {
    id,
    name,
    description: `为页面提供${name}的业务能力`,
    inputs: ['当前用户', '业务编号'],
    outputs,
    pages,
    operationType,
    implementation: {
      kind,
      intentSources: [],
      bindings,
      mappings: [],
      rule:
        kind === '本地实现'
          ? '校验输入 → 执行业务规则 → 返回结果'
          : kind === '多来源组合'
            ? '按业务编号关联各来源，汇总为本操作的返回结果'
            : '',
      matched: !pending,
      confirmed: !pending
    }
  }
}

/** 演示用固定绑定：来源目录默认数据中的库表与服务接口。 */
const BINDINGS = {
  wuhanRecheck: (table: string, comment: string): SourceBinding => ({
    sourceId: 'wuhan-recheck-db',
    sourceName: '武汉回检数据库',
    targetName: table,
    targetComment: comment
  }),
  userCenter: (): SourceBinding => ({
    sourceId: 'user-center',
    sourceName: '用户中心',
    targetName: 'GET /users/{id}',
    targetComment: '员工信息 · 查询用户信息'
  })
}

/** 创建独立的示例数据副本，关闭工作台后保留在组件内，不写入正式应用。 */
function createExampleObjects(): BusinessObject[] {
  return [
    {
      id: 'recheck',
      name: '回检单',
      description: '管理需求回检的申请、提交与审核，记录完整业务生命周期。',
      sourceCategory: 'database',
      fields: [
        { name: '回检编号', type: '文本', required: true },
        { name: '状态', type: '枚举', required: true },
        { name: '申请人', type: '文本', required: true },
        { name: '创建时间', type: '日期时间', required: true }
      ],
      operations: [
        // 演示只保留两个需求自定义方法：一个走数据库绑定，一个走外部 API 绑定。
        makeOperation(
          'my',
          '查询我的回检',
          '数据库',
          [BINDINGS.wuhanRecheck('recheck', '需求回检记录')],
          ['回检编号', '状态', '申请人', '创建时间'],
          ['我的回检'],
          true
        ),
        makeOperation(
          'reviewer',
          '查询审核人信息',
          '外部服务',
          [BINDINGS.userCenter()],
          ['姓名', '所属部门']
        )
      ]
    }
  ]
}

/** 从当前需求说明书投影计划草稿，不在需求之外自动创建示例实体。 */
export function createBusinessObjects(spec: Record<string, unknown>): BusinessObject[] {
  const entities = Array.isArray(spec.entities)
    ? (spec.entities as Array<Record<string, unknown>>)
    : []
  const examples = createExampleObjects()
  return entities.map((entity) => {
    const example = examples.find((item) =>
      String(entity.name).includes(item.name === '回检单' ? '回检' : item.name)
    )
    const rawFields = Array.isArray(entity.fields)
      ? (entity.fields as Array<Record<string, unknown>>)
      : []
    const fields: BusinessField[] = rawFields.map((field) => ({
      name: String(field.label || field.name),
      type: field.type === 'number' ? '数字' : '文本',
      required: field.required === true
    }))
    const descriptions: string[] = Array.isArray(entity.business_operations)
      ? entity.business_operations
          .map((value: unknown) =>
            typeof value === 'string'
              ? value
              : String((value as Record<string, unknown>)?.name || '')
          )
          .filter((value: string) => value.trim())
      : []
    const requirementOperations = descriptions.map((description, index) => {
      const name = description.split(/[：:]/)[0].trim().replace(/\(\)$/, '')
      const preset = example?.operations.find((item) => item.name === name)
      const operation =
        preset ||
        makeOperation(
          `${entity.id}-op-${index}`,
          name,
          '数据库',
          [],
          fields.map((field) => field.name)
        )
      return {
        ...operation,
        name,
        description,
        operationType: name === '查询回检详情' ? ('builtin' as const) : ('custom' as const),
        // 已有演示操作保留自身返回结果，避免“查询审核人”错误返回回检单全部字段。
        outputs: preset ? preset.outputs : fields.map((field) => field.name),
        implementation: {
          ...operation.implementation,
          // 需求→计划投影只保留来源级意向；表级/接口级绑定与字段映射由开发阶段重新完成。
          intentSources: [
            ...new Set(operation.implementation.bindings.map((binding) => binding.sourceName))
          ],
          bindings: [],
          confirmed: false,
          matched: false,
          mappings: []
        }
      }
    })
    const existingDetail = requirementOperations.find(
      (operation) => operation.name === '查询回检详情'
    )
    const builtins = makeBuiltinOperations(String(entity.id), fields)
    // 需求里若明确描述了“查询回检详情”，用它替换通用的查询详情内置方法。
    const builtinOperations = existingDetail
      ? builtins.map((operation) =>
          operation.name === '查询详情'
            ? {
                ...existingDetail,
                id: `${entity.id}-builtin-detail`,
                operationType: 'builtin' as const
              }
            : operation
        )
      : builtins
    const operations = [
      ...builtinOperations,
      ...requirementOperations.filter((operation) => operation !== existingDetail)
    ]
    return {
      id: String(entity.id),
      name: String(entity.name),
      description: String(entity.description || ''),
      fields,
      operations,
      // 演示对象默认按数据库类别落位，并绑定与案例配套的库表。
      sourceCategory: 'database',
      tableBinding: example ? BINDINGS.wuhanRecheck('recheck', '需求回检记录') : undefined
    }
  })
}

/** 平台内置方法的固定文案。 */
const BUILTIN_DESCRIPTIONS: Record<string, string> = {
  分页查询: '按条件分页读取实体列表，内置排序与筛选能力。',
  查询回检详情: '按回检单号读取实体完整字段。',
  查询详情: '按实体编号读取完整字段。',
  新增: '创建一条新的实体记录。',
  更新: '更新允许修改的业务字段。',
  删除: '按业务规则删除一条实体记录。'
}

/** 平台为数据库对象按表结构模板化生成的一组内置方法（分页查询/查询详情/新增/更新/删除）。 */
export function makeBuiltinOperations(
  entityId: string,
  fields: BusinessField[]
): BusinessOperation[] {
  const outputs = fields.map((field) => field.name)
  return [
    makeOperation(
      `${entityId}-builtin-page`,
      '分页查询',
      '数据库',
      [],
      outputs,
      [],
      true,
      'builtin'
    ),
    makeOperation(
      `${entityId}-builtin-detail`,
      '查询详情',
      '数据库',
      [],
      outputs,
      [],
      true,
      'builtin'
    ),
    makeOperation(
      `${entityId}-builtin-create`,
      '新增',
      '数据库',
      [],
      ['新增结果'],
      [],
      true,
      'builtin'
    ),
    makeOperation(
      `${entityId}-builtin-update`,
      '更新',
      '数据库',
      [],
      ['更新结果'],
      [],
      true,
      'builtin'
    ),
    makeOperation(
      `${entityId}-builtin-delete`,
      '删除',
      '数据库',
      [],
      ['删除结果'],
      [],
      true,
      'builtin'
    )
  ].map((operation) => ({
    ...operation,
    description: BUILTIN_DESCRIPTIONS[operation.name] || operation.description
  }))
}

/** 一个对象字段与绑定表列的对应结果；matched=false 表示需人工确认。 */
export type ObjectFieldMatch = {
  field: string
  columnName: string
  columnComment: string
  matched: boolean
}

/** 对象字段与表列的相似度评分：注释与字段名同义自动确认，前后缀关系视为疑似。 */
function scoreFieldToColumn(field: string, column: { name: string; comment: string }): number {
  if (!column.comment) return 0
  if (column.comment === field) return 100
  if (column.comment.includes(field) || field.includes(column.comment)) return 70
  if (field.length >= 2 && column.comment.startsWith(field)) return 60
  return 0
}

/** 把对象字段映射到绑定表的列：最高分列作为对应列，低于疑似阈值的保持待确认。 */
export function matchObjectFields(
  fields: BusinessField[],
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

/** 生成实现状态的简短摘要，用于操作列表、产物树与详情卡。 */
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

/** 按实现类型为操作落实结构化绑定：已有绑定保留，意向按目录来源名落位，数据库类别回落实体级表绑定。 */
function resolveOperationBindings(
  object: BusinessObject,
  operation: BusinessOperation,
  sources: DataSource[]
): SourceBinding[] {
  const existing = operation.implementation.bindings
  if (existing.length) return existing
  if (operation.implementation.kind === '本地实现') return [{ ...localSourceBinding }]
  if (operation.implementation.kind === '数据库' && object.tableBinding) {
    return [{ ...object.tableBinding }]
  }
  const targets = flattenTargets(sources)
  const resolved = operation.implementation.intentSources
    .map((intent) => targets.find((target) => target.sourceName === intent))
    .filter((target): target is BindableTarget => Boolean(target))
    .map((target) => ({
      sourceId: target.sourceId,
      sourceName: target.sourceName,
      targetName: target.targetName,
      targetComment: target.targetComment
    }))
  if (resolved.length) return resolved
  // 目录缺失时的兜底：数据库类别回落实体级表绑定，其余按本地实现交付。
  return object.tableBinding ? [{ ...object.tableBinding }] : [{ ...localSourceBinding }]
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

/** 确认绑定时的字段映射定稿：AI 自动匹配为基础，疑似与无关字段按公共前缀推荐回填，保证确认后全部字段就绪。 */
function confirmedFieldMappings(
  outputs: string[],
  implementation: Implementation,
  sources: DataSource[]
): FieldMapping[] {
  const mappings = autoMatchFields(outputs, implementation, sources)
  const candidates = fieldCandidates(implementation, sources)
  return mappings.map((mapping) => {
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

/**
 * 实体开发工作流的绑定确认：把计划意向一次性落实为结构化绑定与字段映射。
 * 由对话区「确认绑定」卡触发；确认后实体操作全部就绪，右侧面板按确认结果静态呈现。
 */
export function withConfirmedBindings(object: BusinessObject, sources: DataSource[]): BusinessObject {
  return {
    ...object,
    operations: object.operations.map((operation) => {
      if (operation.implementation.confirmed) return operation
      const bindings = resolveOperationBindings(object, operation, sources)
      const implementation: Implementation = {
        ...emptyImplementation(operation.implementation.kind),
        kind: operation.implementation.kind,
        intentSources: operation.implementation.intentSources,
        rule: operation.implementation.rule,
        bindings,
        matched: true
      }
      return {
        ...operation,
        implementation: {
          ...implementation,
          mappings: confirmedFieldMappings(operation.outputs, implementation, sources),
          confirmed: true
        }
      }
    })
  }
}

/** 实体绑定计划行：对话区确认卡按行展示每个操作的数据实现去向与字段映射规模。 */
export type EntityBindingPlanRow = {
  name: string
  operationType: 'builtin' | 'custom'
  kind: ImplementationKind
  target: string
  fields: number
}

/** 生成确认卡用的绑定计划：按当前意向推导绑定目标，与确认后的写回逻辑共用同一解析。 */
export function entityBindingPlan(
  object: BusinessObject,
  sources: DataSource[]
): EntityBindingPlanRow[] {
  return object.operations.map((operation) => {
    const bindings = resolveOperationBindings(object, operation, sources)
    const target = bindings
      .map((binding) =>
        binding.targetName ? `${binding.sourceName} · ${binding.targetName}` : binding.sourceName
      )
      .join(' + ')
    return {
      name: operation.name,
      operationType: operation.operationType,
      kind: operation.implementation.kind,
      target: target || '业务规则推导',
      fields: operation.outputs.length
    }
  })
}
