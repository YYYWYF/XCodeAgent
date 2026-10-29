import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { agentInvocation } from './TechnicalPlanAgentContracts'
import { stringItems } from './TechnicalPlanDocPanelData'
import './TechnicalPlanAgentInvocation.less'

/** 复用既有主题样式展示固定 AG-UI 契约，不把事件流伪装成普通 JSON 响应。 */
export default function TechnicalPlanAgentInvocation({
  agentId,
  pageIds
}: {
  agentId: string
  pageIds: unknown
}): ReactElement {
  const invocation = agentInvocation(agentId)
  return (
    <div className={cx('technical-plan-inspector', 'technical-plan-agent-invocation')}>
      <div className={cx('technical-plan-inspector-title')}>
        <span className={cx('technical-plan-method', 'is-post')}>{invocation.method}</span>
        <strong>智能体会话</strong>
        <code>{invocation.path}</code>
      </div>
      <dl className={cx('technical-plan-inspector-grid')}>
        <div>
          <dt>接口标识</dt>
          <dd>{invocation.endpointId}</dd>
        </div>
        <div>
          <dt>协议 / 传输</dt>
          <dd>AG-UI / SSE（text/event-stream）</dd>
        </div>
        <div>
          <dt>服务</dt>
          <dd>{invocation.serviceId}</dd>
        </div>
        <div>
          <dt>请求</dt>
          <dd>{invocation.requestType}（threadId、runId、messages 等标准字段）</dd>
        </div>
        <div>
          <dt>响应</dt>
          <dd>{invocation.responseType} 事件流，包含运行生命周期、消息与错误事件</dd>
        </div>
        <div>
          <dt>身份</dt>
          <dd>Runtime 匿名会话，按可信 Principal/thread 隔离</dd>
        </div>
        <div>
          <dt>关联页面</dt>
          <dd>{stringItems(pageIds).join('、')}</dd>
        </div>
      </dl>
      <p>所有关联页面共用此接口。调用契约不代表接口已实现或真实集成已完成。</p>
    </div>
  )
}
