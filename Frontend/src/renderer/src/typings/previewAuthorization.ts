export type PreviewAuthorizationAdvisory = {
  applicable: true
  status: 'ready' | 'pending' | 'running' | 'needs_attention' | 'unknown'
  schemaReady: boolean
  seedReady: boolean
  severity: 'warning'
  title: string
  message: string
  nextAction: 'open_authorization_status'
}

/** 校验预览提醒的公开字段，只保留明确允许展示的内容。 */
export function parsePreviewAuthorizationAdvisory(value: unknown): PreviewAuthorizationAdvisory | null {
  if (!value || typeof value !== 'object') return null
  const { applicable, status, schemaReady, seedReady, severity, title, message, nextAction } =
    value as Partial<PreviewAuthorizationAdvisory>
  if (
    applicable !== true ||
    (status !== 'ready' && status !== 'pending' && status !== 'running' &&
      status !== 'needs_attention' && status !== 'unknown') ||
    typeof schemaReady !== 'boolean' ||
    seedReady !== (status === 'ready') ||
    (seedReady && !schemaReady) ||
    severity !== 'warning' ||
    typeof title !== 'string' || !title.trim() || title.length > 64 ||
    typeof message !== 'string' || message.length > 256 || (!seedReady && !message.trim()) ||
    nextAction !== 'open_authorization_status'
  ) return null
  return { applicable, status, schemaReady, seedReady, severity, title, message, nextAction }
}
