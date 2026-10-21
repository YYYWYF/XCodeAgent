import type { ApplicationConfig } from '../../typings'
import {
  applyConfigChanges,
  type ConfigChanges,
  type ConfigOperation
} from '../../service/applicationConfigOperation'

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
    } else if (file === 'backend/src/main/resources/application.yml') {
      messages.push('已更新 application.yml；请重启后端服务以加载新配置。')
    } else {
      messages.push(`已更新工程文件：${file}。`)
    }
  }
  return messages
}

/** 在配置失败时说明哪些工程变更未能确认完成，避免将部分写入误报为成功。 */
export function configOperationFailureMessage(error: unknown, changes: ConfigChanges): string {
  const reason = error instanceof Error ? error.message : '应用配置提交失败，请重试。'
  const pending: string[] = []
  if (Object.keys(changes).some((path) => path === 'auth.enable' || path.startsWith('auth.yht.'))) {
    pending.push('登录相关工程配置')
  }
  if ('authorization.enabled' in changes) pending.push('权限能力代码')
  return pending.length
    ? `应用配置提交失败：${reason}。以下工程变更未能确认完成：${pending.join('、')}。`
    : `应用配置提交失败：${reason}`
}

/** 汇总设置保存后的工程同步提醒，确保界面一次只显示一个反馈弹框。 */
export function settingsSaveNoticeMessages(operation: ConfigOperation | undefined): string[] {
  return ['应用配置已保存。', ...configOperationEffectMessages(operation)]
}

/** 从设置表单提取应用配置白名单字段，避免提交整份旧 application.json。 */
export function settingsConfigChanges(current: ApplicationConfig, next: ApplicationConfig): ConfigChanges {
  const changes: ConfigChanges = {}
  // 登录关闭时，表单不会注册一号通字段；此时既不应读取缺失的 yht，也不应把它们作为配置提案提交。
  const loginEnabled = current.auth.enable || next.auth.enable
  const scalar: Array<[string, string | boolean, string | boolean]> = [
    ['appName', current.appName, next.appName],
    ['appIcon', current.appIcon, next.appIcon],
    ['theme.primaryColor', current.theme.primaryColor, next.theme.primaryColor],
    ['auth.enable', current.auth.enable, next.auth.enable],
    ['authorization.enabled', current.authorization.enabled, next.authorization.enabled]
  ]
  // 启用登录的当前或目标状态需要完整比较凭据；关闭时保留磁盘上的已有值即可。
  if (loginEnabled) {
    const currentYht = current.auth.yht ?? { clientId: '', privateKey: '', publicKey: '', centerPublicKey: '' }
    const nextYht = next.auth.yht ?? { clientId: '', privateKey: '', publicKey: '', centerPublicKey: '' }
    scalar.push(
      ['auth.yht.clientId', currentYht.clientId, nextYht.clientId],
      ['auth.yht.privateKey', currentYht.privateKey, nextYht.privateKey],
      ['auth.yht.publicKey', currentYht.publicKey, nextYht.publicKey],
      ['auth.yht.centerPublicKey', currentYht.centerPublicKey, nextYht.centerPublicKey]
    )
  }
  for (const [path, before, after] of scalar) {
    if (before !== after) changes[path] = after
  }
  const beforeSubjects = current.authorization.initialAdministratorSubjects
  const afterSubjects = next.authorization.initialAdministratorSubjects
  if (JSON.stringify(beforeSubjects) !== JSON.stringify(afterSubjects)) {
    changes['authorization.initialAdministratorSubjects'] = afterSubjects
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
