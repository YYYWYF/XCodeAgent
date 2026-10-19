/** 只把测试报告中的受控相对 HTML 链接解析为当前工作区的本地报告路径。 */
export function localLighthouseReportPath(
  workspaceRoot: string | undefined,
  href: string
): string | undefined {
  if (!workspaceRoot) return undefined
  let relative: string
  try {
    relative = decodeURIComponent(href)
  } catch {
    return undefined
  }
  if (
    !relative.startsWith('./.devagentstudio/runtime/tests/frontend_performance/') ||
    !relative.endsWith('.html') ||
    /[\\:?#]/.test(relative) ||
    Array.from(relative).some(
      (character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127
    ) ||
    relative
      .slice(2)
      .split('/')
      .some((segment) => !segment || segment === '.' || segment === '..')
  )
    return undefined
  return `${workspaceRoot.replace(/[\\/]+$/, '')}/${relative.slice(2)}`
}

/** 使用 Electron 主进程安全打开本地 Lighthouse HTML 报告。 */
export async function openLocalReportFile(reportPath: string): Promise<void> {
  const trimmedPath = reportPath.trim()
  if (!trimmedPath) {
    throw new Error('报告路径为空')
  }
  if (window.devAgentStudio?.browser?.openReportFile) {
    const result = await window.devAgentStudio.browser.openReportFile(trimmedPath)
    if (result?.ok !== true) {
      throw new Error('打开本地报告失败')
    }
    return
  }

  // 兼容尚未加载 openReportFile 的旧 preload：Electron 主窗口的
  // setWindowOpenHandler 会把 file:// 交给系统默认浏览器打开。
  const normalizedPath = trimmedPath.replace(/\\/g, '/')
  const fileUrl = new URL(
    normalizedPath.startsWith('/') ? `file://${normalizedPath}` : `file:///${normalizedPath}`
  ).href
  const openedWindow = window.open(fileUrl, '_blank', 'noopener,noreferrer')
  if (!openedWindow && !window.devAgentStudio?.isElectron) {
    throw new Error('当前应用未加载报告打开能力，请重启应用后重试')
  }
}
