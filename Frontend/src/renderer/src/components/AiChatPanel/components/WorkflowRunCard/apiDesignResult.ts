import type {
  WorkflowApiDesignGateDesign,
  WorkflowApiDesignGateResult,
  WorkflowRunPayload
} from '../../../../typings'

/** 从工作流公开快照中读取已确认的 API 设计结果。 */
export function readApiDesignResult(
  workflow: WorkflowRunPayload
): WorkflowApiDesignGateResult | undefined {
  const candidates: unknown[] = [
    workflow.summary.apiDesignResult,
    workflow.summary.api_design_result,
    workflow.state?.apiDesignResult,
    workflow.state?.api_design_result,
    workflow.result?.apiDesignResult,
    workflow.result?.api_design_result,
    (workflow.summary.clarification as { apiDesignResult?: unknown } | undefined)?.apiDesignResult,
    (workflow.summary.clarification as { api_design_result?: unknown } | undefined)?.api_design_result,
    (workflow.state?.clarification as { apiDesignResult?: unknown } | undefined)?.apiDesignResult,
    (workflow.result?.clarification as { apiDesignResult?: unknown } | undefined)?.apiDesignResult
  ]
  // 节点完成事件把结果放在 data.stateDelta，不能只读取顶层 data；确认后立刻回填依赖这一帧。
  const events = Array.isArray(workflow.events) ? workflow.events : []
  events
    .slice()
    .reverse()
    .flatMap((item) => {
      const data = item.data
      const stateDelta =
        data?.stateDelta && typeof data.stateDelta === 'object'
          ? (data.stateDelta as Record<string, unknown>)
          : undefined
      const detail =
        data?.detail && typeof data.detail === 'object'
          ? (data.detail as Record<string, unknown>)
          : undefined
      return [
        data?.apiDesignResult,
        data?.api_design_result,
        (data?.clarification as { apiDesignResult?: unknown } | undefined)?.apiDesignResult,
        (data?.clarification as { api_design_result?: unknown } | undefined)?.api_design_result,
        stateDelta?.apiDesignResult,
        stateDelta?.api_design_result,
        detail?.apiDesignResult,
        detail?.api_design_result
      ]
    })
    .filter((candidate) => candidate && typeof candidate === 'object')
    .forEach((candidate) => candidates.push(candidate))
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object') continue
    const value = candidate as Record<string, unknown>
    if (value.status !== 'ready' && value.status !== 'confirmed') continue
    const targetType = value.targetType === 'page' ? 'page' : value.targetType === 'endpoint' ? 'endpoint' : undefined
    const targetId = String(value.targetId || '')
    const targetLabel = String(value.targetLabel || '')
    if (!targetType || !targetId || !targetLabel || !Array.isArray(value.designs)) continue
    const designs = value.designs
      .map(normalizeGateDesign)
      .filter((item): item is WorkflowApiDesignGateDesign => Boolean(item))
    if (designs.length !== value.designs.length) continue
    if (targetType === 'endpoint' && designs.length !== 1) continue
    const confirmedForDevelopment = value.confirmedForDevelopment === true
    if (value.status === 'confirmed' && !confirmedForDevelopment) continue
    if (value.status === 'ready' && confirmedForDevelopment) continue
    return {
      status: value.status,
      targetType,
      targetId,
      targetLabel,
      designs,
      confirmedForDevelopment
    }
  }
  return undefined
}

/** 校验并规范化门禁结果中的单项 Endpoint 完整映射。 */
function normalizeGateDesign(value: unknown): WorkflowApiDesignGateDesign | undefined {
  if (!value || typeof value !== 'object') return undefined
  const item = value as Record<string, unknown>
  const design = item.design
  if (!design || typeof design !== 'object' || Array.isArray(design)) return undefined
  const apiContractId = String(item.apiContractId || '')
  const endpointId = String(item.endpointId || '')
  const artifactRevision = String(item.artifactRevision || '')
  const designValue = design as Record<string, unknown>
  if (
    !apiContractId ||
    !endpointId ||
    !/^[0-9a-f]{32}$/.test(artifactRevision) ||
    designValue.status !== 'confirmed' ||
    String(designValue.apiContractId || '') !== apiContractId ||
    String(designValue.endpointId || '') !== endpointId ||
    String(designValue.artifactRevision || '') !== artifactRevision
  ) {
    return undefined
  }
  return { apiContractId, endpointId, artifactRevision, design: designValue }
}
