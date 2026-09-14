/** Electron 退出时沿统一 AG-UI 协议清理生成项目服务。 */
export async function stopProjectPreviewViaAgUi(baseUrl: string, workspace: string): Promise<void> {
  const { HttpAgent, randomUUID } = await import('@ag-ui/client')
  const agent = new HttpAgent({
    url: `${baseUrl.replace(/\/$/, '')}/preview-runtime/run`,
    threadId: randomUUID()
  })
  const result = await agent.runAgent({
    forwardedProps: { previewRuntime: { workspace, action: 'stop' } }
  })
  const payload = (
    result.result as
      | {
          previewRuntime?: {
            status?: string
            error?: { message?: string }
            launchResult?: { status?: string; message?: string }
          }
        }
      | undefined
  )?.previewRuntime
  if (payload?.status !== 'completed' || payload.launchResult?.status !== 'stopped') {
    throw new Error(
      payload?.error?.message || payload?.launchResult?.message || '停止预览服务失败。'
    )
  }
}
