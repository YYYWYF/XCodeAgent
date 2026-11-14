import { Alert } from 'antd'
import type { ReactElement } from 'react'
import type { AgentUiRuntimeConnection } from '../../service/previewRuntime'
import AgentUiObservationStatus from './AgentUiObservationStatus'

const healthLabels = { ready: '已通过', blocked: '未通过', not_verified: '未验证' } as const

/** 区分实时同源健康、静态条件和未验证的业务连接，不提供额外发送入口。 */
export default function AgentUiConnectionStatus({
  connection
}: {
  connection?: AgentUiRuntimeConnection
}): ReactElement | null {
  if (!connection) return null
  const pending = connection.checks.filter((check) => check.status === 'blocked')
  return (
    <Alert
      className="preview-service-callout"
      type="info"
      showIcon
      message={connection.businessPreview ? '真实业务连接已启用' : connection.plainTextPreview ? '真实文本对话预览已启用' : 'Agent UI 完整连接待完成'}
      description={
        <div>
          {connection.endpoint && <p>目标接口：{connection.endpoint}</p>}
          <p>同源健康：{healthLabels[connection.proxyHealth.status]}</p>
          <p>{connection.proxyHealth.reason}</p>
          <p>
            {connection.businessPreview
              ? '当前 Python 智能体按正式声明处理页面上下文、业务工具、结果和用户交互。请按实际场景完成联调验收。'
              : connection.plainTextPreview
              ? '可发送普通文本，回复来自当前 Python 智能体。页面业务上下文、审批和业务结果尚未验收。'
              : '浏览器会话与真实对话：未验证。真实发送保持禁用。'}
          </p>
          {pending.length > 0 && (
            <details>
              <summary>查看未完成条件（{pending.length}）</summary>
              <ul>
                {pending.map((check) => (
                  <li key={check.id}>{check.reason}</li>
                ))}
              </ul>
            </details>
          )}
          <AgentUiObservationStatus observation={connection.observation} />
        </div>
      }
    />
  )
}
