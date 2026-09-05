import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { bootstrapApplicationTemplateGeneration } from './applicationLifecycle'

/** 控制应用模板初始化入口是否启用。 */
export const APPLICATION_TEMPLATE_GENERATION_ENABLED = true

const readinessTasks = new Map<string, Promise<ApplicationLifecycle>>()

/** 将工作区转换成当前桌面平台可稳定去重的任务键。 */
function templateReadinessKey(workspaceRoot: string): string {
  const value = workspaceRoot.trim().replace(/[\\/]+$/, '')
  return window.xcodeAgent?.platform === 'win32' ? value.toLowerCase() : value
}

/** 通过单次 AG-UI 动作触发 Server-owned Bootstrap，前端不再下载或克隆模板。 */
async function runApplicationTemplateReadiness(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  const lifecycle = await bootstrapApplicationTemplateGeneration(application, threadId)
  if (lifecycle.initialization.stage !== 'ready_for_workbench') {
    throw new Error(lifecycle.error?.message || '应用模板初始化未通过完成门禁。')
  }
  return lifecycle
}

/** 以工作区为粒度合并同一次 TechnicalPlan 确认触发的并发 Bootstrap 请求。 */
export function ensureApplicationTemplateReadiness(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  const workspaceRoot = application.workspaceRoot || application.projectParentPath || ''
  if (!workspaceRoot.trim()) return Promise.reject(new Error('应用缺少 workspaceRoot。'))
  const key = templateReadinessKey(workspaceRoot)
  const current = readinessTasks.get(key)
  if (current) return current
  const task = runApplicationTemplateReadiness(application, threadId)
  readinessTasks.set(key, task)
  void task.finally(() => {
    if (readinessTasks.get(key) === task) readinessTasks.delete(key)
  }).catch(() => undefined)
  return task
}
