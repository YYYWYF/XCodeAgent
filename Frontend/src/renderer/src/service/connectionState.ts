export type ConnectionStatus = 'healthy' | 'connecting' | 'reconnecting' | 'unavailable'

export type ConnectionState = {
  status: ConnectionStatus
  requestGeneration: number
  lastError?: string
  lastConnectedAt?: number
  lastFailureAt?: number
}

/** 创建只描述 renderer 到 Backend 通信能力的初始连接状态。 */
export function initialConnectionState(connected = false): ConnectionState {
  return connected
    ? { status: 'healthy', requestGeneration: 0, lastConnectedAt: Date.now() }
    : { status: 'connecting', requestGeneration: 0 }
}

/** 开始新的连接或权威刷新请求，并用递增代次隔离晚到结果。 */
export function beginConnectionRequest(
  current: ConnectionState,
  requestGeneration: number
): ConnectionState {
  if (requestGeneration < current.requestGeneration) return current
  return {
    ...current,
    status: current.lastConnectedAt ? 'reconnecting' : 'connecting',
    requestGeneration
  }
}

/** 只接受最新请求的成功结果，旧请求不得覆盖更新后的连接状态。 */
export function completeConnectionRequest(
  current: ConnectionState,
  requestGeneration: number,
  connectedAt = Date.now()
): ConnectionState {
  if (requestGeneration < current.requestGeneration) return current
  return {
    status: 'healthy',
    requestGeneration,
    lastConnectedAt: connectedAt
  }
}

/** 只接受最新请求的失败结果；错误不会携带或改写任何 durable Recovery 事实。 */
export function failConnectionRequest(
  current: ConnectionState,
  requestGeneration: number,
  error: string,
  failedAt = Date.now()
): ConnectionState {
  if (requestGeneration < current.requestGeneration) return current
  return {
    ...current,
    status: 'unavailable',
    requestGeneration,
    lastError: error.trim() || 'Backend 暂时不可用，无法同步最新状态。',
    lastFailureAt: failedAt
  }
}

/** 判断当前连接是否允许用户显式提交会改变 Workflow 的动作。 */
export function connectionAllowsMutation(connection: ConnectionState): boolean {
  return connection.status === 'healthy'
}
