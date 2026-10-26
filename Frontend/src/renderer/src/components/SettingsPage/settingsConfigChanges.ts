import type { ApplicationConfig } from '../../typings'
import {
  applyConfigChanges,
  type ConfigChanges,
  type ConfigOperation
} from '../../service/applicationConfigOperation'
import { normalizeSettingsConfiguration } from './settingsDraft'

/** 声明设置页提交所依赖的统一配置事务，便于保持 UI 与测试的调用边界一致。 */
export type ApplySettingsConfigChanges = (
  workspaceRoot: string,
  expectedConfigRevision: number,
  changes: ConfigChanges
) => Promise<ConfigOperation>

/** 把后端返回的已安装能力转换为用户可核对的工程代码变更说明。 */
function capabilityEffectMessage(capabilities: string[] | undefined): string | undefined {
  const labels = [...new Set(capabilities || [])]
    .map((capability) => ({ login: '登录', authorization: '权限' }[capability]))
    .filter((label): label is string => Boolean(label))
  return labels.length ? `已向工程注入${labels.join('、')}能力相关代码。` : undefined
}

/** 汇总已实际写入工程的配置副作用，未知文件也必须向用户展示其路径。 */
export function configOperationEffectMessages(operation: ConfigOperation | undefined): string[] {
  const messages = [capabilityEffectMessage(operation?.installedCapabilities)].filter(
    (message): message is string => Boolean(message)
  )
  for (const file of [...new Set(operation?.changedFiles || [])]) {
    if (file === 'frontend/.env') {
      messages.push('已更新 frontend/.env；请重新构建或重启前端服务以加载新配置。')
    } else if (file === 'backend/src/main/resources/application-dev.yml') {
      messages.push('已更新 application-dev.yml；请重启后端服务以加载新配置。')
    } else {
      messages.push(`已更新工程文件：${file}。`)
    }
  }
  return messages
}

/** 在配置失败时说明哪些工程变更未能确认完成，避免将部分写入误报为成功。 */
export function configOperationFailureMessage(error: unknown, changes: ConfigChanges): string {
  void changes
  const detail = error as { failureStage?: string; failureCode?: string; failureTarget?: string } | undefined
  if (detail?.failureStage) {
    const labels: Record<string, string> = {
      desired_save: '应用配置保存失败', template_state_read: '工程模板状态读取失败',
      template_capability_reconcile: '模板能力安装失败', application_parameter_update: '工程参数写入失败',
      config_snapshot_read: '应用配置读取失败'
    }
    return `${labels[detail.failureStage] || '应用配置操作失败'}（${detail.failureCode || 'CONFIG_APPLY_FAILED'}${detail.failureTarget ? `；${detail.failureTarget}` : ''}）。请使用原操作重试。`
  }
  return error instanceof Error ? error.message : '应用配置提交失败，请重试。'

}

/** 汇总设置保存后的工程同步提醒，确保界面一次只显示一个反馈弹框。 */
export function settingsSaveNoticeMessages(operation: ConfigOperation | undefined): string[] {
  return ['应用配置已保存。', ...configOperationEffectMessages(operation)]
}

/** 从设置表单提取应用配置白名单字段，避免提交整份旧 application.json。 */
export function settingsConfigChanges(current: ApplicationConfig, next: ApplicationConfig): ConfigChanges {
  // 草稿展示会为旧快照补默认地址；比较两端也必须使用同一语义基线，避免“未编辑”被提交为字段更新。
  const normalizedCurrent = normalizeSettingsConfiguration(current)
  const normalizedNext = normalizeSettingsConfiguration(next)
  const changes: ConfigChanges = {}
  // 登录关闭时，表单不会注册一号通字段；此时既不应读取缺失的 yht，也不应把它们作为配置提案提交。
  const loginEnabled = normalizedCurrent.auth.enable || normalizedNext.auth.enable
  const scalar: Array<[string, string | boolean, string | boolean]> = [
    ['appName', normalizedCurrent.appName, normalizedNext.appName],
    ['appIcon', normalizedCurrent.appIcon, normalizedNext.appIcon],
    ['theme.primaryColor', normalizedCurrent.theme.primaryColor, normalizedNext.theme.primaryColor],
    ['auth.enable', normalizedCurrent.auth.enable, normalizedNext.auth.enable],
    ['authorization.enabled', normalizedCurrent.authorization.enabled, normalizedNext.authorization.enabled]
  ]
  // 启用登录的当前或目标状态需要完整比较凭据；关闭时保留磁盘上的已有值即可。
  if (loginEnabled) {
    // 旧应用未启用登录时不会保存 yht；首次启用时以当前默认契约作为比较基线。
    const currentYht = normalizedCurrent.auth.yht
    const nextYht = normalizedNext.auth.yht
    scalar.push(
      ['auth.yht.clientId', currentYht.clientId, nextYht.clientId],
      ['auth.yht.privateKey', currentYht.privateKey, nextYht.privateKey],
      ['auth.yht.publicKey', currentYht.publicKey, nextYht.publicKey],
      ['auth.yht.centerPublicKey', currentYht.centerPublicKey, nextYht.centerPublicKey],
      ['auth.yht.centerUri', currentYht.centerUri, nextYht.centerUri],
      ['auth.yht.authorizeUri', currentYht.authorizeUri, nextYht.authorizeUri],
      ['auth.yht.redirectUri', currentYht.redirectUri, nextYht.redirectUri],
      ['auth.yht.endUrl', currentYht.endUrl, nextYht.endUrl],
      ['auth.yht.logoutShareJs', currentYht.logoutShareJs, nextYht.logoutShareJs]
    )
  }
  for (const [path, before, after] of scalar) {
    if (before !== after) changes[path] = after
  }
  const beforeSubjects = normalizedCurrent.authorization.initialAdministratorSubjects
  const afterSubjects = normalizedNext.authorization.initialAdministratorSubjects
  if (JSON.stringify(beforeSubjects) !== JSON.stringify(afterSubjects)) {
    changes['authorization.initialAdministratorSubjects'] = afterSubjects
  }
  // 首次启用权限时，磁盘旧快照可以尚无 api；即使界面补出的默认值与比较基线相同，
  // 也必须随开关提交，后端才能构造满足当前合同的 authorization.api。
  const enablingAuthorization = !current.authorization.enabled && normalizedNext.authorization.enabled
  // 权限关闭时不提交 API 值，避免把隐藏表单字段误写入 Desired。
  if (normalizedCurrent.authorization.enabled || normalizedNext.authorization.enabled) {
    const beforeApi = normalizedCurrent.authorization.api
    const afterApi = normalizedNext.authorization.api
    if (enablingAuthorization || beforeApi?.baseUrl !== afterApi?.baseUrl) {
      changes['authorization.api.baseUrl'] = afterApi?.baseUrl || ''
    }
    if (enablingAuthorization || beforeApi?.viewId !== afterApi?.viewId) {
      changes['authorization.api.viewId'] = afterApi?.viewId || ''
    }
  }
  return changes
}

/** 仅在受管配置确有变化时调用 AG-UI 配置事务，避免打开或保存无关设置产生重复操作。 */
export async function applySettingsConfigChanges(
  current: ApplicationConfig,
  next: ApplicationConfig,
  apply: ApplySettingsConfigChanges = applyConfigChanges
): Promise<ConfigOperation | undefined> {
  const changes = settingsConfigChanges(current, next)
  if (Object.keys(changes).length === 0) return undefined
  return apply(current.workspaceRoot, current.configRevision, changes)
}
