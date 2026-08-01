export const ELEMENT_INSPECTOR_CHANNEL = 'xcode-agent:element-inspector' as const
export const ELEMENT_INSPECTOR_VERSION = 1 as const

export type ElementSourceLocation = {
  sourcePath: string
  line: number
  column: number
}

export type ElementInspectorReadyMessage = {
  channel: typeof ELEMENT_INSPECTOR_CHANNEL
  version: typeof ELEMENT_INSPECTOR_VERSION
  type: 'ready'
}

export type ElementInspectorSelectionMessage = {
  channel: typeof ELEMENT_INSPECTOR_CHANNEL
  version: typeof ELEMENT_INSPECTOR_VERSION
  type: 'element-selected'
  tagName: string
  sourceLocation: ElementSourceLocation | null
}

export type ElementInspectorInboundMessage =
  | ElementInspectorReadyMessage
  | ElementInspectorSelectionMessage

export type ElementInspectorCommand = {
  channel: typeof ELEMENT_INSPECTOR_CHANNEL
  version: typeof ELEMENT_INSPECTOR_VERSION
  type: 'set-active'
  active: boolean
}

/** 判断未知值是否为普通记录，作为跨域消息校验的第一层边界。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 校验 iframe 返回的源码位置，拒绝目录穿越、绝对磁盘路径和异常行列。 */
function parseSourceLocation(value: unknown): ElementSourceLocation | null | undefined {
  if (value === null) return null
  if (!isRecord(value)) return undefined
  const sourcePath = value.sourcePath
  const line = value.line
  const column = value.column
  if (
    typeof sourcePath !== 'string' ||
    sourcePath.length > 1024 ||
    !sourcePath.startsWith('/src/') ||
    sourcePath.split('/').includes('..') ||
    !Number.isSafeInteger(line) ||
    !Number.isSafeInteger(column) ||
    Number(line) <= 0 ||
    Number(column) <= 0
  ) {
    return undefined
  }
  return { sourcePath, line: Number(line), column: Number(column) }
}

/** 解析 iframe 发来的版本化审查消息，非法数据统一返回 null。 */
export function parseElementInspectorMessage(
  value: unknown
): ElementInspectorInboundMessage | null {
  if (!isRecord(value)) return null
  if (value.channel !== ELEMENT_INSPECTOR_CHANNEL || value.version !== ELEMENT_INSPECTOR_VERSION) {
    return null
  }
  if (value.type === 'ready') {
    return {
      channel: ELEMENT_INSPECTOR_CHANNEL,
      version: ELEMENT_INSPECTOR_VERSION,
      type: 'ready'
    }
  }
  if (value.type !== 'element-selected' || typeof value.tagName !== 'string') return null
  const tagName = value.tagName.trim().toLowerCase()
  if (!/^[a-z][a-z0-9-]{0,63}$/.test(tagName)) return null
  const sourceLocation = parseSourceLocation(value.sourceLocation)
  if (sourceLocation === undefined) return null
  return {
    channel: ELEMENT_INSPECTOR_CHANNEL,
    version: ELEMENT_INSPECTOR_VERSION,
    type: 'element-selected',
    tagName,
    sourceLocation
  }
}

/** 创建发送给 iframe 的审查启停命令。 */
export function createElementInspectorCommand(active: boolean): ElementInspectorCommand {
  return {
    channel: ELEMENT_INSPECTOR_CHANNEL,
    version: ELEMENT_INSPECTOR_VERSION,
    type: 'set-active',
    active
  }
}

/** 从当前预览地址提取可用于 postMessage 校验和发送的标准 origin。 */
export function elementInspectorPreviewOrigin(previewUrl: string): string {
  try {
    const url = new URL(previewUrl)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.origin : ''
  } catch {
    return ''
  }
}

/** 判断消息 origin 是否与当前 iframe 预览地址严格一致。 */
export function isExpectedElementInspectorOrigin(origin: string, previewUrl: string): boolean {
  const expectedOrigin = elementInspectorPreviewOrigin(previewUrl)
  return Boolean(expectedOrigin) && origin === expectedOrigin
}
