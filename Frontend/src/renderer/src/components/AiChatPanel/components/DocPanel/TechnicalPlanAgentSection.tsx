import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined
} from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  recordItems,
  stringItems,
  textValue,
  type JsonRecord
} from './TechnicalPlanDocPanelData'
import './TechnicalPlanAgentSection.less'

const { Text } = Typography

type Props = {
  contracts: JsonRecord[]
  sectionKey: string
}

/** 组合智能体运行时标签，缺失字段不会生成空标签。 */
function runtimeLabels(contract: JsonRecord): string[] {
  const runtime = asRecord(contract.runtime)
  const language = textValue(runtime.language)
  const version = textValue(runtime.pythonVersion)
  return [
    language && version ? `${language} ${version}` : language,
    textValue(runtime.framework),
    textValue(runtime.deployment)
  ]
    .map((item) => item.trim())
    .filter(Boolean)
}

/** 把安全与会话字段压缩成可审阅的标签，避免展示原始 JSON。 */
function governanceLabels(contract: JsonRecord): string[] {
  const session = asRecord(contract.session)
  const security = asRecord(contract.security)
  const model = asRecord(contract.model)
  return [
    session.supportsMultiTurn ? '支持多轮会话' : '单轮会话',
    `记忆：${textValue(session.memory, 'none')}`,
    `模型：${textValue(model.selection, 'project_default')}`,
    security.directClientAccess === false ? '禁止客户端直连' : '客户端访问未限制',
    `身份转发：${textValue(security.authForwarding, '未声明')}`
  ]
}

/** 渲染单个能力到工具的绑定关系。 */
function CapabilityBindings({ contract }: { contract: JsonRecord }): ReactElement {
  const bindings = recordItems(contract.capabilityBindings)
  return (
    <div className={cx('technical-plan-agent-binding-list')}>
      {bindings.length ? (
        bindings.map((binding, index) => (
          <div
            className={cx('technical-plan-agent-binding-row')}
            key={textValue(binding.capabilityId, `capability-${index}`)}
          >
            <code>{textValue(binding.capabilityId, `capability-${index + 1}`)}</code>
            <ArrowRightOutlined aria-hidden="true" />
            <span>{stringItems(binding.toolIds).join('、') || '暂无工具'}</span>
          </div>
        ))
      ) : (
        <Text type="secondary">暂无能力工具绑定</Text>
      )}
    </div>
  )
}

/** 渲染工具到业务 API Endpoint 的确定性绑定。 */
function ToolBindings({ contract }: { contract: JsonRecord }): ReactElement {
  const bindings = recordItems(contract.toolBindings)
  return (
    <div className={cx('technical-plan-agent-binding-list')}>
      {bindings.length ? (
        bindings.map((binding, index) => (
          <div
            className={cx('technical-plan-agent-tool-row')}
            key={textValue(binding.toolId, `tool-${index}`)}
          >
            <div>
              <code>{textValue(binding.toolId, `tool-${index + 1}`)}</code>
              <Tag>{textValue(binding.accessMode, 'read')}</Tag>
            </div>
            <ArrowRightOutlined aria-hidden="true" />
            <span>{textValue(binding.endpointId, '未绑定 Endpoint')}</span>
          </div>
        ))
      ) : (
        <Text type="secondary">暂无工具 Endpoint 绑定</Text>
      )}
    </div>
  )
}

/** 展示智能体代码产物路径和 TechnicalPlan 要求执行的检查。 */
function ArtifactChecks({ contract }: { contract: JsonRecord }): ReactElement {
  const artifacts = asRecord(contract.artifacts)
  const artifactPaths = [artifacts.agentPath, artifacts.toolAdapterPath, artifacts.testPath]
    .map((item) => textValue(item).trim())
    .filter(Boolean)
  const checks = stringItems(contract.requiredChecks)
  return (
    <div className={cx('technical-plan-agent-delivery')}>
      <div>
        <strong>代码产物</strong>
        {artifactPaths.map((path) => (
          <code key={path}>{path}</code>
        ))}
      </div>
      <div>
        <strong>Required checks</strong>
        {checks.map((check) => (
          <span key={check}>
            <CheckCircleOutlined aria-hidden="true" />
            <code>{check}</code>
          </span>
        ))}
      </div>
    </div>
  )
}

/** 渲染 TechnicalPlan 中的平台智能体契约，普通应用不会挂载本章节。 */
export default function AgentContractsSection({ contracts, sectionKey }: Props): ReactElement {
  return (
    <section
      aria-label="智能体契约"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <RobotOutlined /> <span>智能体契约</span>
        <Tag>{contracts.length}</Tag>
      </div>
      <div className={cx('technical-plan-agent-list')}>
        {contracts.map((contract, index) => {
          const agentId = textValue(contract.agentId, `agent-${index + 1}`)
          const invocation = asRecord(contract.invocation)
          const knowledgeReferences = stringItems(contract.knowledgeReferences)
          return (
            <article className={cx('technical-plan-agent-card')} key={agentId}>
              <header className={cx('technical-plan-agent-header')}>
                <div>
                  <strong>{agentId}</strong>
                  <span>{textValue(asRecord(contract.runtime).serviceName, 'agent-runtime')}</span>
                </div>
                <div>
                  {runtimeLabels(contract).map((label) => (
                    <Tag key={label}>{label}</Tag>
                  ))}
                </div>
              </header>

              <div className={cx('technical-plan-agent-gateway')}>
                <span>{textValue(invocation.transport, 'ag-ui-sse')}</span>
                <code>{textValue(invocation.gatewayEndpointId, '未绑定 Java 网关 Endpoint')}</code>
                <code>{textValue(invocation.internalPath, '未声明 sidecar 内部路径')}</code>
              </div>

              <div className={cx('technical-plan-agent-grid')}>
                <section>
                  <strong>能力 → 工具</strong>
                  <CapabilityBindings contract={contract} />
                </section>
                <section>
                  <strong>工具 → API Endpoint</strong>
                  <ToolBindings contract={contract} />
                </section>
              </div>

              <div className={cx('technical-plan-agent-governance')}>
                <SafetyCertificateOutlined aria-hidden="true" />
                <div>
                  {governanceLabels(contract).map((label) => (
                    <Tag key={label}>{label}</Tag>
                  ))}
                </div>
                <span>知识引用：{knowledgeReferences.join('、') || '无'}</span>
              </div>

              <ArtifactChecks contract={contract} />
            </article>
          )
        })}
      </div>
    </section>
  )
}
