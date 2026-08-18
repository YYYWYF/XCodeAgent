// 工作台剧本：模拟后端 AG-UI 事件流 + 生命周期，驱动
// 详情审阅 → 构建执行（Dock running）→ 授权（awaiting_authorization）→ 验收（awaiting_acceptance）→ 完成。
// Dock 模式由 lifecycle.activeExecutions[runId] 的 status / pendingInteraction.type 推导（见 planExecutionMode.ts）。

import type { ApplicationLifecycle, WorkflowRunPayload } from '../../typings'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
// 页面/接口契约基座：单一 pms-new 场景（需求回检单模块）。
import { WORKBENCH_PAGES as PAGES } from '../../../../../mock-data/pms-new/workbench-pages'
import { mockPlanningArtifacts } from '../../../../../mock-data/pms-new/planning-artifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { markEndpointDesigned, markPageDesigned } from '../designState'
import { nextLifecycleRevision } from './revision'

const MOCK_APPLICATION_PREVIEW_URL = 'http://127.0.0.1:5190/'

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
  const key = pageId || 'my-rechecks'
  const meta = PAGES[key] || PAGES['my-rechecks']
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
    options.selectedApiContractId ||
      state.selectedApiContractId ||
      result.selectedApiContractId ||
      ''
  ).trim()
  const endpointId = String(
    options.selectedEndpointId || state.selectedEndpointId || result.selectedEndpointId || ''
  ).trim()
  const detailTargetType = String(
    options.detailTargetType || state.detailTargetType || result.detailTargetType || ''
  ).trim()
  // 页面任务会携带依赖接口身份；它仍属于页面工作流，不能误走独立接口剧本。
  if (detailTargetType === 'page') return undefined
  if (detailTargetType === 'endpoint' || (apiContractId && endpointId)) {
    return apiContractId && endpointId ? { apiContractId, endpointId } : undefined
  }
  return undefined
}

// 从 API 契约目录定位 endpoint，返回展示与构建所需元信息。
function endpointMeta(
  apiContractId: string,
  endpointId: string
):
  | {
      apiContractId: string
      endpointId: string
      label: string
      method: string
      path: string
      summary: string
      contractLabel: string
    }
  | undefined {
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
function endpointReviewFor(meta: ReturnType<typeof endpointMeta>): Record<string, unknown> {
  if (!meta) {
    return {
      target_type: 'endpoint',
      target_id: 'endpoint',
      name: '接口',
      method: 'GET',
      path: '/api/unknown',
      data_origin: {
        source_type: 'mysql_existing',
        effective_source: {
          kind: 'mysql_existing',
          database: 'wh_branch',
          tables: ['business_table']
        },
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

function genericEndpointReview(
  meta: NonNullable<ReturnType<typeof endpointMeta>>
): Record<string, unknown> {
  const table = endpointTable(meta)
  const isWrite = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(meta.method)
  const decision =
    meta.method === 'GET'
      ? { behavior: `按查询条件读取 ${table} 数据并分页返回。`, side_effects: [], idempotent: true }
      : {
          behavior: `写入 ${table} 并返回处理结果。`,
          side_effects: ['更新目标数据表'],
          idempotent: false
        }
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
    risks: isWrite ? [{ level: 'medium', description: '写操作涉及数据表变更，需复核影响范围' }] : []
  }
}

// 高频演示接口的专属详情（与页面详情一致，使用真实后端 prompt 风格）。
const RICH_ENDPOINT_REVIEWS: Record<
  string,
  (meta: NonNullable<ReturnType<typeof endpointMeta>>) => Record<string, unknown>
> = {
  'ep-my-rechecks': (meta) => ({
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
      data_purpose: '支撑「我的回检」页的回检单分页列表。',
      data_scope: '读操作',
      read_pattern: '分页 + 状态筛选'
    },
    data_origin: {
      source_type: 'mysql_existing',
      effective_source: { kind: 'mysql_existing', database: 'wh_branch', tables: ['recheck'] },
      field_mappings: [
        { target_field: 'recheck_no', source: 'recheck.recheck_no' },
        { target_field: 'status', source: 'recheck.status' }
      ],
      differences: [],
      notes: ['按当前填报人过滤回检单。']
    },
    endpoint_decision: {
      behavior: '按当前填报人与状态/分页参数返回我的回检单列表。',
      side_effects: [],
      idempotent: true,
      sorting: 'created_at DESC',
      pagination: 'limit/offset'
    },
    interface_design: {
      request_schema: 'GET /api/rechecks/my?status=&page=&size=',
      response_schema: '{ code, data: { list, total }, message }',
      response_fields: ['list', 'total', 'code', 'message'],
      errors: ['400 参数错误', '500 服务异常']
    },
    processing_logic: [
      '解析筛选与分页参数',
      '按当前填报人过滤后分页查询 recheck',
      '统计总数并返回列表'
    ],
    dependent_pages: [{ page_id: 'my-rechecks', usage: 'read' }],
    acceptance_criteria: ['仅返回当前填报人的回检单', '按状态筛选生效', '分页返回且 total 准确'],
    risks: []
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
): WorkbenchExecutionLike {
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

// 工作台三剧本（页面/接口/审查）共享的 base ApplicationLifecycle：ready_for_workbench 初始化基底。
// 集中构造避免 application/initialization 字段在三处重复散落。
function makeBaseLifecycle(
  application: SendWorkflowMessageOptions['application']
): ApplicationLifecycle {
  return {
    schemaVersion: '1.2.0',
    application: {
      id: application?.id || 'app-pms-new',
      name: application?.appName || application?.name || '武汉分行需求回检系统'
    },
    updatedAt: new Date().toISOString(),
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {}
  } as ApplicationLifecycle
}

// 工作台三剧本共享的 emitLifecycle：lifecycleWith 组装 + 通知回调 + 注册权威 lifecycle。
// 三剧本仅 execution 来源（exec/execEndpoint/appExec）不同，合并逻辑一致。
function makeEmitLifecycle(
  baseLifecycle: ApplicationLifecycle,
  runId: string,
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
): (execution: WorkbenchExecutionLike) => ApplicationLifecycle {
  return (execution) => {
    const lifecycle = lifecycleWith(baseLifecycle, runId, execution)
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }
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

// 页面验收交互。
function acceptanceInteraction(): Record<string, unknown> {
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
  const includesRecheckEndpoint = page.id === 'my-rechecks'
  const identity = {
    selectedPageId: page.id,
    ...(includesRecheckEndpoint
      ? {
          selectedApiContractId: 'rechecks',
          selectedEndpointId: 'ep-my-rechecks'
        }
      : {}),
    detailTargetType: 'page'
  }
  return wf(
    threadId,
    runId,
    'detail_confirmation',
    'requires_user_input',
    lifecycle,
    {
      // workflowInteractionAvailability 从 state.lifecycle 读 pendingInteraction 快照，
      // 而 wf() 只把 lifecycle 放 summary.lifecycle —— 不放 state 会导致确认卡判 stale 不渲染。
      lifecycle,
      ...identity,
      clarification: {
        mode: 'detail_review',
        status: 'requires_user_input',
        message: includesRecheckEndpoint
          ? '页面与依赖接口设计已生成，请确认或补充'
          : '页面需求文档已生成，请确认或补充',
        review: {
          pages: [
            pageDesigns[page.id] || {
              target_type: 'page',
              target_id: page.id,
              name: page.label,
              path: page.path,
              page_goal: page.purpose,
              basic_layout: {
                overall: '顶栏 + 左筛选 + 右列表',
                regions: [{ name: '筛选区' }, { name: '列表区' }]
              },
              interactions: ['提交回检弹窗', '状态筛选', '查看回检详情'],
              state_feedback: [
                { state: '加载中', feedbackComponent: 'Skeleton' },
                { state: '空数据', feedbackComponent: 'Empty' }
              ],
              response_bindings: [{ sourcePath: 'data.list', target: 'table.rows' }],
              api_dependencies: [
                { endpointId: 'ep-my-rechecks', method: 'GET', path: '/api/rechecks/my' }
              ],
              acceptance_criteria: ['可提交回检并校验必填', '回检状态可跟踪']
            }
          ],
          endpoints: includesRecheckEndpoint
            ? [endpointReviewFor(endpointMeta('rechecks', 'ep-my-rechecks'))]
            : [],
          summary: {
            page_count: 1,
            endpoint_count: includesRecheckEndpoint ? 1 : 0,
            api_contract_count: 1,
            ...identity
          }
        }
      }
    },
    {
      result: { ...identity, lifecycle }
    }
  )
}

// 构建任务卡（buildExecutionSlice）：通用 3 任务。
function buildExecutionSliceFor(pageId: string, pageLabel: string): Record<string, unknown> {
  const includesRecheckEndpoint = pageId === 'my-rechecks'
  const tasks =
    pageId === 'recheck-introduction'
      ? [
          {
            id: `task-${pageId}-0`,
            task_id: `task-${pageId}-0`,
            unit_id: `page:${pageId}`,
            owner: 'frontend',
            title: `生成 ${pageLabel} 静态页面`,
            status: 'completed',
            target_files: [`frontend/src/pages/${pageId}/index.tsx`]
          },
          {
            id: `task-${pageId}-1`,
            task_id: `task-${pageId}-1`,
            unit_id: `page:${pageId}`,
            owner: 'frontend',
            title: '注册介绍页路由与导航',
            status: 'completed'
          },
          {
            id: `task-${pageId}-2`,
            task_id: `task-${pageId}-2`,
            unit_id: `page:${pageId}`,
            owner: 'test',
            title: '验证无接口状态下页面完整展示',
            status: 'completed'
          }
        ]
      : [
          {
            id: `task-${pageId}-0`,
            task_id: `task-${pageId}-0`,
            unit_id: 'endpoint:rechecks:ep-my-rechecks',
            owner: 'backend',
            title: '实现 GET /api/rechecks/my 查询接口',
            status: 'completed',
            target_files: ['backend/app/routes/rechecks.py']
          },
          {
            id: `task-${pageId}-1`,
            task_id: `task-${pageId}-1`,
            unit_id: `page:${pageId}`,
            owner: 'frontend',
            title: `新增 ${pageLabel} 页面并接入查询接口`,
            status: 'completed',
            target_files: [`frontend/src/pages/${pageId}/index.tsx`]
          },
          {
            id: `task-${pageId}-2`,
            task_id: `task-${pageId}-2`,
            unit_id: `page:${pageId}`,
            owner: 'test',
            title: '联调页面调用与接口响应绑定',
            status: 'completed'
          }
        ]
  return {
    scope: {
      type: 'page',
      id: pageId,
      label: includesRecheckEndpoint ? `${pageLabel}及依赖接口` : pageLabel
    },
    target_unit_ids: [
      `page:${pageId}`,
      ...(includesRecheckEndpoint ? ['endpoint:rechecks:ep-my-rechecks'] : [])
    ],
    tasks,
    summary: { total: tasks.length, completed: tasks.length, pending: 0, running: 0, failed: 0 }
  }
}

// 集成测试检查矩阵（通用 3 项通过）。
function integrationChecks(): unknown[] {
  return [
    {
      id: 'check-contract',
      name: 'API 契约一致性校验',
      status: 'passed',
      required: true,
      evidence: '响应字段与契约定义一致。'
    },
    {
      id: 'check-route',
      name: '页面路由注册校验',
      status: 'passed',
      required: true,
      evidence: '路由已挂载，可正常访问。'
    },
    {
      id: 'check-render',
      name: '页面渲染校验',
      status: 'passed',
      required: false,
      evidence: '页面无运行时报错，关键交互可用。'
    }
  ]
}

// 审查阶段检查矩阵（规范 / 安全 / 健康度 三项通过）。
function reviewChecks(): unknown[] {
  return [
    {
      id: 'check-lint',
      name: '代码规范检测',
      status: 'passed',
      required: true,
      evidence: '命名约定、模块结构与重复代码扫描通过。'
    },
    {
      id: 'check-security',
      name: '安全扫描',
      status: 'passed',
      required: true,
      evidence: '未发现硬编码密钥、越权访问与注入风险。'
    },
    {
      id: 'check-health',
      name: '健康度评估',
      status: 'passed',
      required: true,
      evidence: '圈复杂度正常 / 重复率 0.8% / 单测覆盖 82%。'
    }
  ]
}

// 接口构建的集成测试检查矩阵（契约 + 数据来源 + 联调）。
function endpointIntegrationChecks(): unknown[] {
  return [
    {
      id: 'check-contract',
      name: 'API 契约一致性校验',
      status: 'passed',
      required: true,
      evidence: '接口响应字段与契约定义一致。'
    },
    {
      id: 'check-db',
      name: '数据库结构与数据来源校验',
      status: 'passed',
      required: true,
      evidence: '目标表结构与 data_origin 方案一致。'
    },
    {
      id: 'check-e2e',
      name: '接口集成联调校验',
      status: 'passed',
      required: false,
      evidence: '接口可正常调用，关联页面数据可读。'
    }
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
): WorkbenchExecutionLike {
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
  return endpointWf(
    threadId,
    runId,
    'detail_confirmation',
    'requires_user_input',
    apiContractId,
    endpointId,
    'endpoint',
    lifecycle,
    {
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
    }
  )
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
    {
      id: `task-${endpointId}-0`,
      task_id: `task-${endpointId}-0`,
      unit_id: unitId,
      owner: 'backend',
      title: `新增接口 ${label} 的 Controller/Service`,
      status: 'completed',
      target_files: [`backend/src/main/java/.../controller/`]
    },
    {
      id: `task-${endpointId}-1`,
      task_id: `task-${endpointId}-1`,
      unit_id: unitId,
      owner: 'backend',
      title: `实现 Mapper 与实体映射（${table}）`,
      status: 'completed'
    },
    {
      id: `task-${endpointId}-2`,
      task_id: `task-${endpointId}-2`,
      unit_id: unitId,
      owner: 'database',
      title: `校验并应用数据表结构（${table}）`,
      status: 'completed'
    }
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

  const baseLifecycle = makeBaseLifecycle(options.application)

  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = endpointWf(
      threadId,
      runId,
      phase,
      status,
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      lifecycle,
      state,
      extra
    )
    onWorkflow?.(payload)
    return payload
  }

  // 1. 结束 / 暂停计划。
  if (options.planControlAction === 'end') {
    emitLifecycle(
      execEndpoint(
        runId,
        threadId,
        meta.apiContractId,
        meta.endpointId,
        'finalize_project',
        'completed'
      )
    )
    return endpointWf(
      threadId,
      runId,
      'finalize_project',
      'completed',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      undefined,
      {},
      { summary: { phase: 'finalize_project', status: 'completed', message: '计划已结束' } }
    )
  }
  if (options.planControlAction === 'stop') {
    const lifecycle = emitLifecycle(
      execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'stopped')
    )
    return endpointWf(
      threadId,
      runId,
      'build',
      'stopped',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      lifecycle
    )
  }

  // 2. 验收通过 → 计划完成。
  if (answers.page_acceptance) {
    const lifecycle = emitLifecycle(
      execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'acceptance', 'completed')
    )
    onContent?.(`接口 ${meta.label} 已完成交付，可继续设计其他页面或接口。`)
    return endpointWf(
      threadId,
      runId,
      'acceptance',
      'completed',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      lifecycle
    )
  }

  // 3. 详情审阅确认 → 完整构建链 → 等待验收。
  if (answers.detail_review || resume) {
    const table = endpointTable(meta)
    const steps: Record<string, unknown>[] = []
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.([...steps] as ProcessStepRecord[])
    }

    // inspect_workspace
    onContent?.('已确认设计，正在检查工作区结构…')
    emit(
      'inspect_workspace',
      'completed',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'inspect_workspace',
          'completed'
        )
      )
    )
    pushStep({
      id: 'step-inspect',
      kind: 'workflow',
      status: 'completed',
      title: '检查工作区结构',
      detail: '已扫描后端工程目录、数据库连接配置与既有接口约定。',
      sequence: 1
    })
    await delay(600)

    // inspect_database_context（接口有数据来源，比页面多这一节点）
    onContent?.('正在获取数据库上下文…')
    emit(
      'inspect_database_context',
      'running',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'inspect_database_context',
          'running'
        )
      )
    )
    await delay(700)
    emit(
      'inspect_database_context',
      'completed',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'inspect_database_context',
          'completed'
        )
      )
    )
    pushStep({
      id: 'step-db',
      kind: 'workflow',
      status: 'completed',
      title: '获取数据库信息',
      detail: `已读取目标表 ${table} 结构与数据源配置，确认接口数据来源。`,
      sequence: 2
    })
    await delay(300)

    // prepare_build_tasks
    onContent?.('正在规划构建任务（DAG）…')
    emit(
      'prepare_build_tasks',
      'running',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'prepare_build_tasks',
          'running'
        )
      )
    )
    await delay(800)
    emit(
      'prepare_build_tasks',
      'completed',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'prepare_build_tasks',
          'completed'
        )
      )
    )
    pushStep({
      id: 'step-prepare',
      kind: 'workflow',
      status: 'completed',
      title: '规划构建任务（DAG）',
      detail: `已为接口 ${meta.label} 拆解构建任务并编译执行 DAG。`,
      sequence: 3
    })
    await delay(300)

    // build
    onContent?.('正在生成接口代码…')
    emit(
      'build',
      'running',
      emitLifecycle(
        execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'running')
      )
    )
    await delay(1200)
    emit(
      'build',
      'completed',
      emitLifecycle(
        execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'completed')
      )
    )
    pushStep({
      id: 'step-build',
      kind: 'workflow',
      status: 'completed',
      title: '生成接口代码',
      detail: '',
      sequence: 4,
      buildExecutionSlice: endpointBuildExecutionSlice(
        meta.apiContractId,
        meta.endpointId,
        meta.label,
        table
      )
    })
    await delay(300)

    // integration_test
    onContent?.('正在执行集成测试…')
    emit(
      'integration_test',
      'running',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'integration_test',
          'running'
        )
      )
    )
    await delay(900)
    emit(
      'integration_test',
      'completed',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'integration_test',
          'completed'
        )
      )
    )
    pushStep({
      id: 'step-test',
      kind: 'workflow',
      status: 'completed',
      title: '执行集成测试',
      detail: '',
      sequence: 5,
      checks: endpointIntegrationChecks()
    })
    await delay(300)

    // integration_test 通过 → 接口模块开发完成。完成标记在构建链走完后置位，
    // 确保用户看清节点流程后，再由工作台询问是否进入审查。
    markEndpointDesigned(meta.apiContractId, meta.endpointId)
    onContent?.(
      `「${meta.label}」开发完成（集成测试通过）。\n全部开发产物完成后，可确认进入审查阶段。`
    )
    return endpointWf(
      threadId,
      runId,
      'integration_test',
      'completed',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'integration_test',
          'completed'
        )
      ),
      {},
      { summary: { phase: 'integration_test', status: 'completed', message: '接口开发完成' } }
    )
  }

  // 4. 开始接口详细设计 → 接口详情审阅。
  if (options.selectedEndpointId || options.detailTargetType) {
    onContent?.(`正在为接口 ${meta.label} 生成详细设计…`)
    await delay(700)
    const pending = {
      id: `pi-endpoint-design-${Date.now()}`,
      type: 'page_design_confirmation',
      basedOnRevision: 1,
      payload: { message: '接口设计已生成，等待确认' },
      createdAt: new Date().toISOString()
    }
    const lifecycle = emitLifecycle(
      execEndpoint(
        runId,
        threadId,
        meta.apiContractId,
        meta.endpointId,
        'detail_confirmation',
        'awaiting_user',
        pending
      )
    )
    const payload = endpointReviewPayload(
      threadId,
      runId,
      meta.apiContractId,
      meta.endpointId,
      lifecycle
    )
    onContent?.(`已为接口 ${meta.label} 生成详细设计，请确认后开始构建。`)
    onWorkflow?.(payload)
    return payload
  }

  // 5. 其它 → 最小 running 态。
  const fallback = emitLifecycle(
    execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'running')
  )
  return endpointWf(
    threadId,
    runId,
    'build',
    'running',
    meta.apiContractId,
    meta.endpointId,
    'endpoint',
    fallback
  )
}

/** 所有页面/API完成后进入应用概览，启动完整预览并等待用户进行应用级验收。 */
export async function replayApplicationAcceptance(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-application-acceptance-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const appName =
    options.application?.appName || options.application?.name || '武汉分行需求回检系统'

  /** 生成应用级验收执行状态，供阶段门禁识别。 */
  const appExecution = (
    status: string,
    pending?: Record<string, unknown>
  ): WorkbenchExecutionLike => {
    const now = new Date().toISOString()
    return {
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'acceptance',
      status,
      startedAt: now,
      updatedAt: now,
      ...(pending ? { pendingInteraction: pending } : {})
    }
  }

  /** 发布应用级 lifecycle，并注册为浏览器 mock 的最新事实。 */
  const emitLifecycle = (
    status: string,
    pending?: Record<string, unknown>
  ): ApplicationLifecycle => {
    const lifecycle = {
      schemaVersion: '1.2.0',
      application: { id: appId, name: appName },
      updatedAt: new Date().toISOString(),
      revision: nextLifecycleRevision(),
      initialization: { stage: 'ready_for_workbench', status: 'completed' },
      activeExecutions: { [runId]: appExecution(status, pending) }
    } as ApplicationLifecycle
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }

  /** 同步应用验收 Workflow 投影到对话消息。 */
  const emit = (
    status: string,
    lifecycle: ApplicationLifecycle,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, 'acceptance', status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  if (answers.application_acceptance) {
    onContent?.('应用整体验收已通过，即将进入审查阶段。')
    const completed = emitLifecycle('completed')
    return emit(
      'completed',
      completed,
      {},
      {
        summary: { phase: 'acceptance', status: 'completed', message: '应用验收通过' }
      }
    )
  }

  const pending = {
    id: `pi-application-acceptance-${Date.now()}`,
    type: 'application_acceptance',
    basedOnRevision: 1,
    payload: { message: '请在右侧完整应用预览中完成整体验收。' },
    createdAt: new Date().toISOString()
  }
  const lifecycle = emitLifecycle('awaiting_user', pending)
  onContent?.('所有页面与接口均已完成开发和集成检查。请在右侧预览完整应用，确认后进入审查阶段。')
  return emit(
    'requires_user_input',
    lifecycle,
    {
      clarification: {
        mode: 'application_acceptance',
        status: 'requires_user_input',
        message: '请完成应用整体验收。',
        questions: [
          {
            id: 'application_acceptance',
            header: '应用验收',
            question: '完整应用的页面、接口与关键业务流程是否符合预期？',
            type: 'yesno',
            presetAnswer: { selected: ['是'] }
          }
        ]
      }
    },
    {
      result: { preview_url: MOCK_APPLICATION_PREVIEW_URL },
      summary: { phase: 'acceptance', status: 'requires_user_input', message: '等待应用验收' }
    }
  )
}

// 应用级审查阶段剧本(复用应用概览会话):
// 应用验收通过 → 审查 Agent 做非功能检查(代码审查/规范检测/健康度),审查通过 → 可发布。
export async function replayCodeReview(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-review-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const baseLifecycle = makeBaseLifecycle(options.application)

  const appExec = (
    phase: string,
    status: string,
    pending?: Record<string, unknown>
  ): WorkbenchExecutionLike => {
    const now = new Date().toISOString()
    return {
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase,
      status,
      startedAt: now,
      updatedAt: now,
      ...(pending ? { pendingInteraction: pending } : {})
    }
  }
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, phase, status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  // 1. 审查确认通过 → finalize_project completed(可发布)。
  if (answers.code_review) {
    onContent?.('审查确认通过,应用已就绪,现在可以生成版本了。')
    await delay(300)
    const finalized = emitLifecycle(appExec('finalize_project', 'completed'))
    return emit(
      'finalize_project',
      'completed',
      finalized,
      {},
      {
        summary: { phase: 'finalize_project', status: 'completed', message: '审查通过,可生成版本' }
      }
    )
  }

  // 2. 启动审查 → 走子图(规范检测 → 安全扫描 → 健康度),完成后发确认卡。
  //    子节点 emit 复用 code_review running 的 lifecycle,不调 emitLifecycle 更新 applicationLifecycle,
  //    避免 lint/security/health 进入 activeExecutions 后全部 completed(terminal)导致
  //    deriveWorkbenchPhase 跌回 development(=审查中途跳回开发)。workflow payload 的 phase 仍逐节点切换(节点卡动态)。
  if (answers.review_start) {
    onContent?.('启动应用级代码审查,开始非功能检查(规范检测 / 安全扫描 / 健康度评估)…')
    const reviewRunning = emitLifecycle(appExec('code_review', 'running'))
    emit('code_review', 'running', reviewRunning)
    const steps: Record<string, unknown>[] = []
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.([...steps] as ProcessStepRecord[])
    }

    // 规范检测
    emit('lint_check', 'running', reviewRunning)
    onContent?.('正在执行代码规范检测…')
    await delay(1000)
    emit('lint_check', 'completed', reviewRunning)
    pushStep({
      id: 'step-lint',
      kind: 'workflow',
      status: 'completed',
      title: '代码规范检测',
      detail: '命名约定、模块结构与重复代码扫描通过。',
      sequence: 1
    })

    // 安全扫描
    emit('security_scan', 'running', reviewRunning)
    onContent?.('正在执行安全扫描…')
    await delay(1000)
    emit('security_scan', 'completed', reviewRunning)
    pushStep({
      id: 'step-security',
      kind: 'workflow',
      status: 'completed',
      title: '安全扫描',
      detail: '未发现硬编码密钥、越权访问与注入风险。',
      sequence: 2
    })

    // 健康度评估
    emit('health_check', 'running', reviewRunning)
    onContent?.('正在评估代码健康度…')
    await delay(1000)
    emit('health_check', 'completed', reviewRunning)
    pushStep({
      id: 'step-health',
      kind: 'workflow',
      status: 'completed',
      title: '健康度评估',
      detail: '圈复杂度正常 / 重复率 0.8% / 单测覆盖 82%。',
      sequence: 3,
      checks: reviewChecks()
    })

    onContent?.('代码审查完成:规范检测、安全扫描与健康度评估全部通过,等待确认后即可生成版本。')
    const reviewPending = {
      id: `pi-code-review-${Date.now()}`,
      type: 'code_review',
      basedOnRevision: 1,
      payload: { message: '代码审查完成,等待确认。' },
      createdAt: new Date().toISOString()
    }
    const reviewLifecycle = emitLifecycle(appExec('code_review', 'awaiting_user', reviewPending))
    return emit(
      'code_review',
      'requires_user_input',
      reviewLifecycle,
      {
        clarification: {
          mode: 'code_review',
          status: 'requires_user_input',
          message: '请确认审查结果。',
          questions: [
            {
              id: 'code_review',
              header: '审查确认',
              question: '代码审查、规范检测、安全扫描与健康度评估全部通过，确认后即可生成版本。',
              type: 'yesno',
              presetAnswer: { selected: ['是'] }
            }
          ]
        }
      },
      {
        summary: { phase: 'code_review', status: 'requires_user_input', message: '请确认审查结果' }
      }
    )
  }

  // 3. 首次进入:询问是否启动代码审查。
  const startPending = {
    id: `pi-review-start-${Date.now()}`,
    type: 'review_start',
    basedOnRevision: 1,
    payload: { message: '是否启动代码审查?' },
    createdAt: new Date().toISOString()
  }
  const startLifecycle = emitLifecycle(appExec('code_review', 'awaiting_user', startPending))
  onContent?.(
    '所有页面与接口模块已开发完成,是否启动应用级代码审查(规范检测 / 安全扫描 / 健康度评估)?'
  )
  return emit(
    'code_review',
    'requires_user_input',
    startLifecycle,
    {
      clarification: {
        mode: 'review_start',
        status: 'requires_user_input',
        message: '是否启动代码审查?',
        questions: [
          {
            id: 'review_start',
            header: '启动审查',
            question: '所有模块开发完成,是否启动应用级代码审查?',
            type: 'yesno',
            presetAnswer: { selected: ['是'] }
          }
        ]
      }
    },
    {
      summary: { phase: 'code_review', status: 'requires_user_input', message: '是否启动审查' }
    }
  )
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
  const includesRecheckEndpoint = page.id === 'my-rechecks'
  const pageTaskIdentity = {
    selectedPageId: page.id,
    ...(includesRecheckEndpoint
      ? {
          selectedApiContractId: 'rechecks',
          selectedEndpointId: 'ep-my-rechecks'
        }
      : {}),
    detailTargetType: 'page'
  }
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  // 按当前应用工作区取页面详设数据（pms-new 单页详设）。
  const pageDesigns = appDataByWorkspace(options.application?.workspaceRoot).pageDesigns as Record<
    string,
    Record<string, unknown>
  >

  const baseLifecycle = makeBaseLifecycle(options.application)

  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(
      threadId,
      runId,
      phase,
      status,
      lifecycle,
      { ...pageTaskIdentity, ...state },
      extra
    )
    onWorkflow?.(payload)
    return payload
  }
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)

  // 1. 结束 / 暂停计划。
  if (options.planControlAction === 'end') {
    emitLifecycle(exec(runId, threadId, page.id, 'finalize_project', 'completed'))
    return emit(
      'finalize_project',
      'completed',
      undefined,
      {},
      { summary: { phase: 'finalize_project', status: 'completed', message: '计划已结束' } }
    )
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
    const awaiting = emitLifecycle(
      exec(runId, threadId, page.id, 'build', 'awaiting_user', acceptanceInteraction())
    )
    // 验收态 workflow 需带 clarification.mode='page_acceptance'，
    // 否则 pageAcceptanceContinuationMessage 返回空、验收提交被拦截。
    return emit(
      'acceptance',
      'requires_user_input',
      awaiting,
      {
        lifecycle: awaiting,
        clarification: {
          mode: 'page_acceptance',
          status: 'requires_user_input',
          message: '请预览页面并完成最终验收。',
          questions: []
        }
      },
      {
        summary: {
          phase: 'acceptance',
          status: 'requires_user_input',
          message: '页面已准备好，等待最终验收'
        }
      }
    )
  }

  // 4. 详情审阅确认 → 完整构建链 → 等待验收。
  // 按真实节点链（workflow.py）：inspect_workspace → prepare_build_tasks → build
  // → integration_test → launch_project(page_acceptance + preview_url)。
  // agent_approval 仅数据库高危操作才触发，普通页面构建默认不走。
  if (answers.detail_review || resume) {
    const steps: Record<string, unknown>[] = []
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.([...steps] as ProcessStepRecord[])
    }

    // inspect_workspace
    onContent?.('已确认设计，正在检查工作区结构…')
    emit(
      'inspect_workspace',
      'completed',
      emitLifecycle(exec(runId, threadId, page.id, 'inspect_workspace', 'completed'))
    )
    pushStep({
      id: 'step-inspect',
      kind: 'workflow',
      status: 'completed',
      title: '检查工作区结构',
      detail: '已扫描前端工程目录结构、入口文件与既有约定。',
      sequence: 1
    })
    await delay(600)

    if (includesRecheckEndpoint) {
      onContent?.('正在读取依赖接口的数据源与契约上下文…')
      emit(
        'inspect_database_context',
        'running',
        emitLifecycle(exec(runId, threadId, page.id, 'inspect_database_context', 'running'))
      )
      await delay(700)
      emit(
        'inspect_database_context',
        'completed',
        emitLifecycle(exec(runId, threadId, page.id, 'inspect_database_context', 'completed'))
      )
      pushStep({
        id: 'step-db',
        kind: 'workflow',
        status: 'completed',
        title: '读取依赖接口上下文',
        detail: '已确认 GET /api/rechecks/my 的契约、recheck 数据表与页面响应绑定。',
        sequence: steps.length + 1
      })
      await delay(300)
    }

    // prepare_build_tasks
    onContent?.('正在规划构建任务（DAG）…')
    emit(
      'prepare_build_tasks',
      'running',
      emitLifecycle(exec(runId, threadId, page.id, 'prepare_build_tasks', 'running'))
    )
    await delay(800)
    emit(
      'prepare_build_tasks',
      'completed',
      emitLifecycle(exec(runId, threadId, page.id, 'prepare_build_tasks', 'completed'))
    )
    pushStep({
      id: 'step-prepare',
      kind: 'workflow',
      status: 'completed',
      title: '规划构建任务（DAG）',
      detail: includesRecheckEndpoint
        ? `已把「${page.label}」页面与 GET /api/rechecks/my 编排为同一个双产物任务。`
        : `已为「${page.label}」拆解构建任务并编译执行 DAG。`,
      sequence: steps.length + 1
    })
    await delay(300)

    // build
    onContent?.(page.id === 'my-rechecks' ? '正在实现页面及其依赖的查询接口…' : '正在生成页面代码…')
    emit('build', 'running', emitLifecycle(exec(runId, threadId, page.id, 'build', 'running')))
    await delay(1200)
    emit('build', 'completed', emitLifecycle(exec(runId, threadId, page.id, 'build', 'completed')))
    pushStep({
      id: 'step-build',
      kind: 'workflow',
      status: 'completed',
      title: page.id === 'my-rechecks' ? '实现页面与依赖接口' : '生成页面代码',
      detail: includesRecheckEndpoint ? '先实现查询接口，再生成页面并完成调用与响应字段绑定。' : '',
      sequence: steps.length + 1,
      buildExecutionSlice: buildExecutionSliceFor(page.id, page.label)
    })
    await delay(300)

    // integration_test
    onContent?.('正在执行集成测试…')
    emit(
      'integration_test',
      'running',
      emitLifecycle(exec(runId, threadId, page.id, 'integration_test', 'running'))
    )
    await delay(900)
    emit(
      'integration_test',
      'completed',
      emitLifecycle(exec(runId, threadId, page.id, 'integration_test', 'completed'))
    )
    pushStep({
      id: 'step-test',
      kind: 'workflow',
      status: 'completed',
      title: '执行集成测试',
      detail: includesRecheckEndpoint ? '验证页面可通过依赖接口加载当前用户的回检数据。' : '',
      sequence: steps.length + 1,
      checks: integrationChecks()
    })
    await delay(300)

    // integration_test 通过 → 页面模块开发完成。完成标记在构建链走完后置位，
    // 确保用户看清节点流程后，再由工作台询问是否进入审查。
    markPageDesigned(page.id)
    if (page.id === 'my-rechecks') {
      // “我的回检”以页面为任务入口，但同一工作流同时交付其查询接口，不创建额外接口对话。
      markEndpointDesigned('rechecks', 'ep-my-rechecks')
      onContent?.(
        '“我的回检”页面、GET /api/rechecks/my 查询接口与联调验证均已完成。\n全部开发产物已完成，请确认是否进入审查阶段。'
      )
    } else {
      onContent?.(
        `「${page.label}」开发完成（集成测试通过）。\n全部开发产物完成后，可确认进入审查阶段。`
      )
    }
    return emit(
      'integration_test',
      'completed',
      emitLifecycle(exec(runId, threadId, page.id, 'integration_test', 'completed')),
      {},
      { summary: { phase: 'integration_test', status: 'completed', message: '页面开发完成' } }
    )
  }

  // 5. 开始页面设计（DetailConfirmationPageSelector 点"开始生成"）→ 详情审阅。
  if (options.selectedPageId || options.detailTargetType || options.originalRequest) {
    onContent?.(
      includesRecheckEndpoint
        ? `正在为「${page.label}」设计页面与 GET /api/rechecks/my 依赖接口…`
        : `正在为「${page.label}」生成页面需求文档…`
    )
    // 注：不在开始设计时 markPageDesigned——「已设计」仅在用户确认详细设计(detail_review)后标记。
    // 详情审阅需要 lifecycle 快照中的 pendingInteraction（page_design_confirmation）才能提交确认。
    // 生成中以 processSteps 承载 5 阶段（与构建链一致：Agent 正在执行→已归档 N 个步骤），完成后留历史。
    const designSteps: Array<{ id: string; title: string; detail: string }> =
      includesRecheckEndpoint
        ? [
            {
              id: 'design-context',
              title: '汇总双产物上下文',
              detail: '整合页面目标、接口契约、数据来源与项目约束。'
            },
            {
              id: 'design-page',
              title: '设计我的回检页面',
              detail: '梳理筛选、列表、提交入口与状态反馈。'
            },
            {
              id: 'design-endpoint',
              title: '设计依赖查询接口',
              detail: '定义 GET /api/rechecks/my 的查询参数、响应结构与数据来源。'
            },
            {
              id: 'design-binding',
              title: '校验页面调用关系',
              detail: '把接口 data.list 与 total 绑定到页面列表及分页状态。'
            },
            {
              id: 'design-summary',
              title: '汇总双产物设计',
              detail: '形成页面与接口可共同确认、共同实施的设计方案。'
            }
          ]
        : [
            {
              id: 'design-context',
              title: '汇总应用上下文',
              detail: '整合需求文档、项目计划与页面目标。'
            },
            {
              id: 'design-scope',
              title: '梳理页面范围',
              detail: '明确页面职责、核心功能与关键用户路径。'
            },
            {
              id: 'design-breakdown',
              title: '拆解功能与数据',
              detail: '整理功能点、数据展示与交互依赖。'
            },
            {
              id: 'design-edge',
              title: '补齐边界与验收',
              detail: '定义异常态、边界约束与验收标准。'
            },
            { id: 'design-summary', title: '汇总需求文档', detail: '整理为可确认的页面需求文档。' }
          ]
    const steps: ProcessStepRecord[] = []
    const pushStep = (step: ProcessStepRecord): void => {
      steps.push(step)
      onProcessSteps?.([...steps])
    }
    onWorkflow?.(
      wf(threadId, runId, 'detail_confirmation', 'running', undefined, {
        summary: {
          phase: 'detail_confirmation',
          status: 'running',
          message: includesRecheckEndpoint ? '正在生成页面与依赖接口设计…' : '正在生成页面需求文档…'
        }
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
    onWorkflow?.(
      wf(threadId, runId, 'detail_confirmation', 'completed', undefined, {
        summary: {
          phase: 'detail_confirmation',
          status: 'completed',
          message: includesRecheckEndpoint ? '页面与依赖接口设计已生成' : '页面需求文档已生成'
        }
      })
    )
    await delay(300)
    const pending = {
      id: `pi-design-${Date.now()}`,
      type: 'page_design_confirmation',
      basedOnRevision: 1,
      payload: {
        message: includesRecheckEndpoint
          ? '页面与依赖接口设计已生成，等待统一确认'
          : '页面需求文档已生成，等待确认'
      },
      createdAt: new Date().toISOString()
    }
    const lifecycle = emitLifecycle(
      exec(runId, threadId, page.id, 'detail_confirmation', 'awaiting_user', pending)
    )
    const payload = detailReviewPayload(threadId, runId, page.id, lifecycle, pageDesigns)
    onContent?.(
      includesRecheckEndpoint
        ? `已为「${page.label}」生成页面与依赖接口设计，请确认后在同一任务中开始实施。`
        : `已为「${page.label}」生成页面需求文档，请确认或补充后开始构建。`
    )
    onWorkflow?.(payload)
    return payload
  }

  // 6. 其它（自由聊天/未知）→ 最小 running 态，不崩。
  const fallback = emitLifecycle(exec(runId, threadId, page.id, 'build', 'running'))
  return emit('build', 'running', fallback)
}
