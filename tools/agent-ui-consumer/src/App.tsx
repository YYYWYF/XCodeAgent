import { memo, useCallback, useState, type ReactElement } from 'react'
import { Agent as GeneratedAgent } from './agent-ui'

const Agent = memo(GeneratedAgent)

/** 使用官方默认展示；运行状态、发送和停止均由唯一 Agent 处理。 */
export function App(): ReactElement {
  const [error, setError] = useState('')
  const scenario = new URLSearchParams(window.location.search).get('scenario') ?? 'simple-chat'
  const endpoint = `/agent?scenario=${encodeURIComponent(scenario)}&speed=1`
  /** 稳定回调引用，防止 canonical Runtime 将宿主重渲染解释为运行配置切换。 */
  const reportError = useCallback((value: Error): void => setError(value.message), [])

  return (
    <main className="consumer">
      <p className="preview-source">数据来源：官方 Mock 预览</p>
      {error && <p role="alert">{error}</p>}
      <section className="agent-surface" aria-label="智能体会话">
        <Agent endpoint={endpoint} locale="zh-CN" onError={reportError} />
      </section>
    </main>
  )
}
