import { runPreviewRuntime } from './previewRuntime'

/**
 * 模板项目预览启动服务。
 *
 * 在模板下载完成、进入工作区后，自动调用后端 API 异步启动
 * 后端（如有）和前端开发服务器，不阻塞用户的页面设计与 API 设计。
 */

export type ProjectLaunchStatus = 'running' | 'stopped' | 'failed'

export type ProjectLaunchResult = {
  status: ProjectLaunchStatus
  message: string
  preview_url?: string
  backend?: Record<string, unknown>
  frontend?: Record<string, unknown>
  failed_stage?: string
}

/** 通过独立 AG-UI 流启动生成项目预览。 */
export async function startProjectLaunch(workspace: string): Promise<ProjectLaunchResult> {
  const result = await runPreviewRuntime({ workspace, action: 'start' })
  if (!result.launchResult) throw new Error('启动结果缺失')
  return result.launchResult
}

/** 通过独立 AG-UI 流停止生成项目预览。 */
export async function stopProjectPreview(workspace: string): Promise<ProjectLaunchResult> {
  const result = await runPreviewRuntime({ workspace, action: 'stop' })
  if (!result.launchResult) throw new Error('停止结果缺失')
  return result.launchResult
}
