/** 桌面认证公开合同；不包含用户 token。 */
export type AuthenticationProgress = {
  stage: string
  message: string
  percent: number
}

export type CloudJsonRequest = {
  url: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD'
  headers: Record<string, string>
  body?: string
}

export type AuthenticationResult = {
  status: 'completed' | 'failed' | 'in_progress'
  authenticated?: boolean
  workingDir?: string
  user?: { id: string; name: string }
  expiresAt?: number
  progress?: AuthenticationProgress
  authorizationUrl?: string
  httpStatus?: number
  body?: unknown
  error?: { message?: string }
}
