type RecordValue = Record<string, unknown>
const STATE_SCALARS = new Set([
  'phase',
  'status',
  'selectedPageId',
  'selected_api_contract_id',
  'selected_endpoint_id',
  'selected_entity_id',
  'detail_target_type',
  'requirement_spec_path',
  'product_plan_path',
  'technical_plan_path',
  'project_plan_path',
  'test_report_path',
  'workspace_revision',
  'requirementsConfirmed'
])
const DOCUMENT_DETAILS = new Set(['buildTaskPlan', 'productPlan', 'projectPlan', 'technicalPlan'])
const TRACE_DETAILS = new Set(['buildEvents', 'buildResults', 'testEvents'])
const RUN_SUMMARY_FIELDS = new Set([
  'status',
  'phase',
  'message',
  'errorCode',
  'completedNodeCount',
  'failedEventCount',
  'qualityGatePassed',
  'needsRevision',
  'unitTestGatePassed'
])

/** 终态事件只保留摘要及诊断，不能借 summary 再复制完整计划或确认状态。 */
function runSummary(summary: RecordValue): RecordValue {
  return {
    ...Object.fromEntries(
      Object.entries(summary).filter(
        ([key, item]) =>
          RUN_SUMMARY_FIELDS.has(key) &&
          (item === null || ['string', 'number', 'boolean'].includes(typeof item))
      )
    ),
    ...(record(summary.failureDiagnostic) ? { failureDiagnostic: summary.failureDiagnostic } : {}),
    ...(record(summary.artifacts) ? { artifacts: summary.artifacts } : {})
  }
}

/** 只接受普通 JSON 对象，未知值不能成为事件上下文。 */
function record(value: unknown): RecordValue | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as RecordValue)
    : undefined
}

/** 按当前事件协议选出有界展示字段，不遍历任务或正式产物正文。 */
function stateContext(value: RecordValue): RecordValue {
  const context = Object.fromEntries(
    Object.entries(value)
      .filter(
        ([key, item]) =>
          STATE_SCALARS.has(key) &&
          (item === null || ['string', 'number', 'boolean'].includes(typeof item)) &&
          !(key.endsWith('_path') && String(item).toLowerCase().endsWith('.json'))
      )
      .map(([key, item]) => [key, typeof item === 'string' ? item.slice(0, 4096) : item])
  )
  if (Array.isArray(value.timeline)) {
    context.timeline = value.timeline
      .slice(-256)
      .filter((item): item is string => typeof item === 'string')
      .map((item) => item.slice(0, 160))
  }
  if (record(value.workspaceInspection)) context.workspaceInspection = value.workspaceInspection
  return context
}

/** Build 过程详情不附带每个任务重复内嵌的执行源材料。 */
function executionSlice(value: unknown): unknown {
  const slice = record(value)
  if (!slice || !Array.isArray(slice.tasks)) return value
  return {
    ...slice,
    tasks: slice.tasks
      .filter((task) => record(task))
      .map((task) =>
        Object.fromEntries(
          Object.entries(task as RecordValue).filter(
            ([key]) => !['source_refs', 'acceptance_checks'].includes(key)
          )
        )
      )
  }
}

/** 保留节点诊断和展示详情，文档及原始执行 trace 在事件里只保留摘要或数量。 */
function eventDetail(value: RecordValue): RecordValue {
  const detail = Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !DOCUMENT_DETAILS.has(key) && !TRACE_DETAILS.has(key))
      .map(([key, item]) => [key, key === 'buildExecutionSlice' ? executionSlice(item) : item])
  )
  for (const key of DOCUMENT_DETAILS) {
    const document = record(value[key])
    if (!document) continue
    const summary = Object.fromEntries(
      ['status', 'schema_version', 'version']
        .filter((field) => ['string', 'number', 'boolean'].includes(typeof document[field]))
        .map((field) => [field, document[field]])
    )
    const tasks = record(document.task_graph)?.tasks
    if (Array.isArray(tasks)) summary.taskCount = tasks.length
    detail[`${key}Summary`] = summary
  }
  for (const key of TRACE_DETAILS) {
    if (Array.isArray(value[key])) detail[`${key}Count`] = value[key].length
  }
  return detail
}

/** 实时接收和会话落盘共用事件约束；保留事件顺序，不压缩或删除失败轮次。 */
export function compactWorkflowEvents(value: unknown): RecordValue[] {
  if (!Array.isArray(value)) return []
  const previousByRun = new Map<unknown, Map<string, string>>()
  return value
    .filter((item) => record(item))
    .map((item) => {
      const event = item as RecordValue
      const previous = previousByRun.get(event.runId) || new Map<string, string>()
      previousByRun.set(event.runId, previous)
      const source = record(event.data) || {}
      const data = Object.fromEntries(
        Object.entries(source).filter(([key]) => key !== 'stateDelta')
      )
      if (record(source.summary)) data.summary = runSummary(source.summary as RecordValue)
      if (record(source.detail)) data.detail = eventDetail(source.detail as RecordValue)
      if (record(source.stateDelta)) {
        const delta: RecordValue = {}
        for (const [key, item] of Object.entries(stateContext(source.stateDelta as RecordValue))) {
          const encoded = JSON.stringify(item)
          // 只冻结已经过展示白名单的值，避免后续表单或运行态修改倒写历史事件。
          if (previous.get(key) !== encoded) delta[key] = JSON.parse(encoded)
          previous.set(key, encoded)
        }
        if (Object.keys(delta).length) data.stateDelta = delta
      }
      return { ...event, data }
    })
}
