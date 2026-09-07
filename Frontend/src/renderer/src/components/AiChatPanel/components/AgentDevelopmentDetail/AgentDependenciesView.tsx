import { CheckCircleOutlined, ClockCircleOutlined } from '@ant-design/icons'
import { List, Tag } from 'antd'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningAgentOption } from '../../../../typings'
import { cx } from '../../../../utils'

type Props = Pick<DevelopmentPlanningAgentOption, 'dependencies' | 'artifacts' | 'requiredChecks'>

/** 展示 Agent Runtime、网关、工具、实体、页面和实现文件的只读依赖状态。 */
export default function AgentDependenciesView({
  dependencies,
  artifacts,
  requiredChecks
}: Props): ReactElement {
  const gateway = dependencies.gateway
  const runtime = dependencies.runtime
  return (
    <>
      <section className={cx('agent-detail-section')}>
        <h3>依赖与运行时</h3>
        <dl className={cx('agent-dependency-grid')}>
          <div>
            <dt>Runtime</dt>
            <dd>
              {String(runtime.status || 'unknown')} · {String(runtime.branch || '-')}
              <code>{String(runtime.commitSha || '').slice(0, 12) || '无 commit'}</code>
            </dd>
          </div>
          <div>
            <dt>Java Gateway</dt>
            <dd>
              <code>{String(gateway.endpointId || '-')}</code>
              <span>
                {String(gateway.method || '')} {String(gateway.path || '')}
              </span>
            </dd>
          </div>
          <div>
            <dt>Tools</dt>
            <dd>{dependencies.tools.length} 个 Endpoint 绑定</dd>
          </div>
          <div>
            <dt>实体</dt>
            <dd>{dependencies.entities.length} 个数据实体依赖</dd>
          </div>
          <div>
            <dt>入口页面</dt>
            <dd>{dependencies.pages.length} 个页面 Action 绑定</dd>
          </div>
        </dl>
        <List
          dataSource={dependencies.tools}
          locale={{ emptyText: '当前 Agent 无 Tool 绑定' }}
          renderItem={(tool) => {
            const endpoint = (tool.endpoint || {}) as Record<string, unknown>
            return (
              <List.Item>
                <List.Item.Meta
                  description={`${String(tool.accessMode || '')} · ${String(endpoint.method || '')} ${String(endpoint.path || '')}`}
                  title={String(tool.name || tool.toolId || 'Tool')}
                />
              </List.Item>
            )
          }}
          size="small"
        />
      </section>
      <section className={cx('agent-detail-section')}>
        <h3>实现文件</h3>
        <List
          dataSource={artifacts}
          renderItem={(artifact) => (
            <List.Item>
              <span className={cx('agent-artifact-path')}>
                {artifact.exists ? <CheckCircleOutlined /> : <ClockCircleOutlined />}
                <code>{artifact.path}</code>
              </span>
              <Tag color={artifact.exists ? 'green' : 'default'}>
                {artifact.exists ? '已生成' : '待生成'}
              </Tag>
            </List.Item>
          )}
          size="small"
        />
        <div className={cx('agent-required-checks')}>
          <span>Required checks</span>
          {requiredChecks.map((check) => (
            <code key={check}>{check}</code>
          ))}
        </div>
      </section>
    </>
  )
}
