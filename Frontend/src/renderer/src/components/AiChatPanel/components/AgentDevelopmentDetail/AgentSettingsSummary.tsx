import { PlusOutlined } from '@ant-design/icons'
import { Button, Checkbox, Collapse, Descriptions, Select, Tag, Tooltip, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import type { DevelopmentPlanningAgentOption } from '../../../../typings'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = Pick<DevelopmentPlanningAgentOption, 'agentSettings'>

/** 把未知配置值收敛为普通对象。 */
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 把未知数组收敛为对象数组。 */
function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item)
      )
    : []
}

/** 把未知数组收敛为可展示字符串。 */
function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item || '').trim()).filter(Boolean) : []
}

/** 使用只读复选框表达布尔配置，避免启用状态堆叠成大量彩色标签。 */
function readonlyToggle(
  enabled: boolean,
  enabledLabel = '已启用',
  disabledLabel = '未启用'
): ReactElement {
  return (
    <Checkbox checked={enabled} className={cx('agent-setting-checkbox')} disabled>
      {enabled ? enabledLabel : disabledLabel}
    </Checkbox>
  )
}

/** 渲染无内容时的一致只读占位。 */
function emptyText(label: string): ReactElement {
  return <Text type="secondary">{label}</Text>
}

/** 渲染暂未开放的扩展入口，并阻止按钮点击切换当前折叠面板。 */
function comingSoonAction(label: string): ReactElement {
  return (
    <Tooltip title={`${label}接入功能还在开发中`}>
      <Button
        aria-label={`添加${label}，功能开发中`}
        className={cx('agent-setting-add-button')}
        icon={<PlusOutlined />}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
        size="small"
        type="text"
      />
    </Tooltip>
  )
}

/** 把模型能力字段转换为稳定的用户可读名称。 */
function capabilityLabel(key: string): string {
  const labels: Record<string, string> = {
    streaming: '流式响应',
    toolCalling: '工具调用',
    structuredOutput: '结构化输出',
    vision: '视觉理解',
    observability: '可观测'
  }
  return labels[key] || key
}

/** 渲染一个带可选状态、来源和扩展动作的折叠面板标题。 */
function sectionTitle(
  label: string,
  options: { status?: ReactNode; source?: string; action?: ReactNode } = {}
): ReactElement {
  return (
    <span className={cx('agent-setting-title')}>
      <span className={cx('agent-setting-title-main')}>
        <strong>{label}</strong>
        {options.status}
      </span>
      <span className={cx('agent-setting-title-actions')}>
        {options.source ? <Tag>{options.source}</Tag> : null}
        {options.action}
      </span>
    </span>
  )
}

/** 以用户可读字段展示七段 Agent Settings，不暴露原始 JSON。 */
export default function AgentSettingsSummary({ agentSettings }: Props): ReactElement {
  const prompt = record(agentSettings.prompt)
  const persona = record(prompt.persona)
  const model = record(agentSettings.model)
  const capabilities = record(model.requiredCapabilities)
  const modelRef = String(model.modelRef || model.selection || 'project_default')
  // 可观测是默认必需能力，展示时固定置顶，其余能力保持 TechnicalPlan 原有顺序。
  const capabilityEntries = Object.entries(capabilities).sort(
    ([left], [right]) => Number(right === 'observability') - Number(left === 'observability')
  )
  const memory = record(agentSettings.memory)
  const shortTerm = record(memory.shortTerm)
  const longTerm = record(memory.longTerm)
  const archive = record(memory.archive)
  const tools = record(agentSettings.tools)
  const toolBindings = records(tools.bindings)
  const skills = record(agentSettings.skills)
  const skillBindings = records(skills.bindings)
  const knowledge = record(agentSettings.knowledge)
  const knowledgeSources = records(knowledge.sources)
  const retrieval = record(knowledge.retrieval)
  const context = record(agentSettings.context)
  const contextSources = records(context.sources)
  const budget = record(context.budget)
  const compression = record(context.compression)
  const promptConstraints = strings(prompt.constraints)

  return (
    <Collapse
      className={cx('agent-settings-collapse')}
      defaultActiveKey={['prompt', 'model']}
      ghost
    >
      <Collapse.Panel
        header={sectionTitle('人设与 System Prompt', { source: '用户配置' })}
        key="prompt"
      >
        <div className={cx('agent-prompt-summary')}>
          <div className={cx('agent-prompt-meta-grid')}>
            <article className={cx('agent-prompt-field')}>
              <span className={cx('agent-prompt-field-label')}>角色</span>
              <span className={cx('agent-prompt-field-value')}>
                {String(persona.role || '未设置')}
              </span>
            </article>
            <article className={cx('agent-prompt-field')}>
              <span className={cx('agent-prompt-field-label')}>表达风格</span>
              <span className={cx('agent-prompt-field-value')}>
                {String(persona.tone || '未设置')}
              </span>
            </article>
          </div>
          <article className={cx('agent-prompt-field', 'agent-prompt-field-wide')}>
            <span className={cx('agent-prompt-field-label')}>System Prompt</span>
            <p className={cx('agent-prompt-field-value', 'agent-prompt-text')}>
              {String(prompt.systemPrompt || '未设置')}
            </p>
          </article>
          <article className={cx('agent-prompt-field', 'agent-prompt-field-wide')}>
            <span className={cx('agent-prompt-field-label')}>业务约束</span>
            {promptConstraints.length ? (
              <ul className={cx('agent-prompt-constraint-list')}>
                {promptConstraints.map((item) => <li key={item}>{item}</li>)}
              </ul>
            ) : (
              <span className={cx('agent-prompt-field-value')}>暂无额外业务约束</span>
            )}
          </article>
          <article
            className={cx(
              'agent-prompt-field',
              'agent-prompt-field-wide',
              'agent-prompt-security'
            )}
          >
            <span className={cx('agent-prompt-field-label')}>平台安全规则</span>
            <Tag color="purple">锁定前缀 · 不可覆盖</Tag>
          </article>
        </div>
      </Collapse.Panel>

      <Collapse.Panel
        header={sectionTitle('模型配置', { source: '项目模型策略' })}
        key="model"
      >
        <Descriptions className={cx('agent-setting-descriptions')} column={2} size="small">
          <Descriptions.Item label="模型策略">
            <Select
              aria-label="Agent 模型策略"
              className={cx('agent-model-select')}
              disabled
              options={[
                {
                  label: modelRef === 'project_default' ? '跟随项目默认模型' : modelRef,
                  value: modelRef
                }
              ]}
              size="small"
              value={modelRef}
            />
          </Descriptions.Item>
          <Descriptions.Item label="能力要求" span={2}>
            <div className={cx('agent-setting-checkbox-grid')}>
              {capabilityEntries.map(([key, value]) => (
                <span key={key}>
                  {readonlyToggle(value === true, capabilityLabel(key), capabilityLabel(key))}
                </span>
              ))}
            </div>
          </Descriptions.Item>
          <Descriptions.Item label="追问策略" span={2}>
            由 ProductPlan Interaction 管理，不属于模型参数
          </Descriptions.Item>
        </Descriptions>
      </Collapse.Panel>

      <Collapse.Panel
        header={sectionTitle('记忆模块', {
          status: readonlyToggle(shortTerm.enabled === true)
        })}
        key="memory"
      >
        <div className={cx('agent-setting-card-grid')}>
          <article>
            <div className={cx('agent-setting-card-title')}>
              <strong>短期记忆</strong>
              {readonlyToggle(shortTerm.enabled === true)}
            </div>
            <span>存储：{String(shortTerm.store || '未配置')}</span>
            <small>范围：{String(shortTerm.scope || 'thread')}</small>
          </article>
          <article>
            <div className={cx('agent-setting-card-title')}>
              <strong>长期记忆</strong>
              {readonlyToggle(longTerm.enabled === true, '已启用', '未启用')}
            </div>
            <small>当前 Runtime 尚未接入长期记忆 Adapter</small>
          </article>
          <article>
            <div className={cx('agent-setting-card-title')}>
              <strong>会话归档</strong>
              {readonlyToggle(archive.enabled === true, '已启用', '未启用')}
            </div>
            <small>不保存凭据或物理连接信息</small>
          </article>
        </div>
      </Collapse.Panel>

      <Collapse.Panel
        header={sectionTitle('工具配置', {
          status: readonlyToggle(tools.enabled === true)
        })}
        key="tools"
      >
        <div className={cx('agent-setting-list')}>
          {toolBindings.length
            ? toolBindings.map((binding) => {
                const endpoint = record(binding.endpoint)
                return (
                  <article key={String(binding.toolId || endpoint.endpointId)}>
                    <div>
                      <strong>{String(binding.name || binding.toolId || '未命名工具')}</strong>
                      <Tag color={binding.accessMode === 'write' ? 'orange' : 'purple'}>
                        {binding.accessMode === 'write' ? '写操作 · 平台审批' : '只读'}
                      </Tag>
                    </div>
                    <p>{String(binding.description || '暂无使用说明')}</p>
                    <code>{String(endpoint.endpointId || '未绑定 Endpoint')}</code>
                  </article>
                )
              })
            : emptyText('当前 Agent 不使用工具')}
        </div>
      </Collapse.Panel>

      <Collapse.Panel
        header={sectionTitle('Skills', {
          status: readonlyToggle(skills.enabled === true, '已启用', '未启用'),
          action: comingSoonAction('Skill 市场')
        })}
        key="skills"
      >
        {skillBindings.length
          ? skillBindings.map((binding) => (
              <Tag key={String(binding.skillId)}>{String(binding.skillId)}</Tag>
            ))
          : emptyText('当前 Runtime 尚未启用 Skill Loader')}
      </Collapse.Panel>

      <Collapse.Panel
        header={sectionTitle('知识库配置', {
          status: readonlyToggle(knowledge.enabled === true, '已启用', '未启用'),
          action: comingSoonAction('知识库')
        })}
        key="knowledge"
      >
        <Descriptions className={cx('agent-setting-descriptions')} column={2} size="small">
          <Descriptions.Item label="知识源">
            {knowledgeSources.length ? `${knowledgeSources.length} 个` : '当前版本未启用'}
          </Descriptions.Item>
          <Descriptions.Item label="检索策略">
            {String(retrieval.strategy || 'semantic')} · Top {String(retrieval.topK ?? 5)}
          </Descriptions.Item>
          <Descriptions.Item label="引用策略" span={2}>
            {String(knowledge.citationPolicy || 'disabled')}
          </Descriptions.Item>
        </Descriptions>
      </Collapse.Panel>

      <Collapse.Panel
        header={sectionTitle('上下文配置', { source: '平台策略' })}
        key="context"
      >
        <Descriptions className={cx('agent-setting-descriptions')} column={2} size="small">
          <Descriptions.Item label="上下文来源" span={2}>
            <div className={cx('agent-setting-checkbox-grid')}>
              {contextSources.map((source) => (
                <span key={String(source.type)}>
                  {readonlyToggle(
                    source.enabled === true,
                    String(source.type),
                    String(source.type)
                  )}
                </span>
              ))}
            </div>
          </Descriptions.Item>
          <Descriptions.Item label="输入预算">
            {Math.round(Number(budget.maxInputRatio || 0) * 100)}%
          </Descriptions.Item>
          <Descriptions.Item label="输出预留">
            {Math.round(Number(budget.reserveOutputRatio || 0) * 100)}%
          </Descriptions.Item>
          <Descriptions.Item label="压缩策略" span={2}>
            {String(compression.strategy || 'none') === 'none'
              ? '当前版本不启用摘要压缩'
              : String(compression.strategy)}
          </Descriptions.Item>
        </Descriptions>
      </Collapse.Panel>
    </Collapse>
  )
}
