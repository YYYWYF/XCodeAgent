import { Component, type ReactElement, type ReactNode } from 'react'
import { Alert } from 'antd'

/** 防止配置卡异常破坏整个工作台；不输出可能含凭据的异常内容。 */
export default class ConfigCardBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  /** 将子组件渲染异常转换为局部错误状态。 */
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  /** 保留工作台并提示重新读取配置。 */
  render(): ReactNode {
    return this.state.failed
      ? <Alert type="error" showIcon message="配置卡显示失败，请重新打开并读取应用配置。" />
      : this.props.children
  }
}

/** 校验渲染所需根结构，合法可选对象只在表单草稿中补齐。 */
export function configurationViewError(application: unknown): string | undefined {
  const record = application as Record<string, unknown> | null
  for (const key of ['auth', 'authorization', 'theme']) {
    const value = record?.[key]
    if (!value || typeof value !== 'object' || Array.isArray(value)) return `${key} 必须是对象`
  }
  const auth = record?.auth as Record<string, unknown>
  const authorization = record?.authorization as Record<string, unknown>
  if (typeof auth.enable !== 'boolean') return 'auth.enable 必须是布尔值'
  if (typeof authorization.enabled !== 'boolean') return 'authorization.enabled 必须是布尔值'
  if (!Array.isArray(authorization.initialAdministratorSubjects)) return 'authorization.initialAdministratorSubjects 必须是数组'
  for (const [path, value] of [['auth.yht', auth.yht], ['authorization.api', authorization.api]] as const) {
    if (value !== undefined && (!value || typeof value !== 'object' || Array.isArray(value))) return `${path} 必须是对象`
  }
  if (auth.enable && !auth.yht) return 'auth.yht 必须是对象'
  if (authorization.enabled && !authorization.api) return 'authorization.api 必须是对象'
  const api = authorization.api as Record<string, unknown> | undefined
  if (api && (typeof api.baseUrl !== 'string' || typeof api.viewId !== 'string')) return 'authorization.api.baseUrl/viewId 必须是字符串'
  return undefined
}

/** 显示具体字段结构错误，不创建虚假的持久配置。 */
export function InvalidConfiguration({ reason }: { reason: string }): ReactElement {
  return <Alert type="error" showIcon message="配置无法读取" description={`${reason}；请重新读取 application.json。`} />
}
