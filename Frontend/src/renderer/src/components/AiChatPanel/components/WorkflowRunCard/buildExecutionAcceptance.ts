/** 将工程验收检查投射为用户可读的结构化说明。 */
export function structuredCheckDescriptions(value: unknown): string[] {
  return objectList(value).flatMap((item) => {
    const description = stringValue(item.description).trim()
    if (!description) return []
    const kind = stringValue(item.kind).trim()
    const prefix = kind ? `${kind}：` : ''
    return [`${prefix}${description}`]
  })
}

type BusinessAcceptanceDetail = {
  key: string
  title: string
  status: string
  evidence: string
}

/** 提取失败或阻断的验收证据，工程错误优先使用精确 error 字段。 */
export function acceptanceFailureDetails(
  value: unknown,
  kind: 'engineering' | 'business'
): string[] {
  return objectList(value).flatMap((item) => {
    const status = stringValue(item.status).trim()
    if (!['failed', 'blocked'].includes(status)) return []
    const detail = stringValue(
      kind === 'engineering' ? item.error || item.evidence : item.evidence || item.error
    ).trim()
    if (!detail) return []
    const checkId = stringValue(item.check_id ?? item.id).trim()
    return [checkId ? `[${checkId}] ${detail}` : detail]
  })
}

/** 合并业务检查计划和执行证据，避免运行态任务仍被显示为待检查。 */
export function businessAcceptanceDetails(
  value: unknown,
  evidenceValue: unknown
): BusinessAcceptanceDetail[] {
  const evidenceItems = objectList(evidenceValue)
  const evidenceById = new Map(
    evidenceItems.flatMap((item) => {
      const checkId = stringValue(item.check_id ?? item.id).trim()
      return checkId ? [[checkId, item] as const] : []
    })
  )
  const matchedIds = new Set<string>()
  const details = objectList(value).flatMap((item, index) => {
    const checkId = stringValue(item.id ?? item.check_id).trim()
    const evidenceItem = evidenceById.get(checkId) || {}
    if (checkId) matchedIds.add(checkId)
    const description = stringValue(item.description).trim()
    const kind = stringValue(item.kind).trim()
    const evidence = stringValue(evidenceItem.evidence || item.evidence).trim()
    const status = stringValue(evidenceItem.status || item.status).trim() || 'pending'
    const title = description || checkId || kind
    if (!title && !evidence) return []
    return [
      {
        key: checkId || `${kind || 'business-check'}-${index}`,
        title: kind && description ? `${kind}：${description}` : title || '业务检查',
        status,
        evidence
      }
    ]
  })
  const unmatchedEvidence = evidenceItems.flatMap((item, index) => {
    const checkId = stringValue(item.check_id ?? item.id).trim()
    if (checkId && matchedIds.has(checkId)) return []
    const kind = stringValue(item.kind).trim()
    const evidence = stringValue(item.evidence).trim()
    if (!checkId && !kind && !evidence) return []
    return [
      {
        key: checkId || `${kind || 'business-evidence'}-${index}`,
        title: kind ? `${kind}：${checkId || '业务检查'}` : checkId || '业务检查',
        status: stringValue(item.status).trim() || 'pending',
        evidence
      }
    ]
  })
  return [...details, ...unmatchedEvidence]
}

/** 将业务验收状态转换为中文，避免任务卡片直接显示协议枚举。 */
export function businessAcceptanceStatusText(status: string): string {
  return (
    {
      passed: '通过',
      failed: '失败',
      blocked: '阻断',
      skipped: '已跳过',
      pending: '待检查'
    }[status] || status
  )
}

/** 为业务验收状态选择与当前主题兼容的 Ant Design 标签色。 */
export function businessAcceptanceStatusColor(status: string): string {
  if (status === 'passed') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'blocked') return 'orange'
  return 'default'
}

/** 读取检查和证据中的对象数组，不将空值作为验收项。 */
function objectList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === 'object')
      )
    : []
}

/** 只将真实字符串用于检查文案，避免显示对象序列化噪声。 */
function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}
