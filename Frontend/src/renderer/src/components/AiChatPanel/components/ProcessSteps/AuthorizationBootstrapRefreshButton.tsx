import { ReloadOutlined } from '@ant-design/icons'
import { Button, message } from 'antd'
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { refreshBuiltinAuthorization } from '../../../../service/builtinAuthorization'
import type { WorkflowAuthorizationBootstrap } from '../../../../service/authorizationBootstrap'

/** 只在点击时查询原批次，防止重复点击及切换工作区后的迟到回执覆盖。 */
export default function AuthorizationBootstrapRefreshButton({ workspaceRoot, operationId, operationKind = 'schema', onRefreshed }: {
  workspaceRoot?: string
  operationId: string
  operationKind?: 'schema' | 'seed'
  onRefreshed?: (result: WorkflowAuthorizationBootstrap) => void
}): ReactElement {
  const [refreshing, setRefreshing] = useState(false)
  const inFlight = useRef<symbol>()
  const identity = `${workspaceRoot}:${operationKind}:${operationId}`
  const currentIdentity = useRef(identity)
  currentIdentity.current = identity
  useEffect(() => {
    currentIdentity.current = identity
    inFlight.current = undefined
    setRefreshing(false)
    return () => { inFlight.current = undefined }
  }, [identity])
  /** 单次查询期间锁定按钮，失败保留已有结果并允许再次手动刷新。 */
  async function refreshStatus(): Promise<void> {
    if (!workspaceRoot || inFlight.current) return
    const request = Symbol()
    inFlight.current = request
    setRefreshing(true)
    try {
      const next = await refreshBuiltinAuthorization(workspaceRoot, operationId, operationKind)
      if (currentIdentity.current === identity && inFlight.current === request) onRefreshed?.(next)
    } catch (error) {
      if (currentIdentity.current === identity && inFlight.current === request) message.error(error instanceof Error ? error.message : '刷新失败，请稍后重试。')
    } finally {
      if (currentIdentity.current === identity && inFlight.current === request) {
        inFlight.current = undefined
        setRefreshing(false)
      }
    }
  }
  return (
    <Button
      type="primary"
      icon={<ReloadOutlined />}
      loading={refreshing}
      disabled={!workspaceRoot}
      onClick={refreshStatus}
    >
      刷新状态
    </Button>
  )
}
