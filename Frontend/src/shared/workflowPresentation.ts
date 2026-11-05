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

/** 写入会话时只保存一份展示状态，派生的卡片摘要不能再复制同一批资料。 */
export function persistentWorkflowView(workflow: Record<string, unknown>): Record<string, unknown> {
  const { summary: rawSummary, state: rawState, result: _result, ...rest } = workflow
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
