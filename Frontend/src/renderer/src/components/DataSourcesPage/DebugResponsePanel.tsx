import { Alert, Button, Radio, Tabs, Typography } from 'antd'
import { useMemo, useState } from 'react'
import type { DebugResponse } from '../../service/externalApiDebug'
import { cx } from '../../utils'
import type { Entry } from './debugDraft'
import { sectionMeta } from './dataSourceOperationConfig'
import { JsonStructureViewer } from './JsonStructureViewer'
import { parseJsonSampleText } from './jsonStructure'

type BodyView = 'formatted' | 'raw' | 'structure'

/** 优先格式化 JSON；普通响应保持原文。 */
function formatBody(body: string, raw: boolean): string {
  if (raw) return body
  try { return JSON.stringify(JSON.parse(body), null, 2) }
  catch { return body }
}

/** 显示实测响应与历史，结构视图只列出本次可以同步的 JSON 字段。 */
export default function DebugResponsePanel({ busy, response, responseSample, stale, restoredRedacted, error, history, onLoad }: {
  busy: boolean; response?: DebugResponse; responseSample?: string; stale: boolean; restoredRedacted: boolean; error: string; history: Entry[]
  onLoad: (entry: Entry) => void
}) {
  const [view, setView] = useState<BodyView>('formatted')
  const structure = useMemo(() => parseJsonSampleText(responseSample ?? '').value, [responseSample])
  const bodyView = view === 'structure' && structure === undefined ? 'formatted' : view
  const good = response && response.statusCode >= 200 && response.statusCode < 300
  const body = response?.body ?? ''
  const rendered = useMemo(() => formatBody(body, bodyView === 'raw'), [body, bodyView])
  return <section className="debug-response" aria-label="响应">
    <div className="debug-metrics" role="status">
      {response ? <>
        {stale && <span>上一次响应</span>}
        <strong className={good ? 'debug-status is-success' : 'debug-status is-error'}>HTTP {response.statusCode}</strong>
        <span>{response.elapsedMs} ms</span>
        <span>{response.bytes < 1024 ? response.bytes + ' B' : (response.bytes / 1024).toFixed(1) + ' KB'}</span>
        {response.truncated && <span>响应体已截断</span>}
        {restoredRedacted && <span>敏感内容已隐藏</span>}
        {busy && <span>正在等待新响应…</span>}
      </> : <strong>{busy ? '正在等待响应…' : '响应'}</strong>}
    </div>
    {error && <Alert type="error" showIcon message={error} />}
    <Tabs className="source-operation-subtabs" size="small" defaultActiveKey="body">
      <Tabs.TabPane tab={sectionMeta('responseBody').title} key="body">
        {response ? <>
          <div className="debug-body-tools">
            <Radio.Group size="small" value={bodyView} onChange={event => setView(event.target.value)}>
              <Radio.Button value="formatted">格式化</Radio.Button><Radio.Button value="raw">原始</Radio.Button>
              <Radio.Button value="structure" disabled={structure === undefined}>结构</Radio.Button>
            </Radio.Group>
            {bodyView !== 'structure' && <Typography.Text copyable={{text: rendered}}>复制</Typography.Text>}
          </div>
          {bodyView === 'structure' ? <>
            <p className="debug-muted">可同步的 JSON 字段</p>
            <div className={cx('data-source-json-panel-content-only')}><JsonStructureViewer value={structure} /></div>
          </> : <pre className="debug-code">{rendered || '（空响应体）'}</pre>}
        </> : !error && <div className="debug-empty">{busy ? '请求已发送，正在等待服务器返回。' : '发送请求后在此查看响应体。'}</div>}
      </Tabs.TabPane>
      <Tabs.TabPane tab="响应头" key="headers">
        {response ? <dl className="debug-response-headers">{Object.entries(response.headers).map(([name, value]) =>
          <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl> : <div className="debug-empty">暂无响应头</div>}
      </Tabs.TabPane>
      <Tabs.TabPane tab="调用记录" key="history">
        {history.length ? history.map(entry => <div className="debug-history-row" key={entry.id}>
          <span>{entry.time}</span><strong>{entry.label}</strong>
          {entry.elapsed !== undefined && <span>{entry.elapsed} ms</span>}
          <Button size="small" disabled={busy} onClick={() => onLoad(entry)}>载入参数</Button>
        </div>) : <div className="debug-empty">暂无调用记录</div>}
      </Tabs.TabPane>
    </Tabs>
  </section>
}
