import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationSchemaConfig,
  TemplateDownloadResult
} from '../typings'
import {
  completeApplicationTemplateGeneration,
  prepareApplicationTemplateGeneration
} from './applicationLifecycle'

/** 默认前端模板仓库地址。 */
export const DEFAULT_FRONTEND_TEMPLATE_REPO_URL = 'https://github.com/ruyue1/frontend-template.git'

/** 默认后端模板仓库地址。 */
export const DEFAULT_BACKEND_TEMPLATE_REPO_URL = 'https://github.com/Hupy2118/springboot-template.git'

/** 控制应用模板下载与初始化流程是否启用。 */
export const APPLICATION_TEMPLATE_GENERATION_ENABLED = true

const readinessTasks = new Map<string, Promise<ApplicationLifecycle>>()

/** 携带模板下载结构化结果，供后端 manifest 记录失败现场。 */
class TemplateDownloadError extends Error {
  readonly result: TemplateDownloadResult

  /** 创建包含下载明细的模板下载错误。 */
  constructor(message: string, result: TemplateDownloadResult) {
    super(message)
    this.name = 'TemplateDownloadError'
    this.result = result
  }
}

/** 将工作区转换成当前桌面平台可稳定去重的任务键。 */
function templateReadinessKey(workspaceRoot: string): string {
  const value = workspaceRoot.trim().replace(/[\\/]+$/, '')
  return window.xcodeAgent?.platform === 'win32' ? value.toLowerCase() : value
}

/** 汇总前后端下载错误，避免第三次失败后被静默吞掉。 */
function templateDownloadErrorMessage(result: TemplateDownloadResult): string {
  const messages = result.failedTargets.map((target) => {
    const detail = result.targets[target]
    return `${target}（尝试 ${detail.attempt} 次）：${detail.error || '模板下载失败'}`
  })
  return messages.join('；') || '模板下载失败。'
}

/** 通过 Electron 主进程拉取或复用前后端模板，并返回每个仓库的尝试结果。 */
export async function fetchTemplateCode(
  schema: ApplicationSchemaConfig,
  projectPath: string
): Promise<TemplateDownloadResult> {
  const appName = schema.appName.trim()
  if (!appName) throw new Error('应用名称不能为空，无法拉取模板工程。')
  if (!projectPath.trim()) throw new Error('项目位置不能为空，无法拉取模板工程。')

  const cloneTemplate = window.xcodeAgent?.workspace?.cloneTemplate
  if (!cloneTemplate) throw new Error('当前环境不支持模板工程下载。')
  const result = await cloneTemplate({
    projectPath,
    appName,
    frontendTemplateUrl: DEFAULT_FRONTEND_TEMPLATE_REPO_URL,
    backendTemplateUrl: DEFAULT_BACKEND_TEMPLATE_REPO_URL
  })
  if (!result.ok) throw new TemplateDownloadError(templateDownloadErrorMessage(result), result)
  return result
}

/** 执行一次模板 readiness：下载、页面/菜单增量对账，再通过后端完成门禁。 */
async function runApplicationTemplateReadiness(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  const workspaceRoot = application.workspaceRoot || application.projectParentPath || ''
  let failureMessage = ''

  try {
    const downloadResult = await fetchTemplateCode(application.schema, workspaceRoot)
    await prepareApplicationTemplateGeneration(application, threadId, downloadResult)
  } catch (reason) {
    failureMessage = reason instanceof Error ? reason.message : String(reason)
    if (reason instanceof TemplateDownloadError) {
      try {
        await prepareApplicationTemplateGeneration(application, threadId, reason.result)
      } catch (prepareReason) {
        const prepareMessage =
          prepareReason instanceof Error ? prepareReason.message : String(prepareReason)
        failureMessage = `${failureMessage}；${prepareMessage}`
      }
    }
  }

  const lifecycle = await completeApplicationTemplateGeneration(
    application,
    threadId,
    !failureMessage,
    failureMessage || undefined
  )
  if (lifecycle.initialization.stage !== 'ready_for_workbench') {
    throw new Error(lifecycle.error?.message || failureMessage || '应用模板初始化未通过完成门禁。')
  }
  return lifecycle
}

/** 以工作区为粒度合并同一次 TechnicalPlan 确认触发的并发 readiness。 */
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
  void task
    .finally(() => {
      if (readinessTasks.get(key) === task) readinessTasks.delete(key)
    })
    .catch(() => undefined)
  return task
}
