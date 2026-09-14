/** RequirementSpec 生成提示词：约束原型剧本产物与真实工程确认合同保持一致。 */
export const REQUIREMENT_SPEC_GENERATION_PROMPT = `
你是应用需求分析师。输出一份可进入确认的 RequirementSpec，并只陈述已知或经用户确认的业务事实。

必须覆盖：
1. app_info：应用名称、面向对象、业务目标与摘要；
2. user_roles：每个参与者的稳定 id、职责与业务权限；
3. feature_modules：模块名称、业务说明与优先级；
4. pages：稳定 pageId、路由、所属模块和页面说明；
5. entities：实体、用途、需要记录的信息（fields）与需要支持的业务操作描述（business_operations）；这里只描述业务需求，不设计字段类型、操作参数、返回结构或数据实现；
6. business_flows：端到端流程及可执行步骤；
7. authorization_requirements：仅记录用户明确提出的页面或操作控制；
8. acceptance_criteria：可由用户验证的产品结果。

同时为每个页面补齐后续旅程需要的业务事实：页面目标、信息项、用户操作及预期结果、加载/空/失败/成功/校验状态、页面验收标准。页面、实体、流程、操作和验收标准必须可以相互追溯；不要编造数据源、接口、数据库、技术方案或未确认权限。缺失的关键业务事实必须在确认前标记为待补充，而不是静默省略。
`.trim()

export type RequirementReadinessIssue = {
  key: string
  label: string
  detail: string
}

/** 将未知值收窄为对象，避免不完整草稿让质量检查中断。 */
function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 将未知值收窄为对象数组，空值按未填写处理。 */
function recordsOf(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(recordOf).filter((item) => Object.keys(item).length) : []
}

/** 判断一项文本是否具备可确认的业务描述。 */
function hasText(value: unknown): boolean {
  return typeof value === 'string' && value.trim().length > 0
}

/** 返回确认需求规格前的必备业务事实缺口，规则对齐真实工程 RequirementSpec 校验。 */
export function requirementReadinessIssues(
  specValue: unknown,
  productPlanValue: unknown
): RequirementReadinessIssue[] {
  const spec = recordOf(specValue)
  const productPlan = recordOf(productPlanValue)
  const app = recordOf(spec.app_info)
  const pages = recordsOf(spec.pages)
  const productPages = recordsOf(productPlan.pages)
  const issues: RequirementReadinessIssue[] = []
  const requiredLists: Array<[string, string]> = [
    ['user_roles', '业务参与者'],
    ['feature_modules', '功能模块'],
    ['pages', '页面清单'],
    ['entities', '实体'],
    ['business_flows', '业务流程'],
    ['acceptance_criteria', '应用验收标准']
  ]

  if (!hasText(app.name) || !hasText(app.target || app.description || app.summary)) {
    issues.push({ key: 'app', label: '应用定位', detail: '需填写应用名称与清晰的业务目标。' })
  }
  requiredLists.forEach(([field, label]) => {
    if (
      !recordsOf(spec[field]).length &&
      !(field === 'acceptance_criteria' && Array.isArray(spec[field]) && spec[field].some(hasText))
    ) {
      issues.push({ key: field, label, detail: `至少补充一项${label}。` })
    }
  })
  pages.forEach((page, index) => {
    if (![page.pageId, page.name, page.path, page.module_id, page.description].every(hasText)) {
      issues.push({
        key: `page-${index}`,
        label: `页面 ${index + 1}`,
        detail: '需包含稳定标识、名称、路由、所属模块和说明。'
      })
    }
    const productPage = productPages.find((item) => item.pageId === page.pageId)
    if (
      !productPage ||
      !hasText(productPage.goal) ||
      !recordsOf(productPage.information_items).length ||
      !recordsOf(productPage.actions).length ||
      !Object.keys(recordOf(productPage.state_requirements)).length ||
      !Array.isArray(productPage.acceptance_criteria) ||
      !productPage.acceptance_criteria.some(hasText)
    ) {
      issues.push({
        key: `behavior-${index}`,
        label: `${String(page.name || `页面 ${index + 1}`)}行为`,
        detail: '需补齐目标、信息项、操作、状态和页面验收标准。'
      })
    }
  })
  recordsOf(spec.entities).forEach((entity, index) => {
    const operations = Array.isArray(entity.business_operations) ? entity.business_operations : []
    const hasBusinessOperation = operations.some((operation) =>
      typeof operation === 'string'
        ? hasText(operation)
        : Boolean(
            operation &&
              typeof operation === 'object' &&
              hasText((operation as Record<string, unknown>).name)
          )
    )
    if (
      ![entity.id, entity.name, entity.description].every(hasText) ||
      !recordsOf(entity.fields).length ||
      !hasBusinessOperation
    ) {
      issues.push({
        key: `entity-${index}`,
        label: `实体 ${index + 1}`,
        detail: '需包含稳定标识、名称、用途、至少一个需要记录的信息和一项业务操作。'
      })
    }
  })
  recordsOf(spec.business_flows).forEach((flow, index) => {
    if (
      ![flow.id, flow.name, flow.description].every(hasText) ||
      !Array.isArray(flow.steps) ||
      !flow.steps.length
    ) {
      issues.push({
        key: `flow-${index}`,
        label: `业务流程 ${index + 1}`,
        detail: '需包含稳定标识、说明和至少一个流程步骤。'
      })
    }
  })
  return issues
}

/** 校验通过前阻止工作流进入下游，避免不完整需求驱动 UI、技术规划方案和测试。 */
export function assertRequirementReady(specValue: unknown, productPlanValue: unknown): void {
  const issues = requirementReadinessIssues(specValue, productPlanValue)
  if (issues.length) {
    throw new Error(
      `需求规格尚不完整：${issues
        .slice(0, 3)
        .map((item) => item.label)
        .join('、')}。请在右侧表单补齐后再确认。`
    )
  }
}
