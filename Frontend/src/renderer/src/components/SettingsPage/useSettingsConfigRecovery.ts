import { useState, type Dispatch, type SetStateAction } from 'react'
import { message, type FormInstance } from 'antd'
import type { ApplicationConfig, ApplicationDraft } from '../../typings'
import { hasPendingConfigOperation, resumePendingConfigOperation } from '../../service/applicationConfigOperation'
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
  const [canResume, setCanResume] = useState(() => hasPendingConfigOperation(application.workspaceRoot))
  /** 在提交失败后重新检查本地恢复身份。 */
  const refreshPending = (): void => setCanResume(hasPendingConfigOperation(application.workspaceRoot))
  /** 用户显式恢复原 Desired，前端不重传旧密钥或提交编辑后的提案。 */
  const handleResume = async (): Promise<void> => {
    setSaving(true)
    try {
      await resumePendingConfigOperation(application.workspaceRoot)
      const disk = await loadWorkspaceApplicationConfig(application.workspaceRoot)
      const current = { ...application, ...disk }
      form.setFieldsValue(createSettingsDraft(current))
      onSaved(current)
      setCanResume(false)
    } catch (error) {
      message.error(configOperationFailureMessage(error, {}))
    } finally {
      setSaving(false)
    }
  }
  return { canResume, refreshPending, handleResume }
}
