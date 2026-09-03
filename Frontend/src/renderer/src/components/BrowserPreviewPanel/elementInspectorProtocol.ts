import type { InspectedElementContext } from '../../typings'

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

/** 从已校验的审查消息提取可发送的完整源码上下文，无源码位置时不产生选择。 */
export function inspectedElementContextFromMessage(
  message: ElementInspectorInboundMessage
): InspectedElementContext | null {
  if (message.type !== 'element-selected' || !message.sourceLocation) return null
  return {
    tagName: message.tagName,
    sourcePath: message.sourceLocation.sourcePath,
    line: message.sourceLocation.line,
    column: message.sourceLocation.column
  }
}

/** 将有效 DOM 源码定位格式化为用户可读的即时提示。 */
export function inspectedElementLocationMessage(context: InspectedElementContext): string {
  return `定位到该元素位于${context.sourcePath}文件${context.line}行处`
}

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
