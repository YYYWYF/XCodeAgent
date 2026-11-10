import type { WorkflowBuildExecutionTask, WorkflowBuildTaskPlanTask } from '../../../../typings'
import { buildTaskDisplayStatus } from './buildTaskStatus'
import { taskDisplayTitle } from './buildTaskPresentation'

export function displayTaskTitle(task: WorkflowBuildExecutionTask): string {
  /** 返回任务标题的中文展示文本，避免历史英文任务原样出现在 UI 中。 */

  const title = taskDisplayTitle({
    title: localizeTaskText(task.title || '', task),
    backendStage: task.backendStage
  })
  if (title) return title
  return `构建任务 ${taskId(task)}`
}

function localizeTaskText(value: string, task: WorkflowBuildExecutionTask): string {
  /** 将任务标题、说明和验收点转换为中文；中文原文直接保留。 */

  const text = value.trim()
  if (!text) return ''
  if (containsChinese(text)) return text
  const exact = exactTaskTranslation(text)
  if (exact) return exact
  const generated = generatedTaskTranslation(text, task)
  if (generated) return generated
  return `请完成任务 ${taskId(task)} 的实现与验证。`
}

function containsChinese(value: string): boolean {
  /** 判断文本是否已经包含中文，避免重复加工中文任务内容。 */

  return /[\u4e00-\u9fa5]/.test(value)
}

function exactTaskTranslation(value: string): string {
  /** 翻译已知的任务规划英文模板，覆盖历史会话中常见的任务详情。 */

  const translations: Record<string, string> = {
    'Create Express backend with employee CRUD API endpoints':
      '创建 Express 员工 CRUD 后端 API 接口',
    'Set up a Node.js + Express server with in-memory storage for employees. Implement endpoints: GET /api/employees (list), GET /api/employees/:employeeId (detail), POST /api/employees (create), PUT /api/employees/:employeeId (update), DELETE /api/employees/:employeeId (delete/mark departed). Use the schemas from employee_api contract. Include a /health endpoint. Server listens on port 8000.':
      '搭建 Node.js + Express 服务，使用内存存储管理员工数据。实现 GET /api/employees（列表）、GET /api/employees/:employeeId（详情）、POST /api/employees（创建）、PUT /api/employees/:employeeId（更新）、DELETE /api/employees/:employeeId（删除或标记离职），并遵循 employee_api 契约中的 schema。服务需要提供 /health 健康检查接口，并监听 8000 端口。',
    'Server starts and responds to GET /health with 200 OK.':
      '服务启动后，GET /health 返回 200 OK。',
    'All five employee endpoints return correct responses as per the API contract.':
      '五个员工接口均按照 API 契约返回正确响应。',
    'Create, read, update, delete operations work correctly on in-memory store.':
      '基于内存存储的创建、读取、更新、删除流程可正常工作。'
  }
  return translations[value] || ''
}

function generatedTaskTranslation(value: string, task: WorkflowBuildExecutionTask): string {
  /** 按任务文本中的接口、资源和技术栈生成中文说明，兜底处理历史英文任务。 */

  const lowerValue = value.toLowerCase()
  const entityLabel = taskEntityLabel(value)
  const endpoints = taskEndpointTexts(value)
  if (lowerValue.includes('crud') && lowerValue.includes('api')) {
    return `实现${entityLabel}的 CRUD API 接口${endpoints.length > 0 ? `：${endpoints.join('、')}` : ''}。`
  }
  if (lowerValue.includes('express') && lowerValue.includes('backend')) {
    return `创建 Express 后端服务，完成${entityLabel}相关接口、内存数据存储和健康检查。`
  }
  if (lowerValue.includes('in-memory')) {
    return `使用内存存储完成${entityLabel}数据的增删改查逻辑。`
  }
  if (lowerValue.includes('health')) {
    return '服务需要提供 /health 健康检查接口，并返回成功状态。'
  }
  if (endpoints.length > 0) {
    return `实现并验证接口：${endpoints.join('、')}。`
  }
  if (task.owner === 'data_source') {
    return `完成${entityLabel}后端数据与接口实现，并通过相关验证。`
  }
  if (task.owner === 'frontend') {
    return `完成页面功能实现，并通过相关验证。`
  }
  return ''
}

function taskEntityLabel(value: string): string {
  /** 从英文任务内容推导业务对象的中文名称。 */

  const lowerValue = value.toLowerCase()
  if (lowerValue.includes('employee')) return '员工'
  if (lowerValue.includes('user')) return '用户'
  if (lowerValue.includes('order')) return '订单'
  if (lowerValue.includes('product')) return '商品'
  if (lowerValue.includes('customer')) return '客户'
  return '业务数据'
}

function taskEndpointTexts(value: string): string[] {
  /** 提取任务说明中的 HTTP 接口，生成中文可读接口列表。 */

  const matches = value.match(/\b(GET|POST|PUT|PATCH|DELETE)\s+\/[A-Za-z0-9_/:.-]+/g) || []
  return dedupeStrings(matches.map((endpoint) => endpoint.replace(/\s+/, ' ')))
}

export function taskFailureReason(task: WorkflowBuildExecutionTask): string {
  /** 提取失败原因，兼容后端后续扩展的 failure_detail 文本字段。 */

  if (typeof task.failure_reason === 'string' && task.failure_reason.trim()) {
    return task.failure_reason.trim()
  }
  const detail = objectValue(task.failure_detail)
  for (const key of ['reason', 'message', 'agent_note']) {
    const value = detail[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

export function taskFailureCategoryText(category: unknown): string {
  /** 将后端失败分类转成中文标签，帮助用户定位后续修复方向。 */

  const value = typeof category === 'string' ? category : ''
  const labels: Record<string, string> = {
    runner_crash: '执行器异常',
    runner_protocol_error: '执行协议异常',
    timeout: '执行超时',
    tool_error: '工具调用失败',
    model_error: '模型调用失败',
    sandbox_error: '沙箱权限问题',
    network_error: '网络问题',
    compile_error: '编译失败',
    type_error: '类型检查失败',
    test_failure: '测试失败',
    lint_failure: '代码检查失败',
    runtime_error: '运行时错误',
    acceptance_failed: '验收未通过',
    no_file_changes: '未产生文件变更',
    contract_mismatch: '契约不匹配',
    plan_mismatch: '计划不匹配',
    workspace_snapshot_stale: '工作区快照过期',
    implementation_failure: '实现失败'
  }
  return labels[value] || value
}

export function sortBuildTasksForDisplay(
  tasks: WorkflowBuildExecutionTask[]
): WorkflowBuildExecutionTask[] {
  /** 按用户阅读进度排序任务，让已完成、运行中、失败和待执行形成稳定的执行轨迹。 */

  const originalIndex = new Map(tasks.map((task, index) => [taskId(task), index]))
  return [...tasks].sort((left, right) => {
    const statusDiff = taskStatusRank(left) - taskStatusRank(right)
    if (statusDiff !== 0) return statusDiff

    const leftTime = taskSortTime(left)
    const rightTime = taskSortTime(right)
    if (leftTime !== rightTime) return leftTime - rightTime

    return (originalIndex.get(taskId(left)) || 0) - (originalIndex.get(taskId(right)) || 0)
  })
}

function taskStatusRank(task: WorkflowBuildExecutionTask): number {
  /** 返回任务状态展示优先级，完成项沉淀在顶部，未开始项留在底部。 */

  const status = buildTaskDisplayStatus(task.status)
  if (status === 'completed') return 0
  if (status === 'running') return 1
  if (status === 'failed') return 2
  if (status === 'pending') return 3
  return 4
}

function taskSortTime(task: WorkflowBuildExecutionTask): number {
  /** 优先使用调度时间排序，同一批任务再退回到原始顺序。 */

  const taskRecord = task as WorkflowBuildExecutionTask & {
    scheduler?: Record<string, unknown>
    updated_at?: string
  }
  const candidates = [taskRecord.scheduler?.started_at, taskRecord.updated_at]
  for (const candidate of candidates) {
    if (typeof candidate !== 'string') continue
    const timestamp = Date.parse(candidate)
    if (Number.isFinite(timestamp)) return timestamp
  }
  return Number.MAX_SAFE_INTEGER
}

function dedupeStrings(values: string[]): string[] {
  /** 按出现顺序去重字符串，避免文件范围重复展示。 */

  const seen = new Set<string>()
  return values.filter((value) => {
    if (seen.has(value)) return false
    seen.add(value)
    return true
  })
}

export function taskId(task: WorkflowBuildExecutionTask): string {
  /** 读取 task 的稳定 ID，兼容 task_id 字段。 */

  return task.id || task.task_id || 'unknown-task'
}

export function taskStatusColor(status: string): string {
  /** 将任务状态映射为 Ant Design 标签颜色。 */

  if (status === 'completed') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'running') return 'purple'
  return 'default'
}

export function taskStatusText(status: string): string {
  /** 将任务状态映射为用户可读中文文案。 */

  if (status === 'completed') return '完成'
  if (status === 'failed') return '失败'
  if (status === 'running') return '运行中'
  return '待执行'
}

export function buildToolActivityPlacement(
  task: WorkflowBuildExecutionTask,
  expanded: boolean
): 'header' | 'details' | undefined {
  /** 决定实时工具活动的唯一渲染位置，任务终态或无活动时不展示。 */

  if (task.status !== 'running' || !task.activeToolActivity) return undefined
  return expanded ? 'details' : 'header'
}

/** 读取失败详情中的普通对象，避免空值干扰展示。 */
function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

/** 将执行态任务投影为确认卡共用的文件及依赖展示输入，不改写运行数据。 */
export function executionTaskPlanView(task: WorkflowBuildExecutionTask): WorkflowBuildTaskPlanTask {
  return {
    id: taskId(task),
    title: displayTaskTitle(task),
    description: task.description || '',
    backendStage: task.backendStage,
    coveredEndpoints: task.coveredEndpoints,
    dependencies: task.dependencies || task.dependsOn || [],
    dependencyTitles: task.dependencyTitles,
    target_files: [...(task.target_files || []), ...(task.targetFiles || [])],
    allowed_paths: [...(task.allowed_paths || []), ...(task.allowedPaths || [])],
    change_scope: task.change_scope,
    deliverables: task.deliverables
  }
}

/** 优先按正式 Unit 与 owner 分组，未知归属独立保留，避免漏掉平台或数据库任务。 */
export function executionTaskGroup(task: WorkflowBuildExecutionTask): string {
  const unit = task.unit_id || ''
  if (unit.startsWith('frontend:') || unit.startsWith('page:')) return 'frontend'
  if (unit.startsWith('backend:')) return 'backend'
  if (task.owner === 'frontend') return 'frontend'
  if (['backend', 'data_source', 'database'].includes(task.owner || '')) return 'backend'
  return 'other'
}
