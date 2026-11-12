import { useEffect } from 'react'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'

/** 本次入口已有恢复结果时直接采纳；只有没有入口结果的挂载才补读。 */
export function useWorkbenchLifecycleRecovery(
  application: Pick<ApplicationConfig, 'id' | 'appName' | 'workspaceRoot'>,
  entryLifecycle: ApplicationLifecycle | undefined,
  onChange: (lifecycle: ApplicationLifecycle) => void,
  read = getApplicationLifecycle
): void {
  useEffect(() => {
    if (!application.workspaceRoot) return
    if (entryLifecycle) {
      onChange(entryLifecycle)
      return
    }
    let active = true
    void read(application).then((lifecycle) => {
      if (active) onChange(lifecycle)
    }).catch((error) => {
      if (active) console.warn('读取工作台应用生命周期失败，继续保留工作台。', error)
    })
    return () => { active = false }
  }, [application.id, application.appName, application.workspaceRoot, entryLifecycle, onChange, read])
}
