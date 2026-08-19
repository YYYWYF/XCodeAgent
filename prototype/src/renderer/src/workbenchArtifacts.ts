/**
 * 产出物构建器：把页面详细设计（page-designs.json）序列化为
 * 右侧「文档」tab 的富 markdown，以及「源码」tab 的真实感 TSX。
 */

import { backendControllerPath, frontendPagePath } from './mock/workspaceFiles'
import type { BusinessObject } from './components/BusinessObjects/model'
import {
  TEST_CASE_ESTIMATE_GROUPS,
  type TestCaseExecutionSnapshot,
  type TestCaseEstimateGroup
} from './testCasePreparation'

export type PageDesignRegion = { name?: string; responsibility?: string }
export type PageDesignApiDep = {
  apiContractId?: string
  method?: string
  path?: string
  purpose?: string
}
export type PageDesignBinding = {
  endpointId?: string
  sourcePath?: string
  target?: string
}
export type PageDesignAgentDep = {
  agentId?: string
  name?: string
  purpose?: string
}

export type PageDesign = {
  target_type?: string
  target_id?: string
  name?: string
  path?: string
  page_goal?: string
  basic_layout?: { overall?: string; regions?: PageDesignRegion[] }
  layout_design?: { structure?: string }
  interactions?: string[]
  state_feedback?: string[]
  operation_interactions?: string[]
  operation_visibility?: string[]
  page_navigation?: string[]
  permissions?: string[]
  states?: string[]
  api_dependencies?: PageDesignApiDep[]
  agent_dependencies?: PageDesignAgentDep[]
  response_bindings?: PageDesignBinding[]
  acceptance_criteria?: string[]
  dependent_pages?: string[]
  [key: string]: unknown
}

type RequirementAgentSummary = {
  id?: string
  name?: string
  label?: string
  purpose?: string
  pages?: string[]
  interaction?: string
  boundaries?: string[]
  permissions?: string[]
  acceptance_criteria?: string[]
  acceptanceCriteria?: string[]
}

type ProjectPlanAgentApiReference = {
  apiContractId?: string
  endpointId?: string
  method?: string
  path?: string
}

type ProjectPlanAgentSummary = {
  id?: string
  name?: string
  label?: string
  purpose?: string
  model?: string
  modelId?: string
  pages?: string[]
  tools?: string[]
  apiReferences?: ProjectPlanAgentApiReference[]
  knowledgeReferences?: string[]
  permissions?: string[]
  integration?: string
  acceptanceCriteria?: string[]
}

export type TestCaseEstimate = {
  total: number
  groups: TestCaseEstimateGroup[]
}

/** 从需求或计划剧本读取预计用例概要，缺省时使用统一的演示基线。 */
export function testCaseEstimateFromSource(source?: Record<string, unknown>): TestCaseEstimate {
  const rawEstimate = source?.test_case_estimate
  const rawGroups =
    rawEstimate && typeof rawEstimate === 'object'
      ? (rawEstimate as { groups?: unknown }).groups
      : undefined
  if (Array.isArray(rawGroups) && rawGroups.length > 0) {
    const groups = rawGroups
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
      .map((item, index) => ({
        id: String(item.id || `group-${index + 1}`),
        label: String(item.label || `业务场景 ${index + 1}`),
        total: Math.max(0, Number(item.total || 0)),
        coverage: String(item.coverage || '核心业务路径与异常边界')
      }))
      .filter((group) => group.total > 0)
    if (groups.length > 0) {
      return { groups, total: groups.reduce((sum, group) => sum + group.total, 0) }
    }
  }
  return {
    groups: TEST_CASE_ESTIMATE_GROUPS,
    total: TEST_CASE_ESTIMATE_GROUPS.reduce((sum, group) => sum + group.total, 0)
  }
}

/** 渲染需求文档与计划文档共用的预计测试用例概要表。 */
function buildTestCaseEstimateSection(source?: Record<string, unknown>): string[] {
  const estimate = testCaseEstimateFromSource(source)
  return [
    '## 预计测试用例',
    '',
    `计划确认后将后台异步生成约 **${estimate.total}** 条业务测试用例，按场景分批生成和校验。`,
    '',
    '| 业务场景 | 预计数量 | 覆盖范围 |',
    '| --- | ---: | --- |',
    ...estimate.groups.map((group) => `| ${group.label} | ${group.total} | ${group.coverage} |`),
    ''
  ]
}

/** 把页面详细设计序列化为富 markdown（覆盖目标/布局/交互/接口/验收等）。 */
export function buildPageDesignDoc(design: PageDesign): string {
  const lines: string[] = [`# ${design.name || '页面'} 页面详细设计`, '']
  if (design.path) lines.push(`- **路由**：\`${design.path}\``, '')

  if (design.page_goal) {
    lines.push('## 页面目标', '', design.page_goal, '')
  }

  if (design.basic_layout?.overall || design.basic_layout?.regions?.length) {
    lines.push('## 布局')
    if (design.basic_layout.overall) lines.push('', design.basic_layout.overall)
    design.basic_layout.regions?.forEach((region) => {
      lines.push(`- **${region.name || '区域'}**：${region.responsibility || ''}`)
    })
    lines.push('')
  }
  if (design.layout_design?.structure) {
    lines.push('**结构**：' + design.layout_design.structure, '')
  }

  if (design.interactions?.length) {
    lines.push('## 交互', '')
    design.interactions.forEach((item) => lines.push(`- ${item}`))
    lines.push('')
  }
  if (design.state_feedback?.length) {
    lines.push('## 状态与反馈', '')
    design.state_feedback.forEach((item) => lines.push(`- ${item}`))
    lines.push('')
  }
  if (design.operation_interactions?.length) {
    lines.push('## 操作', '')
    design.operation_interactions.forEach((item) => lines.push(`- ${item}`))
    lines.push('')
  }
  if (design.api_dependencies?.length) {
    lines.push('## 接口依赖', '')
    design.api_dependencies.forEach((api) =>
      lines.push(`- \`${api.method || 'GET'} ${api.path || ''}\` — ${api.purpose || ''}`)
    )
    lines.push('')
  }
  if (design.agent_dependencies?.length) {
    lines.push('## 智能体依赖', '')
    design.agent_dependencies.forEach((agent) =>
      lines.push(`- **${agent.name || agent.agentId || '智能体'}** — ${agent.purpose || ''}`)
    )
    lines.push('')
  }
  if (design.response_bindings?.length) {
    lines.push('## 数据绑定', '')
    design.response_bindings.forEach((binding) =>
      lines.push(`- \`${binding.sourcePath || ''}\` → ${binding.target || ''}`)
    )
    lines.push('')
  }
  if (design.states?.length) {
    lines.push('## 状态', '')
    design.states.forEach((state) => lines.push(`- ${state}`))
    lines.push('')
  }
  if (design.permissions?.length) {
    lines.push('## 权限', '')
    design.permissions.forEach((permission) => lines.push(`- \`${permission}\``))
    lines.push('')
  }
  if (design.acceptance_criteria?.length) {
    lines.push('## 验收标准', '')
    design.acceptance_criteria.forEach((criterion) => lines.push(`- ${criterion}`))
    lines.push('')
  }
  if (design.dependent_pages?.length) {
    lines.push('## 关联页面', '')
    design.dependent_pages.forEach((page) => lines.push(`- ${page}`))
    lines.push('')
  }
  return lines.join('\n')
}

function pascalCase(value: string): string {
  return value
    .split(/[-_]/)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join('')
}

/** 从页面设计生成真实感的 TSX 源码（含状态、表格、数据与接口依赖）。 */
export function buildPageSource(
  design: PageDesign,
  pageId: string
): { filePath: string; content: string } {
  const componentName = `${pascalCase(pageId)}Page`
  const name = design.name || pageId
  const path = design.path || `/${pageId}`
  const regions = design.basic_layout?.regions?.map((r) => r.name || '').filter(Boolean) || []
  const apis = design.api_dependencies || []
  const interactions = design.interactions || []
  const agents = design.agent_dependencies || []
  const apiComments = apis.length
    ? apis
        .map((api) => `// ${api.method || 'GET'} ${api.path || ''} — ${api.purpose || ''}`)
        .join('\n  ')
    : '// 数据通过实体操作或接口依赖提供，数据实现由相关开发工作流确认'

  const content = [
    `import { Button, Card, Space, Table, Tag, message } from 'antd'`,
    `import { useEffect, useState } from 'react'`,
    ``,
    `// 由 AIStudio 生成 · ${name} · ${path}`,
    `type Row = Record<string, unknown>`,
    ``,
    `export default function ${componentName}() {`,
    `  const [loading, setLoading] = useState(false)`,
    `  const [rows, setRows] = useState<Row[]>([])`,
    ``,
    `  // 数据依赖`,
    `  ${apiComments}`,
    ``,
    `  useEffect(() => {`,
    `    void loadList()`,
    `  }, [])`,
    ``,
    `  async function loadList() {`,
    `    setLoading(true)`,
    `    // 调用实体操作并绑定响应`,
    `    setRows([])`,
    `    setLoading(false)`,
    `    message.success('${name}数据已加载')`,
    `  }`,
    ``,
    `  return (`,
    `    <Card title="${name}" loading={loading}>`,
    `      {/* ${regions.join(' / ') || '主内容区'} */}`,
    `      <Space style={{ marginBottom: 12 }}>`,
    `        <Button type="primary" onClick={() => void loadList()}>查询</Button>`,
    `        <Button>新增</Button>`,
    `      </Space>`,
    `      <Table<Row>`,
    `        rowKey={(r) => String(r.id ?? 0)}`,
    `        columns={[`,
    `          { title: '${regions[0] || '主字段'}', dataIndex: 'name' },`,
    `          { title: '状态', dataIndex: 'status', render: (v) => <Tag>{String(v ?? '-')}</Tag> },`,
    `        ]}`,
    `        dataSource={rows}`,
    `      />`,
    `      {/* 交互：${interactions.slice(0, 2).join('；') || '待补充'} */}`,
    `      {/* 智能体：${agents.map((agent) => agent.name || agent.agentId).join('；') || '暂无'} */}`,
    `    </Card>`,
    `  )`,
    `}`,
    ``
  ].join('\n')
  return { filePath: frontendPagePath(pageId), content }
}

/** 从接口设计生成真实感的 Java Controller 源码（对齐 build-task-plan 的 target_files）。 */
export function buildEndpointSource(design: Record<string, any>): {
  filePath: string
  content: string
} {
  const method = String(design.method || 'GET').toUpperCase()
  const path = String(design.path || '/api/resource')
  const summary = String(design.summary || design.name || '接口')
  // /api/rechecks/my → Rechecks;取 path 首段资源名做 Controller 类名。
  const resource =
    path
      .split('/')
      .filter(Boolean)
      .find((seg) => seg !== 'api') || 'Resource'
  const className = `${pascalCase(resource)}Controller`
  const packageName = resource.toLowerCase()

  const iface = (design.interface_design || {}) as Record<string, any>
  const request = (iface.request || {}) as Record<string, any>
  const queryParams = (request.query_parameters || []) as Array<Record<string, any>>
  const response = (iface.response_format || {}) as Record<string, any>
  const logic = (design.processing_logic || []) as string[]

  const methodLower = method.toLowerCase()
  const mapping =
    method === 'GET'
      ? 'GetMapping'
      : method === 'POST'
        ? 'PostMapping'
        : method === 'PUT'
          ? 'PutMapping'
          : method === 'DELETE'
            ? 'DeleteMapping'
            : 'RequestMapping'

  const paramsSig = queryParams
    .map(
      (p) =>
        `@RequestParam(required = ${Boolean(p.required)}) String ${String(p.name || 'arg').replace(/[^a-zA-Z0-9]/g, '')}`
    )
    .join(', ')
  const paramComments = queryParams.length
    ? queryParams
        .map(
          (p) =>
            `   * @param ${String(p.name || 'arg').replace(/[^a-zA-Z0-9]/g, '')} ${p.schema || ''}`
        )
        .join('\n')
    : ''
  const logicComments = logic.length
    ? logic.map((l) => `     * ${l}`).join('\n')
    : '     * 按业务规则处理请求'

  const lines = [
    `package com.aistudio.${packageName}.controller;`,
    ``,
    `import org.springframework.web.bind.annotation.*;`,
    `import com.aistudio.common.api.ApiResponse;`,
    `import org.springframework.web.validation.annotation.Validated;`,
    ``,
    `/**`,
    ` * 由 AIStudio 生成 · ${summary} · ${method} ${path}`,
    ` */`,
    `@RestController`,
    `@RequestMapping("/api/${packageName}")`,
    `@Validated`,
    `public class ${className} {`,
    ``,
    `  /**`,
    `   * ${summary}。`,
    paramComments,
    `   * @return ${response.schema ? JSON.stringify(response.schema) : 'ApiResponse'}`,
    `   */`,
    `  @${mapping}("${path.replace(/^\/api\/[^/]+/, '')}")`,
    `  public ApiResponse<Object> ${methodLower}${className.replace('Controller', '')}(${paramsSig}) {`,
    logicComments,
    `    return ApiResponse.success(null);`,
    `  }`,
    `}`,
    ``
  ]
  // 合并连续空行为单行,避免文档空洞。
  const content = lines.filter((line, i) => !(line === '' && lines[i - 1] === '')).join('\n')

  return {
    filePath: backendControllerPath(packageName),
    content
  }
}

export function buildPageDocFallback(pageLabel: string, path: string, purpose: string): string {
  return [
    `# ${pageLabel} 页面设计`,
    '',
    `- **路由**：\`${path}\``,
    `- **用途**：${purpose || '暂无说明'}`,
    '',
    '## 布局结构',
    '- 页面标题与核心操作区',
    '- 主内容区（列表 / 表单 / 看板）',
    '',
    '## 交互与状态',
    '- 加载 / 空 / 错误态',
    '- 关键操作（新增 / 编辑 / 查询 / 导出）',
    '',
    '> 完整详细设计在「详细设计」确认流程中生成。',
    ''
  ].join('\n')
}

/** 拼一份 app 级需求文档 markdown，作为未选页面时「文档」tab 的兜底。 */
export function buildAppRequirementDoc(
  applicationName: string,
  pages: Array<{ label: string; path?: string; purpose?: string }>,
  apiContracts: Array<{
    label: string
    endpoints: Array<{ method?: string; path?: string; summary?: string }>
  }>
): string {
  const lines: string[] = [`# ${applicationName || '应用'} 需求文档`, '']
  lines.push('## 页面清单')
  if (pages.length === 0) lines.push('_暂无页面_')
  pages.forEach((page) =>
    lines.push(`- **${page.label}** \`${page.path || ''}\` — ${page.purpose || ''}`)
  )
  if (apiContracts.length) {
    lines.push('', '## 接口契约')
    apiContracts.forEach((contract) => {
      lines.push(`- **${contract.label}**`)
      contract.endpoints.forEach((endpoint) =>
        lines.push(
          `  - \`${endpoint.method || ''}\` \`${endpoint.path || ''}\` — ${endpoint.summary || ''}`
        )
      )
    })
  }
  return lines.join('\n')
}

// —— 需求分析/项目计划阶段三份产物文档构建器（需求文档 / 项目计划 / 构建任务计划）——

/** 从需求文档结构化数据渲染 Markdown。appName 优先用当前应用名。 */
export function buildRequirementSpecDoc(spec: Record<string, any>, appName?: string): string {
  const app = (spec.app_info || {}) as Record<string, any>
  const lines = [
    `# 需求文档 · ${appName || app.name || '应用'}`,
    '',
    '## 应用目标',
    String(app.description || ''),
    '',
    '## 用户角色'
  ]
  for (const role of (spec.user_roles || []) as Array<Record<string, any>>) {
    lines.push(`- **${role.name}**：${role.description}`)
    if (Array.isArray(role.permissions) && role.permissions.length) {
      lines.push(`  权限：${role.permissions.join('、')}`)
    }
  }
  lines.push('', '## 页面清单')
  for (const page of (spec.pages || []) as Array<Record<string, any>>) {
    lines.push(`- **${page.name}** \`${page.path}\`：${page.description}`)
  }
  const agents = (spec.agents || spec.agent_requirements || []) as RequirementAgentSummary[]
  if (agents.length) {
    lines.push('', '## 智能体需求')
    for (const agent of agents) {
      lines.push(`- **${agent.name || agent.label || agent.id}**：${agent.purpose || ''}`)
      if (Array.isArray(agent.pages) && agent.pages.length) {
        lines.push(`  - 页面入口：${agent.pages.map((page) => `\`${page}\``).join('、')}`)
      }
      if (agent.interaction) lines.push(`  - 交互：${agent.interaction}`)
      for (const boundary of agent.boundaries || agent.permissions || []) {
        lines.push(`  - 边界：${boundary}`)
      }
      for (const criterion of agent.acceptance_criteria || agent.acceptanceCriteria || []) {
        lines.push(`  - 验收：${criterion}`)
      }
    }
  }
  lines.push('', '## 核心业务流程')
  for (const flow of (spec.business_flows || []) as Array<Record<string, any>>) {
    lines.push(`- **${flow.name}**：${flow.description}`)
    for (const step of (flow.steps || []) as Array<Record<string, any>>) {
      lines.push(`  ${step.step_id}. ${step.description}`)
    }
  }
  lines.push('', ...buildTestCaseEstimateSection(spec))
  lines.push('', '## 验收标准')
  for (const criterion of (spec.acceptance_criteria || []) as string[]) lines.push(`- ${criterion}`)
  lines.push('', '## 假设')
  for (const assumption of (spec.assumptions || []) as string[]) lines.push(`- ${assumption}`)
  return lines.join('\n')
}

/** 从项目计划数据渲染 Markdown（页面树 + 技术栈 + 接口契约 + 执行顺序）。 */
export function buildProjectPlanDoc(plan: Record<string, any>, appName?: string): string {
  const tech = (plan.tech_stack || {}) as Record<string, any>
  const lines = [
    `# 项目计划 · ${appName || '应用'}`,
    '',
    '## 技术栈',
    `- 前端：${tech.frontend}`,
    `- 后端：${tech.backend}`,
    `- 数据库：${tech.database}`,
    '',
    '## 规划摘要',
    String(plan.summary || ''),
    '',
    '## 页面',
    '| 菜单 | 页面 | 路由 |',
    '| --- | --- | --- |'
  ]
  const walk = (nodes: Array<Record<string, any>>): void => {
    for (const node of nodes) {
      if (node.type === 'menu') {
        lines.push(`| **${node.label}** | | |`)
        walk((node.children || []) as Array<Record<string, any>>)
      } else {
        lines.push(`| | ${node.label} | \`${node.path}\` |`)
      }
    }
  }
  walk((plan.menu_tree || []) as Array<Record<string, any>>)
  lines.push('', '## 接口契约')
  for (const api of (plan.apis || []) as Array<Record<string, any>>) {
    lines.push(`- **${api.method}** \`${api.path}\` · ${api.summary}`)
  }
  const agents = (plan.agents || []) as ProjectPlanAgentSummary[]
  if (agents.length) {
    lines.push('', '## 智能体')
    for (const agent of agents) {
      lines.push(
        `- **${agent.name || agent.label || agent.id}** \`${agent.id || ''}\`：${agent.purpose || ''}`
      )
      if (agent.model) {
        lines.push(`  - 模型：${agent.model}${agent.modelId ? `（\`${agent.modelId}\`）` : ''}`)
      }
      if (Array.isArray(agent.pages) && agent.pages.length) {
        lines.push(`  - 页面：${agent.pages.map((page) => `\`${page}\``).join('、')}`)
      }
      if (Array.isArray(agent.tools) && agent.tools.length) {
        lines.push(`  - 工具：${agent.tools.join('、')}`)
      }
      for (const reference of agent.apiReferences || []) {
        lines.push(
          `  - 接口：\`${String(reference.method || 'GET').toUpperCase()} ${reference.path || ''}\`（契约：\`${reference.apiContractId || ''}\`，端点：\`${reference.endpointId || ''}\`）`
        )
      }
      if (Array.isArray(agent.knowledgeReferences) && agent.knowledgeReferences.length) {
        lines.push(`  - 知识：${agent.knowledgeReferences.map((item) => `\`${item}\``).join('、')}`)
      }
      for (const permission of agent.permissions || []) lines.push(`  - 权限：${permission}`)
      if (agent.integration) lines.push(`  - 页面集成：${agent.integration}`)
      for (const criterion of agent.acceptanceCriteria || []) lines.push(`  - 验收：${criterion}`)
    }
  }
  const agentAcceptanceCriteria = (plan.agent_acceptance_criteria || []) as string[]
  if (agentAcceptanceCriteria.length) {
    lines.push('', '## 智能体集成验收')
    for (const criterion of agentAcceptanceCriteria) lines.push(`- ${criterion}`)
  }
  lines.push('', '## 执行顺序')
  for (const step of (plan.execution_order || []) as Array<Record<string, any>>) {
    lines.push(`${step.order}. ${step.task}`)
  }
  lines.push('', ...buildTestCaseEstimateSection(plan))
  return lines.join('\n')
}

/** 从 ProductPlan 当前契约渲染产品可见的页面、动作、状态与验收 Markdown。 */
export function buildProductPlanDoc(plan: Record<string, any>, appName?: string): string {
  const app = (plan.app || {}) as Record<string, any>
  const lines = [
    `# 产品规划 · ${appName || app.name || '应用'}`,
    '',
    String(app.summary || ''),
    '',
    '## 页面与产品行为'
  ]
  for (const page of (plan.pages || []) as Array<Record<string, any>>) {
    lines.push('', `### ${page.name || page.pageId} · \`${page.path || ''}\``)
    if (page.goal) lines.push(String(page.goal))
    const informationItems = (page.information_items || []) as Array<Record<string, any>>
    if (informationItems.length) {
      lines.push('', '**信息项**')
      informationItems.forEach((item) =>
        lines.push(`- ${item.label || item.itemId}：${item.description || ''}`)
      )
    }
    const actions = (page.actions || []) as Array<Record<string, any>>
    if (actions.length) {
      lines.push('', '**用户动作**')
      actions.forEach((action) => {
        const behavior = (action.behavior || {}) as Record<string, any>
        lines.push(
          `- ${action.name || action.actionId}：${behavior.expectedResult || action.description || ''}`
        )
      })
    }
    const acceptance = (page.acceptance_criteria || []) as string[]
    if (acceptance.length) {
      lines.push('', '**页面验收**')
      acceptance.forEach((item) => lines.push(`- ${item}`))
    }
  }
  lines.push('', '## 产品验收标准')
  for (const criterion of (plan.product_acceptance_criteria || []) as string[]) {
    lines.push(`- ${criterion}`)
  }
  return lines.join('\n')
}

/** 从 TechnicalPlan 当前契约渲染架构、实体、API 与页面技术绑定 Markdown。 */
export function buildTechnicalPlanDoc(plan: Record<string, any>, appName?: string): string {
  const architecture = (plan.architecture || {}) as Record<string, any>
  const lines = [
    `# 技术规划方案 · ${appName || '应用'}`,
    '',
    '## 技术架构',
    `- 前端：${architecture.frontend || ''}`,
    `- 后端：${architecture.backend || ''}`,
    `- 数据：${architecture.data || ''}`,
    '',
    '## 实体'
  ]
  for (const entity of (plan.entities || []) as Array<Record<string, any>>) {
    lines.push(`- **${entity.name || entity.id}**：${entity.description || ''}`)
    for (const field of (entity.fields || []) as Array<Record<string, any>>) {
      lines.push(
        `  - ${field.label || field.name} · ${field.type || 'text'}${field.required ? ' · 必填' : ''}`
      )
    }
  }
  lines.push('', '## API 契约')
  for (const contract of (plan.api_contracts || []) as Array<Record<string, any>>) {
    lines.push(`- **${contract.name || contract.id}** \`${contract.base_path || ''}\``)
    for (const endpoint of (contract.endpoints || []) as Array<Record<string, any>>) {
      lines.push(
        `  - \`${endpoint.method || ''}\` \`${endpoint.path || ''}\`：${endpoint.summary || ''}`
      )
    }
  }
  lines.push('', '## 页面技术绑定')
  for (const page of (plan.pages || []) as Array<Record<string, any>>) {
    const references = (page.references || {}) as Record<string, any>
    const endpoints = Array.isArray(references.endpoint_dependencies)
      ? references.endpoint_dependencies.join('、') || '无'
      : '无'
    lines.push(`- **${page.pageId || '页面'}**：Endpoint 依赖 ${endpoints}`)
  }
  return lines.join('\n')
}

/** 从构建任务计划数据渲染 Markdown（构建单元表 + 任务表）。 */
export function buildBuildTaskPlanDoc(plan: Record<string, any>): string {
  const summary = (plan.summary || {}) as Record<string, any>
  const units = (plan.build_units || []) as Array<Record<string, any>>
  const tasks = (plan.task_registry || []) as Array<Record<string, any>>
  const header = [`# 构建任务计划`, '']
  if (plan.version) header.push(`> 计划版本：${plan.version}`)
  if (plan.status) header.push(`> 计划状态：**${plan.status}**`)
  if (summary.total != null) {
    header.push(
      `> 任务总数：${summary.total}（前端 ${summary.frontend ?? '-'} / 后端 ${summary.backend ?? '-'} / 数据库 ${summary.database ?? '-'}）`
    )
  }
  header.push('', '## 构建单元', '| 单元 | 类型 | 状态 |', '| --- | --- | --- |')
  units.forEach((unit) =>
    header.push(`| ${unit.label || unit.id} | ${unit.kind || '-'} | ${unit.status || '-'} |`)
  )
  header.push(
    '',
    '## 任务',
    '| ID | 单元 | Owner | 类型 | 标题 | 验收标准 |',
    '| --- | --- | --- | --- | --- | --- |'
  )
  tasks.forEach((task) => {
    const unitLabel = units.find((unit) => unit.id === task.unit_id)?.label || task.unit_id || '-'
    const acceptance = Array.isArray(task.acceptance_criteria)
      ? task.acceptance_criteria.join('；')
      : '-'
    header.push(
      `| ${task.id} | ${unitLabel} | ${task.owner} | ${task.task_type || '-'} | ${task.title || '-'} | ${acceptance} |`
    )
  })
  return header.join('\n')
}

// —— 开发阶段接口详细设计文档构建器 ——

/** 从接口详设数据渲染 Markdown（数据用途 / 数据来源 / 接口设计 / 处理逻辑 / 验收标准）。 */
export function buildEndpointDesignDoc(design: Record<string, any>): string {
  const method = String(design.method || 'GET').toUpperCase()
  const path = String(design.path || '')
  const lines = [`# ${method} ${path} · ${design.name || '接口'}`, '']
  if (design.summary) lines.push(design.summary, '')

  const usage = (design.data_usage || {}) as Record<string, any>
  lines.push('## 一、数据用途')
  if (usage.purpose) lines.push(`- **用途**：${usage.purpose}`)
  if (Array.isArray(usage.served_pages) && usage.served_pages.length) {
    lines.push(`- **服务页面**：${usage.served_pages.join('、')}`)
  }
  lines.push('')

  const origin = (design.data_origin || {}) as Record<string, any>
  const source = (origin.effective_source || {}) as Record<string, any>
  lines.push('## 二、数据来源')
  lines.push(`- **来源类型**：${origin.source_type || '-'}`)
  if (source.database && Array.isArray(source.tables) && source.tables.length) {
    lines.push(`- **数据源**：${source.database} · ${source.tables.join('、')}`)
  }
  for (const note of (origin.notes || []) as string[]) lines.push(`- ${note}`)
  lines.push('')

  const iface = (design.interface_design || {}) as Record<string, any>
  const request = (iface.request || {}) as Record<string, any>
  lines.push('## 三、接口设计')
  if (request.method) lines.push(`- **Method**：${request.method}`)
  const params = [
    ...((request.path_parameters || []) as Array<Record<string, any>>),
    ...((request.query_parameters || []) as Array<Record<string, any>>),
    ...((request.header_parameters || []) as Array<Record<string, any>>)
  ]
  if (params.length) {
    lines.push('- **请求参数**：')
    params.forEach((param) =>
      lines.push(
        `  - \`${param.name}\`（${param.in || 'param'}）${param.required ? ' 必填' : ''} — ${param.schema || ''}`
      )
    )
  }
  if (request.request_body) {
    lines.push(
      `- **请求体**：\`${JSON.stringify((request.request_body as Record<string, any>).schema || '')}\``
    )
  }
  const response = (iface.response_format || {}) as Record<string, any>
  if (response.status_code != null) {
    lines.push(`- **响应**：HTTP ${response.status_code}`)
    if (response.schema) lines.push(`  - Schema：\`${JSON.stringify(response.schema)}\``)
    if (Array.isArray(response.errors) && response.errors.length) {
      lines.push(`  - 错误：${response.errors.join('、')}`)
    }
  }
  lines.push('')

  if (Array.isArray(design.processing_logic) && design.processing_logic.length) {
    lines.push('## 四、处理逻辑')
    design.processing_logic.forEach((logic: string) => lines.push(`- ${logic}`))
    lines.push('')
  }
  if (Array.isArray(design.acceptance_criteria) && design.acceptance_criteria.length) {
    lines.push('## 五、验收标准')
    design.acceptance_criteria.forEach((criterion: string) => lines.push(`- ${criterion}`))
    lines.push('')
  }
  return lines.join('\n')
}

// —— 文档行级 diff（IDE 式：新旧内容对比，输出标准 unified diff 供 react-diff-view 渲染）——

/**
 * 按行比对旧/新文档内容,输出标准 unified diff(单 hunk)。
 * 粗粒度贪心对齐(演示用,不追求精确 LCS);生成结果可直接喂 react-diff-view 的 parseDiff。
 */
export function buildLineDiff(oldText: string, newText: string, path: string): string {
  const oldLines = (oldText || '').split('\n')
  const newLines = (newText || '').split('\n')
  const out: string[] = []
  let i = 0
  let j = 0
  while (i < oldLines.length || j < newLines.length) {
    if (oldLines[i] === newLines[j]) {
      out.push(' ' + oldLines[i])
      i += 1
      j += 1
      continue
    }
    // 新行在旧剩余中出现 → 视作新增;反之旧行删除
    if (oldLines.slice(i + 1).includes(newLines[j])) {
      out.push('+' + newLines[j])
      j += 1
      continue
    }
    if (newLines.slice(j + 1).includes(oldLines[i])) {
      out.push('-' + oldLines[i])
      i += 1
      continue
    }
    if (i < oldLines.length && j < newLines.length) {
      out.push('-' + oldLines[i])
      out.push('+' + newLines[j])
      i += 1
      j += 1
      continue
    }
    if (i < oldLines.length) {
      out.push('-' + oldLines[i])
      i += 1
      continue
    }
    if (j < newLines.length) {
      out.push('+' + newLines[j])
      j += 1
      continue
    }
  }
  if (out.length === 0) return ''
  const body = out.join('\n')
  return `--- a/${path}\n+++ b/${path}\n@@ -1,${oldLines.length} +1,${newLines.length} @@\n${body}`
}

/** 审查阶段右侧面板的代码审查报告，明确当前审查消费的用例执行结果。 */
export function buildReviewReport(testExecution?: TestCaseExecutionSnapshot): string {
  const testBasis = testExecution
    ? `${testExecution.completed}/${testExecution.total} 条业务测试用例执行通过`
    : '全部业务测试用例已执行'
  return `# 代码审查报告

> 审查依据：${testBasis} · 审查范围：全部页面与接口模块 · 结论：**通过，可生成版本**

## 总览

| 审查项 | 结果 |
| --- | --- |
| 代码规范 | ✅ 通过 |
| 安全检查 | ✅ 通过 |
| 健康度 | ✅ 通过 |

## 代码规范

- 命名规范符合团队约定
- 无冗余 / 重复代码
- 注释覆盖率达标

## 安全检查

- 无硬编码密钥与凭证
- 输入参数校验完整
- 越权访问风险已覆盖

## 健康度

- 圈复杂度：正常
- 重复率：0.8%
- 单测覆盖：82%

## 模块清单

- 页面：我的回检
- 接口：GET /api/rechecks/my
`
}

// 平台内置操作到数据适配方法名的固定映射：适配层代码按这套命名生成。
const BUILTIN_ADAPTER_METHODS: Record<string, string> = {
  分页查询: 'pageQuery',
  查询详情: 'queryById',
  查询回检详情: 'queryById',
  新增: 'insert',
  更新: 'update',
  删除: 'delete'
}

/** 从字段映射标签里还原来源列名：标签形如「武汉回检数据库 · project_name（关联项目）」。 */
function columnNameFromMapping(sourceLabel: string): string {
  const matched = sourceLabel.match(/· (.+?)（/)
  return matched ? matched[1] : sourceLabel
}

/**
 * 从实体确认后的绑定生成数据适配层源码：数据库绑定产出 SQL 适配，外部服务绑定产出
 * 契约调用与出参翻译。对话区「确认绑定」后由开发工作流把这份文件作为实体交付物生成。
 */
export function buildEntityAdapterSource(object: BusinessObject): {
  filePath: string
  content: string
} {
  const className = `${pascalCase(object.id)}EntityAdapter`
  const bindingSummary = Array.from(
    new Set(
      object.operations.flatMap((operation) =>
        operation.implementation.bindings.map(
          (binding) =>
            binding.targetName
              ? `${binding.sourceName} · ${binding.targetName}（${binding.targetComment}）`
              : binding.sourceName
        )
      )
    )
  ).join(' + ')
  const mappingComments = object.fields.map((field) => {
    const mapping = object.operations
      .flatMap((operation) => operation.implementation.mappings)
      .find((item) => item.field === field.name && item.sourceLabel)
    return `    //   ${field.name} → ${mapping ? mapping.sourceLabel : '业务规则推导'}`
  })

  let customIndex = 0
  const methodBlocks = object.operations.map((operation) => {
    const implementation = operation.implementation
    const binding = implementation.bindings[0]
    const builtinMethod = BUILTIN_ADAPTER_METHODS[operation.name]
    const methodName =
      operation.operationType === 'builtin' && builtinMethod
        ? builtinMethod
        : `customOp${(customIndex += 1)}`
    const targetLabel = binding
      ? binding.targetName
        ? `${binding.sourceName} · ${binding.targetName}`
        : binding.sourceName
      : '业务规则推导'
    const head = [
      '    /**',
      `     * ${operation.name}（${operation.operationType === 'builtin' ? '平台内置' : '需求自定义'}） → ${targetLabel}`,
      '     */'
    ]
    // 本地实现：不产生数据访问代码，只落到业务规则服务。
    if (!binding || binding.sourceId === 'local') {
      return [
        ...head,
        `    public List<Map<String, Object>> ${methodName}(Map<String, Object> input) {`,
        `        // 本地业务规则：${implementation.rule || '校验输入 → 执行业务规则 → 返回结果'}`,
        '        return businessRuleService.execute(input);',
        '    }'
      ]
    }
    // 外部服务绑定：按固定契约调用并翻译出参，映射关系来自确认的绑定。
    if (implementation.kind === '外部服务') {
      const translations = implementation.mappings
        .filter((mapping) => mapping.sourceLabel)
        .map((mapping) => `        //   response.${columnNameFromMapping(mapping.sourceLabel)} → ${mapping.field}`)
      return [
        ...head,
        `    public Map<String, Object> ${methodName}(Map<String, Object> input) {`,
        '        // 调用外部服务并按确认的映射翻译出参',
        ...translations,
        `        Map<String, Object> response = externalClient.invoke("${binding.sourceName}", "${binding.targetName}", input);`,
        '        return responseTranslator.translate(response);',
        '    }'
      ]
    }
    // 数据库绑定：按映射出的来源列拼装查询，表名来自确认的库表绑定。
    const columns = implementation.mappings
      .filter((mapping) => mapping.sourceLabel)
      .map((mapping) => columnNameFromMapping(mapping.sourceLabel))
    const columnList = Array.from(new Set(columns)).join(', ') || '*'
    const table = binding.targetName || 'table'
    const kindNote = operation.operationType === 'builtin' ? '平台按表结构模板生成' : '按确认的绑定生成'
    return [
      ...head,
      `    public List<Map<String, Object>> ${methodName}(Map<String, Object> query) {`,
      `        // ${kindNote}：字段映射沿用绑定确认结果`,
      `        String sql = "SELECT ${columnList} FROM ${table}";`,
      '        return jdbcTemplate.queryForList(sql, query);',
      '    }'
    ]
  })

  const content = [
    'package com.aistudio.recheck.entity.adapter;',
    '',
    'import java.util.List;',
    'import java.util.Map;',
    '',
    'import org.springframework.jdbc.core.JdbcTemplate;',
    'import org.springframework.stereotype.Repository;',
    '',
    '/**',
    ` * 由 AIStudio 生成 · 实体「${object.name}」数据适配层`,
    ` * 数据绑定：${bindingSummary}`,
    ' * 字段映射在绑定确认时自动推导；页面统一通过 实体.操作() 消费这份数据能力。',
    ' */',
    '@Repository',
    `public class ${className} {`,
    '',
    '    // 实体字段 → 来源字段映射',
    ...mappingComments,
    '',
    ...methodBlocks.flat(),
    '}',
    ''
  ].join('\n')
  return { filePath: `backend/entity-adapters/${object.id}-entity-adapter.java`, content }
}
