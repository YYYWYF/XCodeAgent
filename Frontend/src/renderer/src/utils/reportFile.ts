/** 使用 Electron 主进程安全打开本地 Lighthouse HTML 报告。 */
export async function openLocalReportFile(reportPath: string): Promise<void> {
  const trimmedPath = reportPath.trim()
  if (!trimmedPath) {
    throw new Error('报告路径为空')
  }
  if (!window.xcodeAgent?.browser?.openReportFile) {
    throw new Error('当前环境不支持打开本地报告')
  }
  const result = await window.xcodeAgent.browser.openReportFile(trimmedPath)
  if (result?.ok !== true) {
    throw new Error('打开本地报告失败')
  }
}
