/** 将发布清单缺失与鉴权/下载失败区分，避免把 SDK 的通用 token 提示误当作根因。 */
export function appUpdateErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : '更新失败'
  const manifest = message.match(/latest(?:-mac)?\.yml/)?.[0]
  if (manifest && /\b404\b/.test(message)) {
    return `更新服务缺少发布清单 ${manifest}（HTTP 404）。请检查发布目录和服务端返回的下载地址`
  }
  return message
}
