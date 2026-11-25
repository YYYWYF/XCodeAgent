const SUMMARY_FIELDS = new Set([
  'status',
  'phase',
  'message',
  'request',
  'errorCode',
  'failureDiagnostic',
  'completedNodeCount',
  'failedEventCount',
  'qualityGatePassed',
  'needsRevision',
  'previewUrl',
  'unitTestGatePassed',
  'unitTestNextAction',
  'unitTestRepairIteration',
  'unitTestMaxRepairIterations',
  'integrationNextAction',
  'repairReturnNode',
  'repairIteration',
  'maxRepairIterations',
  'requiresUserInput',
  'intent'
])

/** 展示层把状态派生为原有卡片字段；对象只共享引用，不生成第二份执行状态。 */
export function displayWorkflowSummary(
  state: Record<string, unknown> | undefined,
  summary: Record<string, unknown>
): Record<string, unknown> {
  return { ...state, ...summary }
}

/** 会话展示只保存一份状态，排除仅供本次查看的内存过程记录。 */
export function persistentWorkflowView(workflow: Record<string, unknown>): Record<string, unknown> {
  const {
    summary: rawSummary, state: rawState, result: _result,
    technicalPlanningOutput: _technicalPlanningOutput, ...rest
  } = workflow
  const summary =
    rawSummary && typeof rawSummary === 'object' ? (rawSummary as Record<string, unknown>) : {}
  const state =
    rawState && typeof rawState === 'object' ? (rawState as Record<string, unknown>) : {}
  return {
    ...rest,
    summary: Object.fromEntries(Object.entries(summary).filter(([key]) => SUMMARY_FIELDS.has(key))),
    state: {
      ...Object.fromEntries(Object.entries(summary).filter(([key]) => !SUMMARY_FIELDS.has(key))),
      ...state
    }
  }
}
