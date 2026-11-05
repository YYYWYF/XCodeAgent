import type { RecoveryReadDiagnostic } from '../../typings'

/** 用同一份诊断文本供界面和复制使用，只列出允许公开的字段。 */
export function errorDiagnosticText(message: string, diagnostic?: RecoveryReadDiagnostic): string {
  if (!diagnostic) return message
  return [
    message,
    `发生操作：${diagnostic.operation}`,
    `异常类型：${diagnostic.exceptionType}`,
    `具体原因：${diagnostic.reason}`,
    `诊断编号：${diagnostic.diagnosticId}`,
    ...(diagnostic.sourceRunId ? [`关联任务：${diagnostic.sourceRunId}`] : []),
    ...(diagnostic.threadId ? [`关联会话：${diagnostic.threadId}`] : [])
  ].join('\n')
}
