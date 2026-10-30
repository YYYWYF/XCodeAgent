import { Checkbox, Collapse, Descriptions, Tag, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import type { RuntimeAgent } from '../../../../service/runtimeWorkbench'

/** 用只读勾选状态复用原 Agent 配置卡片的展示方式。 */
function status(enabled: boolean): ReactElement {
  return <Checkbox disabled checked={enabled}>{enabled ? '已启用' : '未启用'}</Checkbox>
}

/** 在模块标题右侧展示配置来源或启用状态。 */
function title(label: string, detail: ReactNode): ReactElement {
  return <span className="agent-setting-title"><strong>{label}</strong><span>{detail}</span></span>
}

/** 展示当前合同的精确资源文件，不推测旧版本的资源绑定。 */
function ResourceFiles({ config, empty }: { config: Record<string, unknown>; empty: string }): ReactElement {
  const files = Array.isArray(config.files) ? config.files : []
  return files.length ? <div className="agent-setting-list">{files.map((file) => <article key={String(file)}><code>{String(file)}</code></article>)}</div>
    : <Typography.Text type="secondary">{empty}</Typography.Text>
}

/** 按原来的七模块卡片布局展示当前正式设置，不恢复历史配置字段。 */
export default function AgentSettingsSummary({ agent }: { agent: RuntimeAgent }): ReactElement {
  const { prompt, model, tools, skills, knowledge } = agent.agentSettings
  const persona = prompt.persona as { role?: string; tone?: string } | undefined
  const generation = model.generation as { temperature?: number } | undefined
  const bindings = Array.isArray(tools.bindings) ? tools.bindings as Record<string, unknown>[] : []
  return <Collapse className="agent-settings-collapse" ghost defaultActiveKey={['prompt', 'model']}>
    <Collapse.Panel key="prompt" header={title('人设与 System Prompt', <Tag>技术规划</Tag>)}>
      <div className="agent-prompt-summary">
        <div className="agent-prompt-meta-grid">
          <article className="agent-prompt-field"><span className="agent-field-label">角色</span><p>{persona?.role || '未设置'}</p></article>
          <article className="agent-prompt-field"><span className="agent-field-label">表达风格</span><p>{persona?.tone || '未设置'}</p></article>
        </div>
        <article className="agent-prompt-field">
          <span className="agent-field-label">System Prompt</span>
          <p>{String(prompt.systemPrompt || '未设置')}</p>
        </article>
        {Array.isArray(prompt.constraints) && prompt.constraints.length > 0 && <article className="agent-prompt-field">
          <span className="agent-field-label">业务约束</span><ul>{prompt.constraints.map((line, index) => <li key={index}>{String(line)}</li>)}</ul>
        </article>}
      </div>
    </Collapse.Panel>
    <Collapse.Panel key="model" header={title('模型配置', <Tag>项目模型策略</Tag>)}>
      <Descriptions column={1} layout="vertical" size="small">
        <Descriptions.Item label="模型策略"><span className="agent-model-policy">跟随当前项目模型</span></Descriptions.Item>
        <Descriptions.Item label="配置方式">启动时注入模型配置与凭据</Descriptions.Item>
        <Descriptions.Item label="Temperature">{generation?.temperature ?? '跟随项目默认'}</Descriptions.Item>
      </Descriptions>
    </Collapse.Panel>
    <Collapse.Panel key="memory" header={title('记忆模块', <Tag>模板默认</Tag>)}>
      <div className="agent-setting-card-grid"><article><strong>会话记忆</strong><Typography.Text type="secondary">沿用当前 Runtime 模板实现</Typography.Text></article></div>
    </Collapse.Panel>
    <Collapse.Panel key="tools" header={title('工具配置', status(tools.enabled === true))}>
      <div className="agent-setting-list">{bindings.length ? bindings.map((binding) => <article key={String(binding.toolId)}>
        <strong>{String(binding.toolId)}</strong><Descriptions column={1} size="small">
          <Descriptions.Item label="接口分组">{String(binding.apiContractId)}</Descriptions.Item>
          <Descriptions.Item label="接口">{String(binding.endpointId)}</Descriptions.Item>
        </Descriptions>
      </article>) : <Typography.Text type="secondary">当前 Agent 不使用工具</Typography.Text>}</div>
    </Collapse.Panel>
    <Collapse.Panel key="skills" header={title('Skills', status(skills.enabled === true))}>
      <ResourceFiles config={skills} empty="当前 Agent 未启用技能" />
    </Collapse.Panel>
    <Collapse.Panel key="knowledge" header={title('知识库配置', status(knowledge.enabled === true))}>
      <ResourceFiles config={knowledge} empty="当前 Agent 未启用知识资源" />
    </Collapse.Panel>
    <Collapse.Panel key="context" header={title('上下文配置', <Tag>平台策略</Tag>)}>
      <Typography.Text type="secondary">沿用当前 Runtime 模板的上下文策略</Typography.Text>
    </Collapse.Panel>
  </Collapse>
}
