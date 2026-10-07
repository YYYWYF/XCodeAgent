import type { RecoveryFailureDiagnostic } from './recoveryActionPlan'

const MODEL_FAILURE_MESSAGES: Record<string, string> = {
  UNIT_GENERATION_MODEL_SETUP_FAILED: '模型调用准备失败，请检查模型配置。',
  UNIT_GENERATION_MODEL_REQUEST_TIMEOUT: '模型请求超时，请重试。',
  UNIT_GENERATION_MODEL_CONNECTION_FAILED: '无法连接模型服务，请检查连接后重试。',
  UNIT_GENERATION_MODEL_RESPONSE_INVALID: '模型服务返回的数据无法读取，请重试。',
  UNIT_GENERATION_MODEL_CALL_FAILED: '模型调用失败，未能确定具体原因。请重试。',
  UNIT_GENERATION_INFRASTRUCTURE_FAILURE: '生成执行计划失败，未能确定具体原因。请重试。'
}

/** 优先使用同一执行的后端中文说明，仅以明确错误码或 HTTP 状态补充模型原因。 */
export function recoveryFailureMessage(diagnostic?: RecoveryFailureDiagnostic | null): string | undefined {
  if (!diagnostic) return undefined
  if (diagnostic.userMessage?.trim()) return diagnostic.userMessage.trim()
  if (MODEL_FAILURE_MESSAGES[diagnostic.code]) return MODEL_FAILURE_MESSAGES[diagnostic.code]
  if (diagnostic.code === 'UNIT_GENERATION_MODEL_HTTP_ERROR' || diagnostic.origin === 'model_call') {
    switch (diagnostic.httpStatus) {
      case 401: return '模型服务认证失败，请检查访问凭据。'
      case 403: return '模型服务拒绝访问，请检查访问权限。'
      case 404: return '模型服务找不到请求的资源，请检查模型名称和服务地址。'
      case 429: return '模型服务限制了本次请求，请检查服务额度或稍后重试。'
      case 500:
      case 502:
      case 503:
      case 504: return '模型服务暂时无法完成请求，请稍后重试。'
      default: return '模型调用失败，未能确定具体原因。请查看错误详情。'
    }
  }
  return undefined
}
