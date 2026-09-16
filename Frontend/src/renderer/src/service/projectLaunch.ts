import { runPreviewRuntime } from './previewRuntime'

/** 模板项目预览停止服务；启动统一由服务状态中的手动重启操作负责。 */

export type ProjectLaunchStatus = 'running' | 'stopped' | 'failed'

export type ProjectLaunchResult = {
  status: ProjectLaunchStatus
  message: string
  preview_url?: string
  backend?: Record<string, unknown>
  frontend?: Record<string, unknown>
  failed_stage?: string
}

/** 通过独立 AG-UI 流停止生成项目预览。 */
export async function stopProjectPreview(workspace: string): Promise<ProjectLaunchResult> {
  const result = await runPreviewRuntime({ workspace, action: 'stop' })
  if (!result.launchResult) throw new Error('停止结果缺失')
  return result.launchResult
}
