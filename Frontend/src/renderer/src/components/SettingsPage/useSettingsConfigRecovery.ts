import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { message, type FormInstance } from 'antd'
import type { ApplicationConfig, ApplicationDraft } from '../../typings'
import { APPLICATION_CONFIG_REFRESHED_EVENT, inspectPendingConfigIdentity, resumePendingConfigOperation } from '../../service/applicationConfigOperation'
import { loadWorkspaceApplicationConfig } from '../../service/applicationStorage'
import { configOperationFailureMessage } from './settingsConfigChanges'
import { createSettingsDraft } from './settingsDraft'

/** 管理显式恢复入口，原提案成功后才以最新磁盘值替换设置草稿。 */
export function useSettingsConfigRecovery(
  application: ApplicationConfig,
  form: Pick<FormInstance<ApplicationDraft>, 'setFieldsValue'>,
  setSaving: Dispatch<SetStateAction<boolean>>,
  onSaved: (application: ApplicationConfig) => void
) {
  const workspaceRoot = application.workspaceRoot
  const [pending, setPending] = useState(() => inspectPendingConfigIdentity(workspaceRoot))
  const canResume = pending.kind === 'mine'
  const recoveryMessage = pending.kind === 'other' ? '请回到发起配置的原会话继续操作。'
    : pending.kind === 'invalid' ? '无法确认上次配置的身份，已保留原记录。'
    : canResume ? '继续上次未完成的工程同步，保留现有工程。' : undefined
  /** 在提交失败后重新检查本地恢复身份。 */
  const refreshPending = (): void => setPending(inspectPendingConfigIdentity(workspaceRoot))
  useEffect(() => {
    /** 磁盘刷新或切换应用时同步恢复按钮。 */
    const refresh = (): void => setPending(inspectPendingConfigIdentity(workspaceRoot))
    refresh()
    window.addEventListener(APPLICATION_CONFIG_REFRESHED_EVENT, refresh)
    return () => window.removeEventListener(APPLICATION_CONFIG_REFRESHED_EVENT, refresh)
  }, [workspaceRoot])
  /** 用户显式恢复原 Desired，前端不重传旧密钥或提交编辑后的提案。 */
  const handleResume = async (): Promise<void> => {
    if (inspectPendingConfigIdentity(workspaceRoot).kind !== 'mine') {
      refreshPending()
      return
    }
    setSaving(true)
    try {
      const operation = await resumePendingConfigOperation(workspaceRoot)
      if (operation.status !== 'APPLIED') throw new Error('工程配置尚未完成同步，请检查后重试。')
      const disk = await loadWorkspaceApplicationConfig(workspaceRoot)
      const current = { ...application, ...disk }
      form.setFieldsValue(createSettingsDraft(current))
      onSaved(current)
    } catch (error) {
      message.error(configOperationFailureMessage(error, {}))
    } finally {
      refreshPending()
      setSaving(false)
    }
  }
  return { canResume, recoveryMessage, refreshPending, handleResume }
}
