import type { FormalArtifactKey, InitializationPlanningRecord } from './model'
import { invalidatePlanningArtifacts } from './state'
import { synchronizePlanningReferences } from './seed'

export const PLANNING_TITLES: Record<FormalArtifactKey, string> = {
  'requirement-spec': '需求规格',
  'product-plan': '产品规划',
  'ui-designs': 'UI 设计稿',
  'technical-plan': '技术规划方案'
}
const artifactFields = {
  'requirement-spec': 'requirementSpec',
  'product-plan': 'productPlan',
  'ui-designs': 'uiDesigns',
  'technical-plan': 'technicalPlan'
} as const
const labels: Record<string, string> = {
  business_operations: '需要支持的业务操作',
  app_info: '应用目标',
  app: '应用目标',
  name: '名称',
  description: '说明',
  summary: '概述',
  user_roles: '用户角色',
  pages: '页面',
  business_flows: '业务流程',
  steps: '流程步骤',
  permissions: '职责与权限',
  acceptance_criteria: '验收标准',
  product_acceptance_criteria: '产品验收标准',
  assumptions: '前提条件',
  information_items: '信息项',
  label: '名称',
  goal: '页面目标',
  path: '路径',
  actions: '用户动作',
  behavior: '操作行为',
  expectedResult: '预期结果',
  state_requirements: '页面状态',
  loading: '加载中',
  empty: '无数据',
  error: '失败',
  success: '成功',
  validation: '输入校验',
  architecture: '技术架构',
  frontend: '前端',
  backend: '后端',
  data: '数据',
  entities: '实体',
  fields: '字段',
  api_contracts: 'API 契约',
  endpoints: '接口',
  schemas: '数据结构',
  properties: '属性',
  change_request: '本次调整要求',
  business_constraints: '业务约束',
  target_audience: '目标用户',
  references: '页面实现绑定',
  endpoint_dependencies: '接口依赖',
  action_implementations: '动作实现',
  method: '请求方法',
  base_path: '基础路径',
  response_schema_ref: '响应结构',
  request_schema_ref: '请求结构',
  endpointId: '接口引用',
  actionId: '动作标识',
  sections: '内容区域',
  template: '版式'
}
const internalFields = new Set([
  'schema_version',
  'confirmation_status',
  'pageId',
  'itemId',
  'actionId',
  'id',
  'page_key',
  'module_id',
  'status',
  'variant',
  'accent',
  'test_case_estimate',
  'authorizationTargets',
  'navigation_targets',
  'data_source_type',
  'terminal',
  'layout'
])
const editableFields = new Set([
  'name',
  'description',
  'summary',
  'label',
  'goal',
  'expectedResult',
  'loading',
  'empty',
  'error',
  'success',
  'validation',
  'frontend',
  'backend',
  'data',
  'change_request',
  'target_audience'
])
const editableLists = new Set([
  'permissions',
  'acceptance_criteria',
  'product_acceptance_criteria',
  'assumptions',
  'business_constraints',
  'steps',
  'sections'
])

/** 生成 Markdown 并保留字段身份标记；未展示的内部结构始终留在领域记录中。 */
export function planningMarkdown(
  record: InitializationPlanningRecord,
  key: FormalArtifactKey
): string {
  const lines = [`# ${PLANNING_TITLES[key]} · ${record.applicationName}`, '']
  /** 按现有结构输出可读章节，只为可编辑的业务文本生成同步标记。 */
  function visit(value: any, path: string, field: string, depth: number): void {
    if (value === null || value === undefined || internalFields.has(field)) return
    if (typeof value !== 'object') {
      if (typeof value === 'string' && (editableFields.has(field) || editableLists.has(field))) {
        lines.push(`<!-- planning-field:${path} -->`, value, '<!-- /planning-field -->', '')
      } else lines.push(`- ${labels[field] || field}：${String(value)}`, '')
      return
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) => {
        if (typeof item === 'object')
          lines.push(
            `${'#'.repeat(Math.min(depth, 4))} ${item.name || item.label || item.pageId || item.id || `${labels[field] || field} ${index + 1}`}`,
            ''
          )
        visit(item, `${path}/${index}`, field, depth + 1)
      })
      return
    }
    Object.entries(value).forEach(([child, item]) => {
      if (internalFields.has(child)) return
      if (item && typeof item === 'object')
        lines.push(`${'#'.repeat(Math.min(depth, 4))} ${labels[child] || child}`, '')
      else if (typeof item === 'string' && editableFields.has(child))
        lines.push(`**${labels[child] || child}**`, '')
      visit(item, `${path}/${child}`, child, depth + 1)
    })
  }
  visit(record.artifacts[artifactFields[key]], '', '', 2)
  // 产品规划仅保留为需求表单的内部结构，页面行为与验收仍合并写入同一份需求说明书。
  if (key === 'requirement-spec') {
    lines.push('', '## 页面行为与验收', '')
    visit(record.artifacts.productPlan, '/productPlan', 'productPlan', 2)
  }
  return lines.join('\n')
}

/** 解析已知字段标记，拒绝丢失/重复标记，避免把编辑后的正文静默丢弃。 */
function markdownFields(markdown: string): Map<string, string> {
  const fields = new Map<string, string>()
  const expression =
    /<!-- planning-field:([^\n]+) -->\r?\n([\s\S]*?)\r?\n<!-- \/planning-field -->/g
  for (const match of markdown.matchAll(expression)) {
    if (fields.has(match[1])) throw new Error('文档包含重复字段标记，请恢复后再保存。')
    fields.set(match[1], match[2].trim())
  }
  return fields
}

/** 把用户 Markdown 修改合并回原结构，保留所有 ID、Schema 与隐藏权限信息。 */
export function applyPlanningMarkdown(
  current: InitializationPlanningRecord,
  key: FormalArtifactKey,
  markdown: string
): InitializationPlanningRecord {
  const allowed =
    key === 'technical-plan'
      ? current.stage === 'awaiting_technical_plan_confirmation'
      : key === 'ui-designs'
        ? current.stage === 'awaiting_ui_design_confirmation'
        : [
            'awaiting_requirement_document_confirmation',
            'awaiting_ui_design_confirmation',
            'awaiting_planning_stage_entry'
          ].includes(current.stage)
  if (!allowed) throw new Error('当前阶段不能编辑这份产物，请进入对应审阅阶段。')
  const baseline = planningMarkdown(current, key)
  const expected = markdownFields(baseline)
  const edited = markdownFields(markdown)
  if (expected.size !== edited.size || [...expected.keys()].some((path) => !edited.has(path)))
    throw new Error('请保留文档字段标记，在标记之间修改正文。')
  const next = structuredClone(current)
  for (const [path, value] of edited) {
    if (!value) throw new Error('正式产物的业务说明不能为空。')
    const segments = path.split('/').slice(1)
    // 合并说明书中的页面行为仍落在内部 ProductPlan，避免丢失下游 UI/技术规划方案引用。
    let owner: any =
      key === 'requirement-spec' && segments[0] === 'productPlan'
        ? (segments.shift(), next.artifacts.productPlan)
        : next.artifacts[artifactFields[key]]
    for (const part of segments.slice(0, -1)) owner = owner[part]
    owner[segments[segments.length - 1]] = value
  }
  if (key === 'requirement-spec' || key === 'product-plan') {
    invalidatePlanningArtifacts(next, ['ui-designs', 'technical-plan'])
    next.artifactStatus['requirement-spec'] = next.artifactStatus['product-plan'] = 'pending'
    next.stage = 'awaiting_requirement_document_confirmation'
    if (key === 'requirement-spec') {
      next.artifacts.productPlan.app.name = next.artifacts.requirementSpec.app_info.name
      next.artifacts.productPlan.app.summary = next.artifacts.requirementSpec.app_info.description
    }
  } else if (key === 'ui-designs') {
    invalidatePlanningArtifacts(next, ['technical-plan'])
    next.artifacts.uiDesigns.pages.forEach((page) => {
      page.status = 'generated'
    })
    next.artifacts.uiDesigns.confirmation_status = 'pending_user_confirmation'
  }
  next.artifactStatus[key] = 'pending'
  next.revision += 1
  next.updatedAt = new Date().toISOString()
  synchronizePlanningReferences(next.artifacts)
  next.documents = {}
  return next
}

/** 保存每份当前已生成的正式 Markdown，供编辑、确认与文件视图共享。 */
export function refreshPlanningDocuments(record: InitializationPlanningRecord): void {
  // ProductPlan 不再生成独立用户文档；它的可见业务事实已合入需求规格说明书。
  for (const key of ['requirement-spec', 'ui-designs', 'technical-plan'] as FormalArtifactKey[]) {
    if (record.artifactStatus[key] !== 'draft')
      record.documents[key] = planningMarkdown(record, key)
  }
  delete record.documents['product-plan']
}
