import { useEffect, type Dispatch, type SetStateAction } from 'react'
import { message } from 'antd'
import type { ApplicationConfig } from '../typings'
import { APPLICATION_CONFIG_REFRESHED_EVENT } from '../service/applicationConfigOperation'
import { loadWorkspaceApplicationConfig } from '../service/applicationStorage'

/** 配置事务完成后只读刷新当前工作区，阻止后续卡片继续消费过期快照。 */
export function useApplicationConfigRefresh(
  workspaceRoot: string,
  setApplication: Dispatch<SetStateAction<ApplicationConfig>>
): void {
  useEffect(() => {
    let active = true
    /** 只响应当前工作区的配置刷新通知，过期读取不得污染已切换应用。 */
    const refresh = (event: Event): void => {
      if ((event as CustomEvent<{ workspaceRoot: string }>).detail.workspaceRoot !== workspaceRoot) return
      void loadWorkspaceApplicationConfig(workspaceRoot).then((schema) => {
        if (active) setApplication((current) => ({ ...current, ...schema }))
      }).catch(() => {
        if (active) message.error('应用配置读取失败，请重新打开应用。')
      })
    }
    window.addEventListener(APPLICATION_CONFIG_REFRESHED_EVENT, refresh)
    return () => {
      active = false
      window.removeEventListener(APPLICATION_CONFIG_REFRESHED_EVENT, refresh)
    }
  }, [workspaceRoot, setApplication])
}
