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
