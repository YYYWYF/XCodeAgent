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

/** 返回后端 API 的基础地址（仅 origin，不含路径）。 */
function getBackendOrigin(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl ? agentBaseUrl.replace(/\/$/, '') : ''
}

/**
 * 触发模板项目预览启动。
 *
 * 该请求会持续到后端完成安装/构建并启动服务器，前端无需 await，
 * 通过返回的 Promise 异步处理结果即可，不阻塞用户操作。
 */
export async function startProjectLaunch(workspace: string): Promise<ProjectLaunchResult> {
  const baseUrl = getBackendOrigin()
  const response = await fetch(`${baseUrl}/api/projects/launch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace }),
  })
  if (!response.ok) {
    const errorText = await response.text().catch(() => '未知错误')
    return {
      status: 'failed',
      message: `启动请求失败（${response.status}）：${errorText}`,
    }
  }
  return response.json() as Promise<ProjectLaunchResult>
}

/** 停止指定工作区已启动的前后端预览服务。 */
export async function stopProjectPreview(workspace: string): Promise<ProjectLaunchResult> {
  const baseUrl = getBackendOrigin()
  const response = await fetch(`${baseUrl}/api/projects/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace }),
  })
  if (!response.ok) {
    const errorText = await response.text().catch(() => '未知错误')
    return {
      status: 'failed',
      message: `停止请求失败（${response.status}）：${errorText}`,
    }
  }
  return response.json() as Promise<ProjectLaunchResult>
}
