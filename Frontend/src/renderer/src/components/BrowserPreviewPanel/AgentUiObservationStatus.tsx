import type { ReactElement } from 'react'
import type { AgentUiObservation } from '../../service/previewRuntime'

const sources = {
  'official-mock': 'Mock · 模拟回复',
  'real-text': '真实 Python · 开发接线',
  'formal-text': '真实 Python · 正式接线',
  'formal-business': '真实 Python · 业务接线',
  unknown: '未知或当前连接未就绪'
} as const

/** 展示当前磁盘和启动身份的只读差异，不提供来源切换或业务执行按钮。 */
export default function AgentUiObservationStatus({
  observation
}: {
  observation?: AgentUiObservation | null
}): ReactElement | null {
  if (!observation) return null
  const identity = observation.identity
  return (
    <details>
      <summary>来源与身份</summary>
      <p>当前来源：{sources[observation.source]}</p>
      <p>启动会话：{observation.sessionId || '未捕获，请重启受管预览'}</p>
      <p>Python 启动：{observation.launchId || '未验证'}</p>
      <p>磁盘与启动摘要：{observation.identityMatches ? '一致' : '未确认或已变化，需重启后复核'}</p>
      {identity.status === 'available' && (
        <dl style={{ overflowWrap: 'anywhere' }}>
          <dt>当前转入计划</dt>
          <dd>{identity.transferPlanId}</dd>
          <dt>已转入共享文件的当前 SHA-256</dt>
          <dd>{identity.sourceSha256}</dd>
          <dt>当前锁文件 SHA-256</dt>
          <dd>{identity.lockSha256}</dd>
          <dt>实际包版本与元数据 SHA-256</dt>
          {identity.packages?.map((item) => (
            <dd key={item.name}>
              {item.name}@{item.version}
              <br />
              {item.metadataSha256}
            </dd>
          ))}
        </dl>
      )}
      <p>以上是观察摘要，不替代转入验证或业务验收。回放尚未实现。</p>
    </details>
  )
}
