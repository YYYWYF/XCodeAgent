const DEFAULT_PREVIEW_URL = 'https://example.com'

export type PreviewNavigationState = {
  history: string[]
  index: number
}

/** 规范化用户或 Workflow 提供的预览地址。 */
export function normalizePreviewUrl(value: string): string {
  const trimmedValue = value.trim()
  if (!trimmedValue) return ''

  if (/^[a-z][a-z\d+.-]*:\/\//i.test(trimmedValue)) {
    return trimmedValue
  }

  if (/^(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?(\/.*)?$/i.test(trimmedValue)) {
    return `http://${trimmedValue}`
  }

  return `https://${trimmedValue}`
}

/** 读取指定应用最近一次成功使用的预览地址。 */
export function getStoredPreviewUrl(applicationId: string): string {
  try {
    return window.localStorage.getItem(`xcode-agent-preview-url:${applicationId}`) || ''
  } catch {
    return ''
  }
}

/** 持久化指定应用当前使用的预览地址。 */
export function storePreviewUrl(applicationId: string, url: string): void {
  try {
    window.localStorage.setItem(`xcode-agent-preview-url:${applicationId}`, url)
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }
}

/** 返回应用预览初始地址，缺少历史记录时使用安全默认页。 */
export function getInitialPreviewUrl(applicationId: string): string {
  return normalizePreviewUrl(getStoredPreviewUrl(applicationId)) || DEFAULT_PREVIEW_URL
}

/** 将新地址追加到预览历史，并在回退后导航时丢弃旧的前进记录。 */
export function navigatePreviewHistory(
  state: PreviewNavigationState,
  rawUrl: string
): PreviewNavigationState {
  const nextUrl = normalizePreviewUrl(rawUrl)
  const currentUrl = state.history[state.index] || ''
  if (!nextUrl || nextUrl === currentUrl) return state

  return {
    history: [...state.history.slice(0, state.index + 1), nextUrl],
    index: state.index + 1
  }
}

/** 使用系统浏览器打开规范化后的预览地址。 */
export async function openExternalPreviewUrl(url: string): Promise<void> {
  const targetUrl = normalizePreviewUrl(url)
  if (!targetUrl) return

  if (window.xcodeAgent?.browser?.openExternal) {
    await window.xcodeAgent.browser.openExternal(targetUrl)
    return
  }

  window.open(targetUrl, '_blank', 'noopener,noreferrer')
}

/** 使用 Electron 独立窗口或浏览器弹窗打开预览地址。 */
export async function openPreviewWindow(url: string): Promise<void> {
  const targetUrl = normalizePreviewUrl(url)
  if (!targetUrl) return

  if (window.xcodeAgent?.browser?.openPreviewWindow) {
    await window.xcodeAgent.browser.openPreviewWindow(targetUrl)
    return
  }

  const openedWindow = window.open(
    targetUrl,
    `xcode-agent-preview-${Date.now()}`,
    'popup,width=1280,height=860,left=80,top=60,noopener,noreferrer'
  )

  if (!openedWindow) {
    throw new Error('浏览器阻止了新预览窗口')
  }
}
