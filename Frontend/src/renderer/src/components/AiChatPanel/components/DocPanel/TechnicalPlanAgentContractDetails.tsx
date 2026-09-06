import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  LockOutlined,
  SettingOutlined
} from '@ant-design/icons'
import { Collapse, Tag, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  recordItems,
  stringItems,
  textValue,
  type JsonRecord
} from './TechnicalPlanDocPanelData'

const { Panel } = Collapse
const { Text } = Typography

/** 统一渲染设置项，避免把 Agent Contract 退化成原始 JSON。 */
function SettingField({ label, children }: { label: string; children: ReactNode }): ReactElement {
  return (
    <div className={cx('technical-plan-agent-field')}>
      <span>{label}</span>
      <div>{children}</div>
    </div>
  )
}

/** 把布尔配置转换成面向用户的启用状态。 */
function enabledLabel(value: unknown): string {
  return value === true ? '启用' : '关闭'
}

/** 展示从 ProductPlan 投影出的只读业务事实。 */
export function AgentProductFacts({ contract }: { contract: JsonRecord }): ReactElement {
  const identity = asRecord(contract.identity)
  const interaction = asRecord(contract.interaction)
  const clarification = asRecord(interaction.clarification)
  const capabilities = recordItems(contract.capabilities)
  const boundaries = stringItems(identity.boundaries)
  return (
    <section className={cx('technical-plan-agent-product-facts')}>
      <div className={cx('technical-plan-agent-subtitle')}>
        <strong>产品事实</strong>
        <Tag>来自 ProductPlan</Tag>
      </div>
      <div className={cx('technical-plan-agent-fact-grid')}>
        <SettingField label="Agent ID">
          <code>{textValue(contract.agentId, '未声明')}</code>
        </SettingField>
        <SettingField label="用途">
          <Text>{textValue(identity.purpose, '未声明')}</Text>
        </SettingField>
        <SettingField label="交互">
          <Text>
            {textValue(interaction.mode, '未声明')} ·{' '}
            {interaction.supportsMultiTurn ? '支持多轮' : '单轮'} · 追问
            {clarification.allowed ? '允许' : '关闭'}
          </Text>
        </SettingField>
        <SettingField label="业务边界">
          <div className={cx('technical-plan-agent-tags')}>
            {boundaries.length ? boundaries.map((item) => <Tag key={item}>{item}</Tag>) : '无'}
          </div>
        </SettingField>
      </div>
      <div className={cx('technical-plan-agent-capabilities')}>
        {capabilities.map((capability, index) => (
          <div key={textValue(capability.capabilityId, `capability-${index}`)}>
            <div>
              <code>{textValue(capability.capabilityId, `capability-${index + 1}`)}</code>
              <strong>{textValue(capability.name, '未命名能力')}</strong>
            </div>
            <Text>{textValue(capability.expectedResult, '未声明预期结果')}</Text>
            <span>
              <ArrowRightOutlined aria-hidden="true" />{' '}
              {stringItems(capability.toolIds).join('、') || '不依赖工具'}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

/** 展示 Prompt 的人设、业务指令与约束。 */
function PromptSettings({ prompt }: { prompt: JsonRecord }): ReactElement {
  const persona = asRecord(prompt.persona)
  const constraints = stringItems(prompt.constraints)
  return (
    <section className={cx('technical-plan-agent-setting-card')}>
      <strong>Prompt</strong>
      <SettingField label="人设">
        <Text>{textValue(persona.role, '未声明')}</Text>
      </SettingField>
      <SettingField label="语气">
        <Text>{textValue(persona.tone, '未声明')}</Text>
      </SettingField>
      <SettingField label="System Prompt">
        <Text>{textValue(prompt.systemPrompt, '未声明')}</Text>
      </SettingField>
      <SettingField label="约束">
        <div className={cx('technical-plan-agent-tags')}>
          {constraints.length ? constraints.map((item) => <Tag key={item}>{item}</Tag>) : '无'}
        </div>
      </SettingField>
    </section>
  )
}

/** 展示项目模型选择和生成参数。 */
function ModelSettings({ model }: { model: JsonRecord }): ReactElement {
  const capabilities = asRecord(model.requiredCapabilities)
  const generation = asRecord(model.generation)
  return (
    <section className={cx('technical-plan-agent-setting-card')}>
      <strong>Model</strong>
      <SettingField label="模型策略">
        <code>{textValue(model.selection, 'project_default')}</code>
      </SettingField>
      <SettingField label="模型引用">
        <code>{textValue(model.modelRef, 'project_default')}</code>
      </SettingField>
      <SettingField label="能力要求">
        <div className={cx('technical-plan-agent-tags')}>
          {Object.entries(capabilities).map(([name, enabled]) => (
            <Tag key={name}>{`${name}：${enabledLabel(enabled)}`}</Tag>
          ))}
        </div>
      </SettingField>
      <SettingField label="Temperature">
        <Text>{textValue(generation.temperature, '未声明')}</Text>
      </SettingField>
    </section>
  )
}

/** 渲染一层 Memory 配置并说明尚未实现的关闭原因。 */
function MemoryTier({
  name,
  value,
  disabledReason
}: {
  name: string
  value: JsonRecord
  disabledReason?: string
}): ReactElement {
  const enabled = value.enabled === true
  return (
    <div className={cx('technical-plan-agent-memory-tier')}>
      <div>
        <strong>{name}</strong>
        <Tag>{enabledLabel(enabled)}</Tag>
      </div>
      <span>
        {enabled
          ? `${textValue(value.store, '未声明')} · ${textValue(value.scope, '未声明')}`
          : disabledReason || '当前未启用'}
      </span>
      {value.connectionRef ? <code>{textValue(value.connectionRef)}</code> : null}
    </div>
  )
}

/** 展示短期、长期和归档三层 Memory。 */
function MemorySettings({ memory }: { memory: JsonRecord }): ReactElement {
  return (
    <section className={cx('technical-plan-agent-setting-card', 'is-wide')}>
      <strong>Memory</strong>
      <div className={cx('technical-plan-agent-memory-grid')}>
        <MemoryTier name="Short-term" value={asRecord(memory.shortTerm)} />
        <MemoryTier
          disabledReason="当前 Runtime 尚未启用长期记忆"
          name="Long-term"
          value={asRecord(memory.longTerm)}
        />
        <MemoryTier
          disabledReason="当前 Runtime 尚未启用 OSS Archive"
          name="Archive"
          value={asRecord(memory.archive)}
        />
      </div>
    </section>
  )
}

/** 展示 Tool 的用途、HTTP Endpoint、Schema 与审批策略。 */
function ToolSettings({ tools }: { tools: JsonRecord }): ReactElement {
  const bindings = recordItems(tools.bindings)
  return (
    <section className={cx('technical-plan-agent-setting-card', 'is-wide')}>
      <div className={cx('technical-plan-agent-setting-heading')}>
        <strong>Tools</strong>
        <Tag>{enabledLabel(tools.enabled)}</Tag>
      </div>
      <div className={cx('technical-plan-agent-tool-list')}>
        {bindings.length ? (
          bindings.map((binding, index) => {
            const endpoint = asRecord(binding.endpoint)
            return (
              <div key={textValue(binding.toolId, `tool-${index}`)}>
                <header>
                  <div>
                    <strong>{textValue(binding.name, '未命名工具')}</strong>
                    <code>{textValue(binding.toolId, `tool-${index + 1}`)}</code>
                  </div>
                  <Tag>{textValue(binding.accessMode, 'read')}</Tag>
                </header>
                <Text>{textValue(binding.description, '未声明使用时机')}</Text>
                <div className={cx('technical-plan-agent-endpoint')}>
                  <Tag>{textValue(endpoint.method, 'HTTP')}</Tag>
                  <code>{textValue(endpoint.path, '未声明路径')}</code>
                  <span>{textValue(endpoint.endpointId, '未绑定 Endpoint')}</span>
                </div>
                <footer>
                  <span>Request：{textValue(endpoint.requestSchemaRef, '无')}</span>
                  <span>Response：{textValue(endpoint.responseSchemaRef, '无')}</span>
                  <span>审批：{textValue(binding.approvalPolicy, '未声明')}</span>
                </footer>
              </div>
            )
          })
        ) : (
          <Text type="secondary">当前 Agent 不使用工具，将以 `tools=[]` 创建。</Text>
        )}
      </div>
    </section>
  )
}

/** 展示 Skills、Knowledge 和 Context 三类高级 AgentSettings。 */
function AdvancedSettings({ settings }: { settings: JsonRecord }): ReactElement {
  const skills = asRecord(settings.skills)
  const knowledge = asRecord(settings.knowledge)
  const retrieval = asRecord(knowledge.retrieval)
  const context = asRecord(settings.context)
  const budget = asRecord(context.budget)
  const compression = asRecord(context.compression)
  return (
    <div className={cx('technical-plan-agent-advanced-grid')}>
      <section>
        <strong>Skills · {enabledLabel(skills.enabled)}</strong>
        <Text>
          {skills.enabled ? '仅加载正式绑定的 Skill' : '当前 Runtime 尚未接入 Skill Loader'}
        </Text>
        <code>{textValue(skills.loadingPolicy, 'explicit_only')}</code>
      </section>
      <section>
        <strong>Knowledge · {enabledLabel(knowledge.enabled)}</strong>
        <Text>
          {knowledge.enabled ? '仅检索正式知识源' : '当前 Runtime 尚未接入 Knowledge Retriever'}
        </Text>
        <span>
          {textValue(retrieval.strategy, 'semantic')} · topK {textValue(retrieval.topK, '5')} · 引用{' '}
          {textValue(knowledge.citationPolicy, 'disabled')}
        </span>
      </section>
      <section>
        <strong>Context</strong>
        <div className={cx('technical-plan-agent-tags')}>
          {recordItems(context.sources).map((source) => (
            <Tag key={textValue(source.type)}>
              {textValue(source.type)}：{enabledLabel(source.enabled)}
            </Tag>
          ))}
        </div>
        <span>
          Budget：{textValue(budget.strategy, 'model_window')} · 输入{' '}
          {textValue(budget.maxInputRatio, '未声明')} · 输出预留{' '}
          {textValue(budget.reserveOutputRatio, '未声明')}
        </span>
        <span>
          压缩：{textValue(compression.strategy, 'none')}
          {textValue(compression.strategy, 'none') === 'none'
            ? '（当前 Runtime 尚未接入摘要压缩）'
            : ''}
        </span>
      </section>
    </div>
  )
}

/** 渲染七段 AgentSettings，核心配置展开、高级配置默认折叠。 */
export function AgentSettingsReview({ contract }: { contract: JsonRecord }): ReactElement {
  const settings = asRecord(contract.agentSettings)
  return (
    <section className={cx('technical-plan-agent-settings')}>
      <div className={cx('technical-plan-agent-subtitle')}>
        <SettingOutlined aria-hidden="true" />
        <strong>AgentSettings</strong>
        <Tag>用户确认配置</Tag>
      </div>
      <div className={cx('technical-plan-agent-settings-grid')}>
        <PromptSettings prompt={asRecord(settings.prompt)} />
        <ModelSettings model={asRecord(settings.model)} />
        <MemorySettings memory={asRecord(settings.memory)} />
        <ToolSettings tools={asRecord(settings.tools)} />
      </div>
      <Collapse className={cx('technical-plan-agent-collapse')} ghost>
        <Panel forceRender header="高级配置 · Skills / Knowledge / Context" key="advanced">
          <AdvancedSettings settings={settings} />
        </Panel>
      </Collapse>
    </section>
  )
}

/** 展示平台编译生成且不可由 Prompt 改写的契约字段。 */
export function AgentPlatformDetails({ contract }: { contract: JsonRecord }): ReactElement {
  const source = asRecord(contract.source)
  const invocation = asRecord(contract.invocation)
  const runtime = asRecord(contract.runtime)
  const security = asRecord(contract.security)
  const artifacts = asRecord(contract.artifacts)
  const evaluation = asRecord(contract.evaluation)
  const artifactPaths = [artifacts.agentPath, artifacts.toolAdapterPath, artifacts.testPath]
    .map((item) => textValue(item).trim())
    .filter(Boolean)
  return (
    <Collapse className={cx('technical-plan-agent-collapse', 'is-platform')} ghost>
      <Panel
        forceRender
        header={
          <span className={cx('technical-plan-agent-collapse-title')}>
            <LockOutlined aria-hidden="true" /> 平台派生配置 <Tag>只读</Tag>
          </span>
        }
        key="platform"
      >
        <div className={cx('technical-plan-agent-platform-grid')}>
          <section>
            <strong>来源与调用</strong>
            <code>{textValue(source.productPlanSha256, '未绑定 ProductPlan Hash')}</code>
            <span>{textValue(invocation.transport, 'ag-ui-sse')}</span>
            <code>{textValue(invocation.gatewayEndpointId, '未绑定 Java Gateway')}</code>
            <code>{textValue(invocation.internalPath, '未声明 Runtime 路径')}</code>
          </section>
          <section>
            <strong>Runtime 与安全</strong>
            <span>
              {textValue(runtime.language, 'Python')} {textValue(runtime.pythonVersion)} ·{' '}
              {textValue(runtime.framework, 'DeepAgents')}
            </span>
            <code>{textValue(runtime.agentFactory, 'create_deep_agent')}</code>
            <span>客户端直连：{security.directClientAccess === false ? '禁止' : '未限制'}</span>
            <span>身份转发：{textValue(security.authForwarding, '未声明')}</span>
            <span>工具授权：{textValue(security.toolAuthorization, '未声明')}</span>
          </section>
          <section>
            <strong>代码产物</strong>
            {artifactPaths.map((path) => (
              <code key={path}>{path}</code>
            ))}
          </section>
          <section>
            <strong>Required checks</strong>
            {stringItems(contract.requiredChecks).map((check) => (
              <span key={check}>
                <CheckCircleOutlined aria-hidden="true" /> <code>{check}</code>
              </span>
            ))}
          </section>
          <section className={cx('is-wide')}>
            <strong>Evaluation</strong>
            {[
              ...stringItems(evaluation.productAcceptanceCriteria),
              ...stringItems(evaluation.engineeringCriteria)
            ].map((item) => (
              <span key={item}>• {item}</span>
            ))}
          </section>
        </div>
      </Panel>
    </Collapse>
  )
}
