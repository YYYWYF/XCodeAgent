import { DatasourceEnum, type ApplicationConfig, type ApplicationDraft } from '../../typings'
import { LOGIN_AUTHORIZATION_DEFAULTS } from '../Welcome/constants'

/** 将旧快照补齐为当前设置页比较与展示共用的配置形状，显式空值保持不变。 */
export function normalizeSettingsConfiguration(application: ApplicationConfig): ApplicationConfig {
  const yht = application.auth.yht || ({} as ApplicationDraft['auth']['yht'])
  const defaults = LOGIN_AUTHORIZATION_DEFAULTS
  return {
    ...application,
    auth: {
      ...application.auth,
      yht: {
        ...yht,
        centerUri: yht.centerUri ?? defaults.centerUri,
        authorizeUri: yht.authorizeUri ?? defaults.authorizeUri,
        redirectUri: yht.redirectUri ?? defaults.redirectUri,
        endUrl: yht.endUrl ?? defaults.endUrl,
        logoutShareJs: yht.logoutShareJs ?? defaults.logoutShareJs
      }
    },
    authorization: {
      ...application.authorization,
      api: {
        baseUrl: application.authorization.api?.baseUrl ?? defaults.baseUrl,
        viewId: application.authorization.api?.viewId ?? defaults.viewId
      }
    }
  }
}

/** 将已持久化的应用配置补全为新建表单可直接展示的草稿形状。 */
export function createSettingsDraft(application: ApplicationConfig): ApplicationDraft {
  // Electron 直接读取磁盘文件；旧 v6 配置缺少本期地址时，与提交比较使用相同的补齐规则。
  const normalizedApplication = normalizeSettingsConfiguration(application)
  if (application.datasource.type === DatasourceEnum.DB) {
    const database = application.datasource.db ?? { useBuiltin: false }
    return {
      ...normalizedApplication,
      projectPath: application.workspaceRoot,
      branchName: application.branchName ?? '',
      envText: '',
      datasource: {
        type: DatasourceEnum.DB,
        db: {
          ...database,
          connectionMode: database.dbidMode ? 'dbid' : 'plant'
        }
      }
    }
  }

  return {
    ...normalizedApplication,
    projectPath: application.workspaceRoot,
    branchName: application.branchName ?? '',
    envText: '',
    datasource: application.datasource
  }
}
