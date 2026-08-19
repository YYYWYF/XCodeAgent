/** 使用 Electron 主进程安全打开本地 Lighthouse HTML 报告。 */
export async function openLocalReportFile(reportPath: string): Promise<void> {
  const trimmedPath = reportPath.trim()
  if (!trimmedPath) {
    throw new Error('报告路径为空')
  }
  if (window.xcodeAgent?.browser?.openReportFile) {
    const result = await window.xcodeAgent.browser.openReportFile(trimmedPath)
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
  if (!openedWindow && !window.xcodeAgent?.isElectron) {
    throw new Error('当前应用未加载报告打开能力，请重启应用后重试')
  }
}
