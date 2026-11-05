import { Alert, Spin, Tabs } from 'antd'
import type { DebugHttpResponse } from '../../../../service/runtimeWorkbench'
import type { DebugConversation } from '../../../../service/runtimeDebugConversation'
import AgentDebugEvents from './AgentDebugEvents'

/** JSON 格式化只影响阅读，非 JSON 正文保持原文并保留 HTTP 状态。 */
function readableBody(body: string): string {
  try { return JSON.stringify(JSON.parse(body), null, 2) } catch { return body }
}

/** 展示接口响应与 AG-UI 事件详情，不将 HTTP 成功直接解释为业务成功。 */
export default function AgentDebugResponse({ response, stream, loading }: { response?: DebugHttpResponse; stream?: DebugConversation; loading?: boolean }): JSX.Element {
  const http = response || stream?.http
  const metadata = http && <span className="agent-debug-http-status" data-error={http.statusCode >= 400}>HTTP {http.statusCode} · {http.elapsedMs} ms</span>
  const content = <>
    {http?.protocolMismatch && <Alert type="error" showIcon message="实际 Content-Type 与 JSON 契约不一致，请检查接口响应。" />}
    {!http && !stream?.events?.length ? <div className="agent-debug-response-empty" role="status">
      {loading && <Spin size="small" />}<strong>{loading ? '正在等待接口响应' : '等待接口响应'}</strong>
      <p>{loading ? '收到响应后展示 HTTP 状态、正文和 Headers。' : '完成请求输入并发送后，结果会显示在这里。'}</p></div>
      : <Tabs size="small" items={[
        { key: 'body', label: response ? 'Body' : '最终结果', children:
          <pre>{response ? readableBody(response.body) : stream?.result == null ? '当前没有结构化业务结果' : JSON.stringify(stream.result, null, 2)}</pre> },
        { key: 'headers', label: '响应 Headers', children: <pre>{JSON.stringify(http?.headers || {}, null, 2)}</pre> },
        ...(stream ? [{ key: 'events', label: `执行事件 (${stream.events?.length || 0})`, children: <AgentDebugEvents events={stream.events} /> },
          { key: 'request', label: '请求详情', children: <pre>{JSON.stringify(stream.request || {}, null, 2)}</pre> }] : [])
      ]} />}
  </>
  return stream ? <details className="agent-debug-response is-stream" aria-label="请求与响应详情">
    <summary>请求与执行详情{metadata}</summary>{content}</details>
    : <section className="agent-debug-response" aria-label="请求与响应详情">
      <div className="agent-debug-section-heading"><h4>响应</h4>{metadata}</div>{content}</section>
}
