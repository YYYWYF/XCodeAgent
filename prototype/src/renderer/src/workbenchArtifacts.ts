/**
 * 产出物构建器：把页面详细设计（page-designs.json）序列化为
 * 右侧「文档」tab 的富 markdown，以及「源码」tab 的真实感 TSX。
 */

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
  response_bindings?: PageDesignBinding[]
  acceptance_criteria?: string[]
  dependent_pages?: string[]
  [key: string]: unknown
}

/** 把页面详细设计序列化为富 markdown（覆盖目标/布局/交互/接口/验收等）。 */
export function buildPageDesignDoc(design: PageDesign): string {
  const lines: string[] = [`# ${design.name || '页面'} 页面需求文档`, '']
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

/** 从页面设计生成真实感的 TSX 源码（含状态、表格、接口依赖）。 */
export function buildPageSource(design: PageDesign, pageId: string): { filePath: string; content: string } {
  const componentName = `${pascalCase(pageId)}Page`
  const name = design.name || pageId
  const path = design.path || `/${pageId}`
  const regions = design.basic_layout?.regions?.map((r) => r.name || '').filter(Boolean) || []
  const apis = design.api_dependencies || []
  const interactions = design.interactions || []
  const apiComments = apis.length
    ? apis.map((api) => `// ${api.method || 'GET'} ${api.path || ''} — ${api.purpose || ''}`).join('\n  ')
    : '// 暂无接口依赖'

  const content = [
    `import { Button, Card, Space, Table, Tag, message } from 'antd'`,
    `import { useEffect, useState } from 'react'`,
    ``,
    `// 由 XCodeAgent 生成 · ${name} · ${path}`,
    `type Row = Record<string, unknown>`,
    ``,
    `export default function ${componentName}() {`,
    `  const [loading, setLoading] = useState(false)`,
    `  const [rows, setRows] = useState<Row[]>([])`,
    ``,
    `  // 接口依赖`,
    `  ${apiComments}`,
    ``,
    `  useEffect(() => {`,
    `    void loadList()`,
    `  }, [])`,
    ``,
    `  async function loadList() {`,
    `    setLoading(true)`,
    `    // 对接接口并绑定响应`,
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
    `    </Card>`,
    `  )`,
    `}`,
    ``
  ].join('\n')
  return { filePath: `frontend/src/pages/${pageId}/index.tsx`, content }
}

/** 从接口设计生成真实感的 Java Controller 源码（对齐 build-task-plan 的 target_files）。 */
export function buildEndpointSource(
  design: Record<string, any>
): { filePath: string; content: string } {
  const method = String(design.method || 'GET').toUpperCase()
  const path = String(design.path || '/api/resource')
  const summary = String(design.summary || design.name || '接口')
  // /api/rechecks/my → Rechecks;取 path 首段资源名做 Controller 类名。
  const resource = path.split('/').filter(Boolean).find((seg) => seg !== 'api') || 'Resource'
  const className = `${pascalCase(resource)}Controller`
  const packageName = resource.toLowerCase()

  const iface = (design.interface_design || {}) as Record<string, any>
  const request = (iface.request || {}) as Record<string, any>
  const queryParams = (request.query_parameters || []) as Array<Record<string, any>>
  const response = (iface.response_format || {}) as Record<string, any>
  const logic = (design.processing_logic || []) as string[]

  const methodLower = method.toLowerCase()
  const mapping = method === 'GET' ? 'GetMapping' : method === 'POST' ? 'PostMapping' : method === 'PUT' ? 'PutMapping' : method === 'DELETE' ? 'DeleteMapping' : 'RequestMapping'

  const paramsSig = queryParams
    .map((p) => `@RequestParam(required = ${Boolean(p.required)}) String ${String(p.name || 'arg').replace(/[^a-zA-Z0-9]/g, '')}`)
    .join(', ')
  const paramComments = queryParams.length
    ? queryParams.map((p) => `   * @param ${String(p.name || 'arg').replace(/[^a-zA-Z0-9]/g, '')} ${p.schema || ''}`).join('\n')
    : ''
  const logicComments = logic.length
    ? logic.map((l) => `     * ${l}`).join('\n')
    : '     * 按业务规则处理请求'

  const lines = [
    `package com.xcodeagent.${packageName}.controller;`,
    ``,
    `import org.springframework.web.bind.annotation.*;`,
    `import com.xcodeagent.common.api.ApiResponse;`,
    `import org.springframework.web.validation.annotation.Validated;`,
    ``,
    `/**`,
    ` * 由 XCodeAgent 生成 · ${summary} · ${method} ${path}`,
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
  const content = lines
    .filter((line, i) => !(line === '' && lines[i - 1] === ''))
    .join('\n')

  return {
    filePath: `backend/src/main/java/com/xcodeagent/${packageName}/controller/${className}.java`,
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
  apiContracts: Array<{ label: string; endpoints: Array<{ method?: string; path?: string; summary?: string }> }>
): string {
  const lines: string[] = [`# ${applicationName || '应用'} 需求文档`, '']
  lines.push('## 页面清单')
  if (pages.length === 0) lines.push('_暂无页面_')
  pages.forEach((page) => lines.push(`- **${page.label}** \`${page.path || ''}\` — ${page.purpose || ''}`))
  if (apiContracts.length) {
    lines.push('', '## 接口契约')
    apiContracts.forEach((contract) => {
      lines.push(`- **${contract.label}**`)
      contract.endpoints.forEach((endpoint) =>
        lines.push(`  - \`${endpoint.method || ''}\` \`${endpoint.path || ''}\` — ${endpoint.summary || ''}`)
      )
    })
  }
  return lines.join('\n')
}

// —— 设计阶段三份产物文档构建器（需求文档 / 项目计划 / 构建任务计划）——

/** 从需求文档结构化数据渲染 Markdown。appName 优先用当前应用名。 */
export function buildRequirementSpecDoc(
  spec: Record<string, any>,
  appName?: string
): string {
  const app = (spec.app_info || {}) as Record<string, any>
  const lines = [`# 需求文档 · ${appName || app.name || '应用'}`, '', '## 应用目标', String(app.description || ''), '', '## 用户角色']
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
  lines.push('', '## 核心业务流程')
  for (const flow of (spec.business_flows || []) as Array<Record<string, any>>) {
    lines.push(`- **${flow.name}**：${flow.description}`)
    for (const step of (flow.steps || []) as Array<Record<string, any>>) {
      lines.push(`  ${step.step_id}. ${step.description}`)
    }
  }
  lines.push('', '## 验收标准')
  for (const criterion of (spec.acceptance_criteria || []) as string[]) lines.push(`- ${criterion}`)
  lines.push('', '## 假设')
  for (const assumption of (spec.assumptions || []) as string[]) lines.push(`- ${assumption}`)
  return lines.join('\n')
}

/** 从项目计划数据渲染 Markdown（页面树 + 技术栈 + 接口契约 + 执行顺序）。 */
export function buildProjectPlanDoc(
  plan: Record<string, any>,
  appName?: string
): string {
  const tech = (plan.tech_stack || {}) as Record<string, any>
  const lines = [`# 项目计划 · ${appName || '应用'}`, '', '## 技术栈', `- 前端：${tech.frontend}`, `- 后端：${tech.backend}`, `- 数据库：${tech.database}`, '', '## 规划摘要', String(plan.summary || ''), '', '## 页面', '| 菜单 | 页面 | 路由 |', '| --- | --- | --- |']
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
  lines.push('', '## 执行顺序')
  for (const step of (plan.execution_order || []) as Array<Record<string, any>>) {
    lines.push(`${step.order}. ${step.task}`)
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
  units.forEach((unit) => header.push(`| ${unit.label || unit.id} | ${unit.kind || '-'} | ${unit.status || '-'} |`))
  header.push('', '## 任务', '| ID | 单元 | Owner | 类型 | 标题 | 验收标准 |', '| --- | --- | --- | --- | --- | --- |')
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
      lines.push(`  - \`${param.name}\`（${param.in || 'param'}）${param.required ? ' 必填' : ''} — ${param.schema || ''}`)
    )
  }
  if (request.request_body) {
    lines.push(`- **请求体**：\`${JSON.stringify((request.request_body as Record<string, any>).schema || '')}\``)
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

/** 审查阶段右侧面板的代码审查报告(非功能检查:规范 / 安全 / 健康度)。 */
export function buildReviewReport(): string {
  return `# 代码审查报告

> 审查范围：全部页面与接口模块 · 审查项：代码规范 / 安全 / 健康度 · 结论：**通过，可发布**

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
