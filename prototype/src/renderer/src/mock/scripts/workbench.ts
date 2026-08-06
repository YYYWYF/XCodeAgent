// 工作台剧本：模拟后端 AG-UI 事件流 + 生命周期，驱动
// 详情审阅 → 构建执行（Dock running）→ 授权（awaiting_authorization）→ 验收（awaiting_acceptance）→ 完成。
// Dock 模式由 lifecycle.activeExecutions[runId] 的 status / pendingInteraction.type 推导（见 planExecutionMode.ts）。

import type { ApplicationLifecycle, WorkflowRunPayload } from '../../typings'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
// 页面/接口契约基座：三应用业务模型一致（差异在 designed 状态与详设数据，由 preload 按 workspace 合并）。
import { WORKBENCH_PAGES as PAGES } from '../../../../../mock-data/pms-dev/workbench-pages'
import { mockPlanningArtifacts } from '../../../../../mock-data/pms-dev/planning-artifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { markEndpointDesigned, markPageDesigned } from '../designState'
import { nextLifecycleRevision } from './revision'

type ReplayCallbacks = {
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
}

// 页面与接口 execution 共用的最小结构，供 lifecycleWith 组装 activeExecutions。
type WorkbenchExecutionLike = {
  scope: string
  targetId: string
  pageId?: string
  threadId: string
  runId: string
  phase: string
  status: string
  startedAt: string
  updatedAt: string
  resourceKeys?: string[]
  pendingInteraction?: Record<string, unknown>
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

// 严格递增的 lifecycle revision：与 planning.ts 共用 revision.ts 的单调计数器，
// 前端 latestApplicationLifecycle 只在 revision 更大时替换；统一计数避免任何后发
// lifecycle（设计/工作台）被按 revision 拒绝合并导致 stage 冻住。
function pageMeta(pageId?: string): { id: string; label: string; path: string; purpose: string } {
  const key = pageId || 'my-projects'
  const meta = PAGES[key] || PAGES['my-projects']
  return { id: key, label: meta.label, path: meta.path, purpose: meta.purpose }
}

function workflowPageId(workflow: WorkflowRunPayload | undefined): string | undefined {
  const summary = workflow?.summary as { selectedPageId?: string } | undefined
  return summary?.selectedPageId || (workflow?.state?.selectedPageId as string | undefined)
}

// —— 接口（endpoint）目标识别 ——

// 从本次请求或 resumeState 快照解析 endpoint 目标。
// 端点确认/构建的续传消息只带 resumeState，不重复携带 selectedEndpointId，
// 因此必须同时读 workflow.state / workflow.result 中持久化的目标身份。
function resolveEndpointTarget(
  options: SendWorkflowMessageOptions,
  resume?: WorkflowRunPayload
): { apiContractId: string; endpointId: string } | undefined {
  const state = (resume?.state || {}) as Record<string, unknown>
  const result = (resume?.result || {}) as Record<string, unknown>
  const apiContractId = String(
    options.selectedApiContractId || state.selectedApiContractId || result.selectedApiContractId || ''
  ).trim()
  const endpointId = String(
    options.selectedEndpointId || state.selectedEndpointId || result.selectedEndpointId || ''
  ).trim()
  if (options.detailTargetType === 'endpoint' || (apiContractId && endpointId)) {
    return apiContractId && endpointId ? { apiContractId, endpointId } : undefined
  }
  return undefined
}

// 从 API 契约目录定位 endpoint，返回展示与构建所需元信息。
function endpointMeta(
  apiContractId: string,
  endpointId: string
): { apiContractId: string; endpointId: string; label: string; method: string; path: string; summary: string; contractLabel: string } | undefined {
  const contract = mockPlanningArtifacts.apiContracts.find((item) => item.id === apiContractId)
  if (!contract) return undefined
  const endpoint = contract.endpoints.find(
    (item, index) => (item.id || String(index + 1)) === endpointId
  )
  if (!endpoint) return undefined
  const method = String(endpoint.method || 'GET').toUpperCase()
  const path = String(endpoint.path || '')
  return {
    apiContractId,
    endpointId,
    label: `${method} ${path}`.trim(),
    method,
    path,
    summary: String(endpoint.summary || ''),
    contractLabel: contract.label
  }
}

// 生成 endpoint 详细设计审阅对象。data_origin 固定为已解析的 mysql_existing，
// 避免 isNeedsUserConfirmationDataOrigin 命中 DataOriginDecisionField 拦截确认按钮。
function endpointReviewFor(
  meta: ReturnType<typeof endpointMeta>
): Record<string, unknown> {
  if (!meta) {
    return {
      target_type: 'endpoint',
      target_id: 'endpoint',
      name: '接口',
      method: 'GET',
      path: '/api/unknown',
      data_origin: {
        source_type: 'mysql_existing',
        effective_source: { kind: 'mysql_existing', database: 'wh_branch', tables: ['business_table'] },
        differences: []
      },
      endpoint_decision: { behavior: '读取并返回业务数据。' },
      interface_design: { response_schema: '统一响应信封' },
      acceptance_criteria: ['接口返回统一响应信封']
    }
  }
  const rich = RICH_ENDPOINT_REVIEWS[meta.endpointId]
  if (rich) return rich(meta)
  return genericEndpointReview(meta)
}

// 目标表名：取路径末段去 s 复数，如 /api/inbound/orders → inbound_order。
function endpointTable(meta: NonNullable<ReturnType<typeof endpointMeta>>): string {
  const segment = meta.path.split('/').filter(Boolean).pop() || 'business_table'
  return segment.replace(/s$/i, '')
}

function genericEndpointReview(meta: NonNullable<ReturnType<typeof endpointMeta>>): Record<string, unknown> {
  const table = endpointTable(meta)
  const isWrite = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(meta.method)
  const decision =
    meta.method === 'GET'
      ? { behavior: `按查询条件读取 ${table} 数据并分页返回。`, side_effects: [], idempotent: true }
      : { behavior: `写入 ${table} 并返回处理结果。`, side_effects: ['更新目标数据表'], idempotent: false }
  return {
    target_type: 'endpoint',
    target_id: `${meta.apiContractId}:${meta.endpointId}`,
    name: meta.label,
    api_contract_id: meta.apiContractId,
    endpoint_id: meta.endpointId,
    data_source_id: 'wh-branch-db',
    method: meta.method,
    path: meta.path,
    summary: meta.summary,
    data_usage: {
      served_pages: [meta.contractLabel],
      data_purpose: meta.summary || '支撑对应业务页面的数据读写。',
      data_scope: isWrite ? '写操作' : '读操作'
    },
    data_origin: {
      source_type: 'mysql_existing',
      effective_source: { kind: 'mysql_existing', database: 'wh_branch', tables: [table] },
      field_mappings: [],
      differences: [],
      notes: ['复用现有业务表，不新增表结构。']
    },
    endpoint_decision: {
      behavior: decision.behavior,
      side_effects: decision.side_effects,
      idempotent: decision.idempotent,
      error_handling: ['参数校验失败返回 400', '数据不存在返回 404', '服务异常返回 500']
    },
    interface_design: {
      request_schema: isWrite ? `${meta.endpointId}Request` : '查询参数',
      response_schema: '统一响应信封：{ code, data, message }',
      response_fields: ['code', 'data', 'message'],
      errors: ['400 参数错误', '404 不存在', '500 服务异常']
    },
    processing_logic: isWrite
      ? ['校验请求参数与业务规则', '执行数据写入并校验影响行数', '记录操作日志']
      : ['解析查询条件', '分页读取数据', '返回规范响应'],
    dependent_pages: [],
    acceptance_criteria: [
      `调用 ${meta.method} ${meta.path} 返回统一响应信封`,
      `非法参数返回 400 且不产生副作用`,
      `接口只访问项目计划中已声明的数据表`
    ],
    risks: isWrite
      ? [{ level: 'medium', description: '写操作涉及数据表变更，需复核影响范围' }]
      : []
  }
}

// 高频演示接口的专属详情（与页面详情一致，使用真实后端 prompt 风格）。
const RICH_ENDPOINT_REVIEWS: Record<string, (meta: NonNullable<ReturnType<typeof endpointMeta>>) => Record<string, unknown>> = {
  'ep-my-projects': (meta) => ({
    target_type: 'endpoint',
    target_id: `${meta.apiContractId}:${meta.endpointId}`,
    name: meta.label,
    api_contract_id: meta.apiContractId,
    endpoint_id: meta.endpointId,
    data_source_id: 'wh-branch-db',
    method: meta.method,
    path: meta.path,
    summary: meta.summary,
    data_usage: {
      served_pages: ['我的项目'],
      data_purpose: '支撑「我的项目」页的项目列表分页查询与筛选。',
      data_scope: '读操作',
      read_pattern: '分页 + 状态/关键字筛选'
    },
    data_origin: {
      source_type: 'mysql_existing',
      effective_source: { kind: 'mysql_existing', database: 'wh_branch', tables: ['project', 'project_member'] },
      field_mappings: [
        { target_field: 'project_id', source: 'project.id' },
        { target_field: 'owner', source: 'project_member.user_id' },
        { target_field: 'status', source: 'project.status' }
      ],
      differences: [],
      notes: ['按当前登录用户过滤负责人/成员，project 与 project_member 联查。']
    },
    endpoint_decision: {
      behavior: '按当前用户与状态/关键字过滤，分页返回我的项目。',
      side_effects: [],
      idempotent: true,
      sorting: 'created_at DESC',
      pagination: 'limit/offset'
    },
    interface_design: {
      request_schema: 'GET /api/projects/my?status=&keyword=&page=&size=',
      response_schema: '{ code, data: { list, total }, message }',
      response_fields: ['list', 'total', 'code', 'message'],
      errors: ['400 参数错误', '500 服务异常']
    },
    processing_logic: [
      '解析筛选与分页参数',
      '按当前用户过滤后分页查询 project',
      '统计总数并返回列表'
    ],
    dependent_pages: [{ page_id: 'my-projects', usage: 'read' }],
    acceptance_criteria: [
      '仅返回当前用户负责/参与的项目',
      '按状态与关键字筛选生效',
      '分页返回且 total 准确'
    ],
    risks: []
  }),
  'ep-recheck-create': (meta) => ({
    target_type: 'endpoint',
    target_id: `${meta.apiContractId}:${meta.endpointId}`,
    name: meta.label,
    api_contract_id: meta.apiContractId,
    endpoint_id: meta.endpointId,
    data_source_id: 'wh-branch-db',
    method: meta.method,
    path: meta.path,
    summary: meta.summary,
    data_usage: {
      served_pages: ['我的回检'],
      data_purpose: '支撑「我的回检」页提交需求回检单。',
      data_scope: '写操作',
      write_pattern: '事务性创建'
    },
    data_origin: {
      source_type: 'mysql_existing',
      effective_source: { kind: 'mysql_existing', database: 'wh_branch', tables: ['recheck'] },
      field_mappings: [
        { target_field: 'recheck_no', source: 'recheck.recheck_no' },
        { target_field: 'status', source: 'recheck.status' }
      ],
      differences: [],
      notes: ['回检单状态初始为待审核。']
    },
    endpoint_decision: {
      behavior: '校验项目与达成情况必填后，创建回检单并置为待审核。',
      side_effects: ['写入 recheck'],
      idempotent: false,
      transaction: true
    },
    interface_design: {
      request_schema: '{ project_id, achievement, issues, suggestion }',
      response_schema: '{ code, data: { recheck_id }, message }',
      response_fields: ['recheck_id', 'code', 'message'],
      errors: ['400 达成情况为空', '500 服务异常']
    },
    processing_logic: [
      '校验项目与达成情况必填',
      '事务写入回检单',
      '返回回检单号并置待审核'
    ],
    dependent_pages: [{ page_id: 'my-rechecks', usage: 'write' }],
    acceptance_criteria: [
      '达成情况为空时拒绝提交',
      '提交成功返回 recheck_id 且状态为待审核'
    ],
    risks: [{ level: 'medium', description: '写操作涉及回检单，需事务保护' }]
  }),
  'ep-recheck-review': (meta) => ({
    target_type: 'endpoint',
    target_id: `${meta.apiContractId}:${meta.endpointId}`,
    name: meta.label,
    api_contract_id: meta.apiContractId,
    endpoint_id: meta.endpointId,
    data_source_id: 'wh-branch-db',
    method: meta.method,
    path: meta.path,
    summary: meta.summary,
    data_usage: {
      served_pages: ['回检审核'],
      data_purpose: '回检审核人对待审核回检单进行通过/驳回处理。',
      data_scope: '写操作（高影响）',
      write_pattern: '状态流转 + 整改要求'
    },
    data_origin: {
      source_type: 'mysql_existing',
      effective_source: { kind: 'mysql_existing', database: 'wh_branch', tables: ['recheck'] },
      field_mappings: [
        { target_field: 'status', source: 'recheck.status' },
        { target_field: 'review_comment', source: 'recheck.review_comment' }
      ],
      differences: [],
      notes: ['通过置完成；驳回记录整改要求并回流填报人。']
    },
    endpoint_decision: {
      behavior: '校验回检单为待审核后按 decision 流转状态，驳回必填整改要求。',
      side_effects: ['更新 recheck.status', '写入 recheck.review_comment'],
      idempotent: false,
      transaction: true,
      guard: '驳回必须填写整改要求'
    },
    interface_design: {
      request_schema: 'PUT /api/rechecks/{id}/review { decision: approved|rejected, comment }',
      response_schema: '{ code, data: { status }, message }',
      response_fields: ['status', 'code', 'message'],
      errors: ['409 状态非法', '400 驳回缺整改要求', '500 服务异常']
    },
    processing_logic: [
      '校验回检单处于待审核状态',
      '通过置完成；驳回记录整改要求并回流填报人',
      '返回最新状态'
    ],
    dependent_pages: [{ page_id: 'recheck-review', usage: 'write' }],
    acceptance_criteria: [
      '通过/驳回操作可用',
      '驳回必填整改要求',
      '重复审核同一单返回 409'
    ],
    risks: [{ level: 'high', description: '审核结果影响回检闭环，驳回需整改要求' }]
  })
}

// 构造执行条目（驱动底部 Dock）。
function exec(
  runId: string,
  threadId: string,
  pageId: string,
  phase: string,
  status: string,
  pendingInteraction?: Record<string, unknown>
) {
  const now = new Date().toISOString()
  return {
    scope: 'page',
    targetId: pageId,
    pageId,
    threadId,
    runId,
    phase,
    status,
    startedAt: now,
    updatedAt: now,
    ...(pendingInteraction ? { pendingInteraction } : {})
  }
}

// 构造携带 execution 的 ApplicationLifecycle。
function lifecycleWith(
  base: ApplicationLifecycle,
  runId: string,
  execution: WorkbenchExecutionLike
): ApplicationLifecycle {
  return {
    ...base,
    revision: nextLifecycleRevision(),
    activeExecutions: { [runId]: execution }
  } as ApplicationLifecycle
}

// 组装 WorkflowRunPayload：lifecycle 同时放入 summary.lifecycle / state / result（与 withWorkflowExecutionStatus 一致）。
function wf(
  threadId: string,
  runId: string,
  phase: string,
  status: string,
  lifecycle: ApplicationLifecycle | undefined,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {}
): WorkflowRunPayload {
  const payload: Record<string, unknown> = {
    runId,
    threadId,
    summary: { phase, status, message: '', ...(lifecycle ? { lifecycle } : {}) },
    events: [{ type: 'workflow.node.started', nodeName: phase }],
    state: lifecycle ? { ...state, lifecycle } : state,
    result: lifecycle ? { lifecycle } : {},
    ...extra
  }
  return payload as unknown as WorkflowRunPayload
}

// 授权交互（AgentApprovalCard 读取 payload 的 title/subject/risk 等）。
function approvalInteraction(message: string) {
  return {
    id: `pi-approval-${Date.now()}`,
    type: 'agent_approval',
    basedOnRevision: 1,
    payload: {
      title: '需要审批',
      subject: '读取工作区文件并生成前端代码',
      description: 'Agent 请求执行以下操作，确认后继续。',
      risk: { level: 'medium', reasons: ['涉及工作区文件写入', '会自动创建/修改前端代码'] },
      details: 'workspaceRoot=C:\\Users\\WX\\Documents\\ExampleWorkspace\\wh-branch-pms\n目标页面：我的项目'
    },
    createdAt: new Date().toISOString()
  }
}

// 页面验收交互。
function acceptanceInteraction() {
  return {
    id: `pi-acceptance-${Date.now()}`,
    type: 'page_acceptance',
    basedOnRevision: 1,
    payload: { message: '页面已准备好，等待最终验收。' },
    createdAt: new Date().toISOString()
  }
}

// 页面设计详情审阅产物。lifecycle 必须携带 execution 的 pendingInteraction（page_design_confirmation），
// 否则前端 workflowInteractionAvailability 判为 stale，确认按钮被禁用。
function detailReviewPayload(
  threadId: string,
  runId: string,
  pageId: string,
  lifecycle: ApplicationLifecycle,
  pageDesigns: Record<string, Record<string, unknown>>
): WorkflowRunPayload {
  const page = pageMeta(pageId)
  return wf(threadId, runId, 'detail_confirmation', 'requires_user_input', lifecycle, {
    // workflowInteractionAvailability 从 state.lifecycle 读 pendingInteraction 快照，
    // 而 wf() 只把 lifecycle 放 summary.lifecycle —— 不放 state 会导致确认卡判 stale 不渲染。
    lifecycle,
    clarification: {
      mode: 'detail_review',
      status: 'requires_user_input',
      message: '页面需求文档已生成，请确认或补充',
      review: {
        pages: [
          pageDesigns[page.id] || {
            target_type: 'page',
            target_id: page.id,
            name: page.label,
            path: page.path,
            page_goal: page.purpose,
            basic_layout: { overall: '顶栏 + 左筛选 + 右列表', regions: [{ name: '筛选区' }, { name: '列表区' }] },
            interactions: ['点击行查看项目详情', '新建项目弹窗', '状态筛选'],
            state_feedback: [{ state: '加载中', feedbackComponent: 'Skeleton' }, { state: '空数据', feedbackComponent: 'Empty' }],
            response_bindings: [{ sourcePath: 'data.list', target: 'table.rows' }],
            api_dependencies: [{ endpointId: 'ep-my-projects', method: 'GET', path: '/api/projects/my' }],
            acceptance_criteria: ['可按状态与关键字筛选', '列表分页展示', '新建项目校验必填']
          }
        ],
        summary: {
          page_count: 1,
          endpoint_count: 0,
          api_contract_count: 1,
          selectedPageId: page.id,
          detailTargetType: 'page'
        }
      }
    }
  })
}

// 构建任务卡（buildExecutionSlice）：通用 3 任务。
function buildExecutionSliceFor(pageId: string, pageLabel: string): Record<string, unknown> {
  const tasks = [
    { id: `task-${pageId}-0`, task_id: `task-${pageId}-0`, unit_id: `page:${pageId}`, owner: 'frontend', title: `新增 ${pageLabel} 页面组件`, status: 'completed', target_files: [`frontend/src/pages/${pageId}/index.tsx`] },
    { id: `task-${pageId}-1`, task_id: `task-${pageId}-1`, unit_id: `page:${pageId}`, owner: 'frontend', title: `对接 ${pageLabel} 相关 API`, status: 'completed' },
    { id: `task-${pageId}-2`, task_id: `task-${pageId}-2`, unit_id: `page:${pageId}`, owner: 'frontend', title: `注册路由 /${pageId}`, status: 'completed' }
  ]
  return {
    scope: { type: 'page', id: pageId, label: pageLabel },
    target_unit_ids: [`page:${pageId}`],
    tasks,
    summary: { total: tasks.length, completed: tasks.length, pending: 0, running: 0, failed: 0 }
  }
}

// 集成测试检查矩阵（通用 3 项通过）。
function integrationChecks(): unknown[] {
  return [
    { id: 'check-contract', name: 'API 契约一致性校验', status: 'passed', required: true, evidence: '响应字段与契约定义一致。' },
    { id: 'check-route', name: '页面路由注册校验', status: 'passed', required: true, evidence: '路由已挂载，可正常访问。' },
    { id: 'check-render', name: '页面渲染校验', status: 'passed', required: false, evidence: '页面无运行时报错，关键交互可用。' }
  ]
}

// 接口构建的集成测试检查矩阵（契约 + 数据来源 + 联调）。
function endpointIntegrationChecks(): unknown[] {
  return [
    { id: 'check-contract', name: 'API 契约一致性校验', status: 'passed', required: true, evidence: '接口响应字段与契约定义一致。' },
    { id: 'check-db', name: '数据库结构与数据来源校验', status: 'passed', required: true, evidence: '目标表结构与 data_origin 方案一致。' },
    { id: 'check-e2e', name: '接口集成联调校验', status: 'passed', required: false, evidence: '接口可正常调用，关联页面数据可读。' }
  ]
}

// 构造接口执行的底部 Dock 条目（scope='endpoint' + resourceKeys 供 planExecutionContextForEndpoint 匹配）。
function execEndpoint(
  runId: string,
  threadId: string,
  apiContractId: string,
  endpointId: string,
  phase: string,
  status: string,
  pendingInteraction?: Record<string, unknown>
) {
  const now = new Date().toISOString()
  return {
    scope: 'endpoint',
    targetId: endpointId,
    resourceKeys: [`endpoint:${apiContractId}:${endpointId}`],
    threadId,
    runId,
    phase,
    status,
    startedAt: now,
    updatedAt: now,
    ...(pendingInteraction ? { pendingInteraction } : {})
  }
}

// 组装接口 WorkflowRunPayload：state/result 持久化 endpoint 身份，
// 供续传消息（只带 resumeState）恢复目标与 chatSessions 保存会话归属。
function endpointWf(
  threadId: string,
  runId: string,
  phase: string,
  status: string,
  apiContractId: string,
  endpointId: string,
  detailTargetType: string,
  lifecycle: ApplicationLifecycle | undefined,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {}
): WorkflowRunPayload {
  const identity = {
    selectedApiContractId: apiContractId,
    selectedEndpointId: endpointId,
    detailTargetType
  }
  const payload: Record<string, unknown> = {
    runId,
    threadId,
    summary: { phase, status, message: '', ...(lifecycle ? { lifecycle } : {}) },
    events: [{ type: 'workflow.node.started', nodeName: phase }],
    state: { ...state, ...identity, ...(lifecycle ? { lifecycle } : {}) },
    result: { ...identity, ...(lifecycle ? { lifecycle } : {}) },
    ...extra
  }
  return payload as unknown as WorkflowRunPayload
}

// 接口详细设计审阅产物。
function endpointReviewPayload(
  threadId: string,
  runId: string,
  apiContractId: string,
  endpointId: string,
  lifecycle: ApplicationLifecycle
): WorkflowRunPayload {
  const meta = endpointMeta(apiContractId, endpointId)
  const review = endpointReviewFor(meta)
  return endpointWf(threadId, runId, 'detail_confirmation', 'requires_user_input', apiContractId, endpointId, 'endpoint', lifecycle, {
    clarification: {
      mode: 'detail_review',
      status: 'requires_user_input',
      message: `请审阅接口 \`${endpointId}\` 详细设计；仅展开需要调整的对象。`,
      review: {
        pages: [],
        endpoints: [review],
        summary: {
          page_count: 0,
          endpoint_count: 1,
          api_contract_count: mockPlanningArtifacts.apiContracts.length,
          selectedApiContractId: apiContractId,
          selectedEndpointId: endpointId,
          detailTargetType: 'endpoint'
        }
      }
    }
  })
}

// 接口构建任务卡（buildExecutionSlice）：后端 + 数据库任务。
function endpointBuildExecutionSlice(
  apiContractId: string,
  endpointId: string,
  label: string,
  table: string
): Record<string, unknown> {
  const unitId = `endpoint:${apiContractId}:${endpointId}`
  const tasks = [
    { id: `task-${endpointId}-0`, task_id: `task-${endpointId}-0`, unit_id: unitId, owner: 'backend', title: `新增接口 ${label} 的 Controller/Service`, status: 'completed', target_files: [`backend/src/main/java/.../controller/`] },
    { id: `task-${endpointId}-1`, task_id: `task-${endpointId}-1`, unit_id: unitId, owner: 'backend', title: `实现 Mapper 与实体映射（${table}）`, status: 'completed' },
    { id: `task-${endpointId}-2`, task_id: `task-${endpointId}-2`, unit_id: unitId, owner: 'database', title: `校验并应用数据表结构（${table}）`, status: 'completed' }
  ]
  return {
    scope: { type: 'endpoint', id: `${apiContractId}:${endpointId}`, label },
    target_unit_ids: [unitId],
    tasks,
    summary: { total: tasks.length, completed: tasks.length, pending: 0, running: 0, failed: 0 }
  }
}

// 接口（endpoint）工作台剧本：接口详细审阅 → 构建链（检查工作区 → 获取数据库信息 →
// 规划任务 → 生成接口代码 → 集成测试）→ 验收。与页面剧本共用 lifecycle/revision 机制，
// 但 scope='endpoint'，构建链多一个 inspect_database_context 节点。
async function replayEndpointWorkbench(
  threadId: string,
  target: { apiContractId: string; endpointId: string },
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload | undefined> {
  const { onContent, onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-endpoint-${Date.now()}`
  const meta = endpointMeta(target.apiContractId, target.endpointId) || {
    apiContractId: target.apiContractId,
    endpointId: target.endpointId,
    label: target.endpointId,
    method: 'GET',
    path: '/api/unknown',
    summary: '',
    contractLabel: ''
  }
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>

  const baseLifecycle = {
    schemaVersion: '1.2.0',
    application: {
      id: options.application?.id || 'app-pms-new',
      name: options.application?.appName || options.application?.name || '武汉分行项目管理系统'
    },
    updatedAt: new Date().toISOString(),
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {}
  } as ApplicationLifecycle

  const emitLifecycle = (execution: ReturnType<typeof execEndpoint>): ApplicationLifecycle => {
    const lifecycle = lifecycleWith(baseLifecycle, runId, execution)
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }
  const emit = (phase: string, status: string, lifecycle: ApplicationLifecycle | undefined, state = {}, extra: Partial<WorkflowRunPayload> = {}): WorkflowRunPayload => {
    const payload = endpointWf(threadId, runId, phase, status, meta.apiContractId, meta.endpointId, 'endpoint', lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  // 1. 结束 / 暂停计划。
  if (options.planControlAction === 'end') {
    emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'finalize_project', 'completed'))
    return endpointWf(threadId, runId, 'finalize_project', 'completed', meta.apiContractId, meta.endpointId, 'endpoint', undefined, {}, { summary: { phase: 'finalize_project', status: 'completed', message: '计划已结束' } })
  }
  if (options.planControlAction === 'stop') {
    const lifecycle = emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'stopped'))
    return endpointWf(threadId, runId, 'build', 'stopped', meta.apiContractId, meta.endpointId, 'endpoint', lifecycle)
  }

  // 2. 验收通过 → 计划完成。
  if (answers.page_acceptance) {
    const lifecycle = emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'acceptance', 'completed'))
    onContent?.(`接口 ${meta.label} 已完成交付，可继续设计其他页面或接口。`)
    return endpointWf(threadId, runId, 'acceptance', 'completed', meta.apiContractId, meta.endpointId, 'endpoint', lifecycle)
  }

  // 3. 详情审阅确认 → 完整构建链 → 等待验收。
  if (answers.detail_review || resume) {
    markEndpointDesigned(meta.apiContractId, meta.endpointId)
    const table = endpointTable(meta)
    const steps: Record<string, unknown>[] = []
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.([...steps] as ProcessStepRecord[])
    }

    // inspect_workspace
    onContent?.('已确认设计，正在检查工作区结构…')
    emit('inspect_workspace', 'completed', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'inspect_workspace', 'completed')))
    pushStep({ id: 'step-inspect', kind: 'workflow', status: 'completed', title: '检查工作区结构', detail: '已扫描后端工程目录、数据库连接配置与既有接口约定。', sequence: 1 })
    await delay(600)

    // inspect_database_context（接口有数据来源，比页面多这一节点）
    onContent?.('正在获取数据库上下文…')
    emit('inspect_database_context', 'running', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'inspect_database_context', 'running')))
    await delay(700)
    emit('inspect_database_context', 'completed', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'inspect_database_context', 'completed')))
    pushStep({ id: 'step-db', kind: 'workflow', status: 'completed', title: '获取数据库信息', detail: `已读取目标表 ${table} 结构与数据源配置，确认接口数据来源。`, sequence: 2 })
    await delay(300)

    // prepare_build_tasks
    onContent?.('正在规划构建任务（DAG）…')
    emit('prepare_build_tasks', 'running', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'prepare_build_tasks', 'running')))
    await delay(800)
    emit('prepare_build_tasks', 'completed', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'prepare_build_tasks', 'completed')))
    pushStep({ id: 'step-prepare', kind: 'workflow', status: 'completed', title: '规划构建任务（DAG）', detail: `已为接口 ${meta.label} 拆解构建任务并编译执行 DAG。`, sequence: 3 })
    await delay(300)

    // build
    onContent?.('正在生成接口代码…')
    emit('build', 'running', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'running')))
    await delay(1200)
    emit('build', 'completed', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'completed')))
    pushStep({ id: 'step-build', kind: 'workflow', status: 'completed', title: '生成接口代码', detail: '', sequence: 4, buildExecutionSlice: endpointBuildExecutionSlice(meta.apiContractId, meta.endpointId, meta.label, table) })
    await delay(300)

    // integration_test
    onContent?.('正在执行集成测试…')
    emit('integration_test', 'running', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'integration_test', 'running')))
    await delay(900)
    emit('integration_test', 'completed', emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'integration_test', 'completed')))
    pushStep({ id: 'step-test', kind: 'workflow', status: 'completed', title: '执行集成测试', detail: '', sequence: 5, checks: endpointIntegrationChecks() })
    await delay(300)

    // launch_project：接口服务就绪，等待验收
    onContent?.(`集成测试已通过，接口服务已就绪。\n\n请在右侧预览验证接口效果，确认验收。`)
    const launchLifecycle = emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'launch_project', 'awaiting_user', acceptanceInteraction()))
    return endpointWf(
      threadId,
      runId,
      'launch_project',
      'requires_user_input',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      launchLifecycle,
      {
        clarification: { mode: 'page_acceptance', status: 'requires_user_input', message: '请预览并完成接口验收。', questions: [] }
      },
      {
        summary: { phase: 'launch_project', status: 'requires_user_input', message: '接口服务已就绪，请验收' },
        result: { preview_url: window.location.origin }
      }
    )
  }

  // 4. 开始接口详细设计 → 接口详情审阅。
  if (options.selectedEndpointId || options.detailTargetType) {
    onContent?.(`正在为接口 ${meta.label} 生成详细设计…`)
    await delay(700)
    markEndpointDesigned(meta.apiContractId, meta.endpointId)
    const pending = {
      id: `pi-endpoint-design-${Date.now()}`,
      type: 'page_design_confirmation',
      basedOnRevision: 1,
      payload: { message: '接口设计已生成，等待确认' },
      createdAt: new Date().toISOString()
    }
    const lifecycle = emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'detail_confirmation', 'awaiting_user', pending))
    const payload = endpointReviewPayload(threadId, runId, meta.apiContractId, meta.endpointId, lifecycle)
    onContent?.(`已为接口 ${meta.label} 生成详细设计，请确认后开始构建。`)
    onWorkflow?.(payload)
    return payload
  }

  // 5. 其它 → 最小 running 态。
  const fallback = emitLifecycle(execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'running'))
  return endpointWf(threadId, runId, 'build', 'running', meta.apiContractId, meta.endpointId, 'endpoint', fallback)
}

export async function replayWorkbench(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload | undefined> {
  const { onContent, onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  // 接口目标优先于页面：选中接口或续传快照带接口身份时走接口剧本。
  const endpointTarget = resolveEndpointTarget(options, resume)
  if (endpointTarget) {
    return replayEndpointWorkbench(threadId, endpointTarget, options, callbacks)
  }
  const runId = resume?.runId || `mock-run-${Date.now()}`
  const page = pageMeta(options.selectedPageId || workflowPageId(resume))
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  // 按当前应用工作区取页面详设数据（pms-dev 有完整详设，其它应用走兜底）。
  const pageDesigns = appDataByWorkspace(options.application?.workspaceRoot).pageDesigns as Record<
    string,
    Record<string, unknown>
  >

  const baseLifecycle = {
    schemaVersion: '1.2.0',
    application: {
      id: options.application?.id || 'app-pms-new',
      name: options.application?.appName || options.application?.name || '武汉分行项目管理系统'
    },
    updatedAt: new Date().toISOString(),
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {}
  } as ApplicationLifecycle

  const emit = (phase: string, status: string, lifecycle: ApplicationLifecycle | undefined, state = {}, extra: Partial<WorkflowRunPayload> = {}): WorkflowRunPayload => {
    const payload = wf(threadId, runId, phase, status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }
  const emitLifecycle = (execution: ReturnType<typeof exec>): ApplicationLifecycle => {
    const lifecycle = lifecycleWith(baseLifecycle, runId, execution)
    onApplicationLifecycle?.(lifecycle)
    // 注册为当前权威 lifecycle，后续 getApplicationLifecycle 会返回它（含 activeExecutions）。
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }

  // 1. 结束 / 暂停计划。
  if (options.planControlAction === 'end') {
    emitLifecycle(exec(runId, threadId, page.id, 'finalize_project', 'completed'))
    return emit('finalize_project', 'completed', undefined, {}, { summary: { phase: 'finalize_project', status: 'completed', message: '计划已结束' } })
  }
  if (options.planControlAction === 'stop') {
    const lifecycle = emitLifecycle(exec(runId, threadId, page.id, 'build', 'stopped'))
    return emit('build', 'stopped', lifecycle)
  }

  // 2. 验收通过 → 计划完成。
  if (answers.page_acceptance) {
    const lifecycle = emitLifecycle(exec(runId, threadId, page.id, 'acceptance', 'completed'))
    onContent?.(`「${page.label}」已完成交付，可继续设计其他页面或接口。`)
    return emit('acceptance', 'completed', lifecycle)
  }

  // 3. 授权决策 → 继续构建 → 等待验收。
  if (answers.agent_approval) {
    onContent?.('已授权，继续执行开发任务…')
    const running = emitLifecycle(exec(runId, threadId, page.id, 'build', 'running'))
    emit('build', 'running', running)
    await delay(900)
    const awaiting = emitLifecycle(exec(runId, threadId, page.id, 'build', 'awaiting_user', acceptanceInteraction()))
    // 验收态 workflow 需带 clarification.mode='page_acceptance'，
    // 否则 pageAcceptanceContinuationMessage 返回空、验收提交被拦截。
    return emit('acceptance', 'requires_user_input', awaiting, {
      lifecycle: awaiting,
      clarification: {
        mode: 'page_acceptance',
        status: 'requires_user_input',
        message: '请预览页面并完成最终验收。',
        questions: []
      }
    }, { summary: { phase: 'acceptance', status: 'requires_user_input', message: '页面已准备好，等待最终验收' } })
  }

  // 4. 详情审阅确认 → 完整构建链 → 等待验收。
  // 按真实节点链（workflow.py）：inspect_workspace → prepare_build_tasks → build
  // → integration_test → launch_project(page_acceptance + preview_url)。
  // agent_approval 仅数据库高危操作才触发，普通页面构建默认不走。
  if (answers.detail_review || resume) {
    markPageDesigned(page.id)
    const steps: Record<string, unknown>[] = []
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.([...steps] as ProcessStepRecord[])
    }

    // inspect_workspace
    onContent?.('已确认设计，正在检查工作区结构…')
    emit('inspect_workspace', 'completed', emitLifecycle(exec(runId, threadId, page.id, 'inspect_workspace', 'completed')))
    pushStep({ id: 'step-inspect', kind: 'workflow', status: 'completed', title: '检查工作区结构', detail: '已扫描前端工程目录结构、入口文件与既有约定。', sequence: 1 })
    await delay(600)

    // prepare_build_tasks
    onContent?.('正在规划构建任务（DAG）…')
    emit('prepare_build_tasks', 'running', emitLifecycle(exec(runId, threadId, page.id, 'prepare_build_tasks', 'running')))
    await delay(800)
    emit('prepare_build_tasks', 'completed', emitLifecycle(exec(runId, threadId, page.id, 'prepare_build_tasks', 'completed')))
    pushStep({ id: 'step-prepare', kind: 'workflow', status: 'completed', title: '规划构建任务（DAG）', detail: `已为「${page.label}」拆解构建任务并编译执行 DAG。`, sequence: 2 })
    await delay(300)

    // build
    onContent?.('正在生成页面代码…')
    emit('build', 'running', emitLifecycle(exec(runId, threadId, page.id, 'build', 'running')))
    await delay(1200)
    emit('build', 'completed', emitLifecycle(exec(runId, threadId, page.id, 'build', 'completed')))
    pushStep({ id: 'step-build', kind: 'workflow', status: 'completed', title: '生成页面代码', detail: '', sequence: 3, buildExecutionSlice: buildExecutionSliceFor(page.id, page.label) })
    await delay(300)

    // integration_test
    onContent?.('正在执行集成测试…')
    emit('integration_test', 'running', emitLifecycle(exec(runId, threadId, page.id, 'integration_test', 'running')))
    await delay(900)
    emit('integration_test', 'completed', emitLifecycle(exec(runId, threadId, page.id, 'integration_test', 'completed')))
    pushStep({ id: 'step-test', kind: 'workflow', status: 'completed', title: '执行集成测试', detail: '', sequence: 4, checks: integrationChecks() })
    await delay(300)

    // launch_project：启动预览，返回 preview_url，等待验收（page_acceptance）
    onContent?.(`集成测试已通过，预览已启动。\n\n请在右侧预览查看效果，确认验收。`)
    const launchLifecycle = emitLifecycle(exec(runId, threadId, page.id, 'launch_project', 'awaiting_user', acceptanceInteraction()))
    return emit(
      'launch_project',
      'requires_user_input',
      launchLifecycle,
      {
        clarification: { mode: 'page_acceptance', status: 'requires_user_input', message: '请预览页面并完成最终验收。', questions: [] }
      },
      {
        summary: { phase: 'launch_project', status: 'requires_user_input', message: '预览已就绪，请验收' },
        result: { preview_url: window.location.origin }
      }
    )
  }

  // 5. 开始页面设计（DetailConfirmationPageSelector 点"开始生成"）→ 详情审阅。
  if (options.selectedPageId || options.detailTargetType || options.originalRequest) {
    onContent?.(`正在为「${page.label}」生成页面需求文档…`)
    // 注：不在开始设计时 markPageDesigned——「已设计」仅在用户确认详细设计(detail_review)后标记。
    // 详情审阅需要 lifecycle 快照中的 pendingInteraction（page_design_confirmation）才能提交确认。
    // 生成中以 processSteps 承载 5 阶段（与构建链一致：Agent 正在执行→已归档 N 个步骤），完成后留历史。
    const designSteps: Array<{ id: string; title: string; detail: string }> = [
      { id: 'design-context', title: '汇总应用上下文', detail: '整合需求文档、项目计划与页面目标。' },
      { id: 'design-scope', title: '梳理页面范围', detail: '明确页面职责、核心功能与关键用户路径。' },
      { id: 'design-breakdown', title: '拆解功能与数据', detail: '整理功能点、数据展示与交互依赖。' },
      { id: 'design-edge', title: '补齐边界与验收', detail: '定义异常态、边界约束与验收标准。' },
      { id: 'design-summary', title: '汇总需求文档', detail: '整理为可确认的页面需求文档。' }
    ]
    const steps: ProcessStepRecord[] = []
    const pushStep = (step: ProcessStepRecord): void => {
      steps.push(step)
      onProcessSteps?.([...steps])
    }
    onWorkflow?.(
      wf(threadId, runId, 'detail_confirmation', 'running', undefined, {
        summary: { phase: 'detail_confirmation', status: 'running', message: '正在生成页面需求文档…' }
      })
    )
    for (let i = 0; i < designSteps.length; i += 1) {
      const stage = designSteps[i]
      await delay(650)
      pushStep({
        id: `step-design-${i}`,
        kind: 'workflow',
        status: 'completed',
        title: stage.title,
        detail: stage.detail,
        nodeName: 'detail_confirmation',
        sequence: i + 1
      })
    }
    onWorkflow?.(wf(threadId, runId, 'detail_confirmation', 'completed', undefined, {
      summary: { phase: 'detail_confirmation', status: 'completed', message: '页面需求文档已生成' }
    }))
    await delay(300)
    const pending = {
      id: `pi-design-${Date.now()}`,
      type: 'page_design_confirmation',
      basedOnRevision: 1,
      payload: { message: '页面需求文档已生成，等待确认' },
      createdAt: new Date().toISOString()
    }
    const lifecycle = emitLifecycle(exec(runId, threadId, page.id, 'detail_confirmation', 'awaiting_user', pending))
    const payload = detailReviewPayload(threadId, runId, page.id, lifecycle, pageDesigns)
    onContent?.(`已为「${page.label}」生成页面需求文档，请确认或补充后开始构建。`)
    onWorkflow?.(payload)
    return payload
  }

  // 6. 其它（自由聊天/未知）→ 最小 running 态，不崩。
  const fallback = emitLifecycle(exec(runId, threadId, page.id, 'build', 'running'))
  return emit('build', 'running', fallback)
}
