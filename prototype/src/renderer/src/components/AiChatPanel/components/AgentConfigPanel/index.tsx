import {
  FileSearchOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Button, Segmented, Select, Space, Tooltip, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import {
  areAgentConfigsEqual,
  type AgentConfigRevisionStatus,
  type AgentConfigState
} from '../../../../agentConfig'
import type { DevelopmentPlanningAgent } from '../../../../agentDevelopment'
import { cx } from '../../../../utils'
import AgentResourcePickerModal from './AgentResourcePickerModal'
import { ConfigSection, SelectedResources } from './ConfigSection'
import ConversationExperience from './ConversationExperience'
import AgentPersonaReplyLogic from './AgentPersonaReplyLogic'
import {
  AGENT_CONFIG_MODEL,
  AGENT_CONFIG_RESOURCE_KIND_LABELS,
  getAgentConfigCatalog
} from './catalog'
import ModelConfigFields from './ModelConfigFields'
import type { AgentConfigResource, AgentConfigResourceKind } from './types'
import './AgentConfigPanel.less'

const { Text } = Typography

type Props = {
  agent: DevelopmentPlanningAgent
  hidden?: boolean
  readOnly?: boolean
  readOnlyMessage?: string
  config: AgentConfigState
  activeConfig: AgentConfigState
  status: AgentConfigRevisionStatus
  onChange: (config: AgentConfigState) => void
  onApply: () => void
  onReset: () => void
}

type ConfigSectionKey = 'model' | AgentConfigResourceKind | 'conversation'

const RESOURCE_SECTIONS: Array<{
  kind: AgentConfigResourceKind
  title: string
  icon: ReactElement
}> = [
  { kind: 'skills', title: '技能', icon: <RobotOutlined /> },
  { kind: 'knowledge', title: '知识检索', icon: <FileSearchOutlined /> },
  { kind: 'tools', title: '工具', icon: <ToolOutlined /> }
]

/** 将配置状态转换为面板顶部和底部可读的应用状态文案。 */
function configStatusLabel(status: AgentConfigRevisionStatus, dirty: boolean): string {
  if (status === 'pending_generation') return '待重新生成'
  if (status === 'generating') return '正在重新生成'
  if (status === 'awaiting_acceptance') return '等待验收'
  if (status === 'error') return '生成失败，可重试'
  if (dirty || status === 'draft') return '未应用配置'
  return '配置已应用'
}

/** 渲染智能体配置预览和修改面板；草稿由智能体会话级状态统一管理。 */
export default function AgentConfigPanel({
  activeConfig,
  agent,
  config,
  hidden,
  readOnly = false,
  readOnlyMessage = '当前查看的是已生成版本，内容只读；如需调整，请先发起新迭代或回退。',
  onApply,
  onChange,
  onReset,
  status
}: Props): ReactElement {
  const [mode, setMode] = useState<'simple' | 'advanced'>('simple')
  const [expandedSections, setExpandedSections] = useState<Set<ConfigSectionKey>>(new Set())
  const [resourcePickerKind, setResourcePickerKind] = useState<AgentConfigResourceKind>()
  const dirty = !areAgentConfigsEqual(activeConfig, config)
  const resourceCatalogs = useMemo(
    () => ({
      skills: getAgentConfigCatalog('skills', agent),
      knowledge: getAgentConfigCatalog('knowledge', agent),
      tools: getAgentConfigCatalog('tools', agent)
    }),
    [agent]
  )

  /** 切换智能体时恢复独立配置草稿，避免不同智能体相互污染。 */
  useEffect(() => {
    setMode('simple')
    setExpandedSections(new Set())
    setResourcePickerKind(undefined)
  }, [agent.id, readOnly])

  /** 展开或收起一个配置模块。 */
  const toggleSection = (section: ConfigSectionKey): void => {
    setExpandedSections((current) => {
      const next = new Set(current)
      if (next.has(section)) next.delete(section)
      else next.add(section)
      return next
    })
  }

  /** 向指定资源模块追加一项，重复点击不会产生重复配置。 */
  const addResource = (kind: AgentConfigResourceKind, resource: AgentConfigResource): void => {
    if (readOnly) return
    if (config[kind].some((item) => item.id === resource.id)) return
    onChange({ ...config, [kind]: [...config[kind], resource] })
  }

  /** 从指定资源模块移除一项。 */
  const removeResource = (kind: AgentConfigResourceKind, resourceId: string): void => {
    if (readOnly) return
    onChange({
      ...config,
      [kind]: config[kind].filter((resource) => resource.id !== resourceId)
    })
  }

  /** 更新模型选择并把修改交给会话级配置存储。 */
  const updateModel = (model: string): void => {
    if (readOnly) return
    onChange({ ...config, model: { ...config.model, model } })
  }

  /** 更新模型展开区参数并保留同一份配置草稿。 */
  const updateModelSettings = (model: AgentConfigState['model']): void => {
    if (readOnly) return
    onChange({ ...config, model })
  }

  /** 更新对话体验开关并保留模型和资源选择。 */
  const updateConversation = (conversation: AgentConfigState['conversation']): void => {
    if (readOnly) return
    onChange({ ...config, conversation })
  }

  /** 更新人设与回复逻辑草稿，保留其他配置模块的当前值。 */
  const updatePersonaReplyLogic = (personaReplyLogic: string): void => {
    if (readOnly) return
    onChange({ ...config, personaReplyLogic })
  }

  const revisionInProgress = ['pending_generation', 'generating', 'awaiting_acceptance'].includes(
    status
  )

  return (
    <section aria-label={`${agent.label}配置`} className={cx('agent-config')} hidden={hidden}>
      <header className={cx('agent-config-header')}>
        <div className={cx('agent-config-title')}>
          <span aria-hidden="true" className={cx('agent-config-title-marker')} />
          <Text strong>配置</Text>
        </div>
        <div className={cx('agent-config-header-actions')}>
          <span className={cx('agent-config-status', dirty && 'dirty')}>
            {configStatusLabel(status, dirty)}
          </span>
          <Segmented
            aria-label="配置模式"
            className={cx('agent-config-mode')}
            onChange={(value) => setMode(value as 'simple' | 'advanced')}
            options={[
              { label: '简单', value: 'simple' },
              { label: '高级', value: 'advanced' }
            ]}
            value={mode}
          />
        </div>
      </header>

      <div className={cx('agent-config-scroll')}>
        <AgentPersonaReplyLogic
          agent={agent}
          onChange={updatePersonaReplyLogic}
          readOnly={readOnly}
          value={config.personaReplyLogic}
        />

        <ConfigSection
          expanded={expandedSections.has('model')}
          onToggle={() => toggleSection('model')}
          title="模型"
          trailing={
            <Select
              aria-label="选择模型"
              className={cx('agent-config-model-select')}
              disabled={readOnly}
              onChange={updateModel}
              options={[{ label: AGENT_CONFIG_MODEL, value: AGENT_CONFIG_MODEL }]}
              value={config.model.model}
            />
          }
        >
          <ModelConfigFields
            onChange={updateModelSettings}
            readOnly={readOnly}
            settings={config.model}
          />
        </ConfigSection>

        {RESOURCE_SECTIONS.map(({ icon, kind, title }) => (
          <ConfigSection
            addLabel={AGENT_CONFIG_RESOURCE_KIND_LABELS[kind]}
            count={config[kind].length}
            expanded={expandedSections.has(kind)}
            key={kind}
            onAdd={readOnly ? undefined : () => setResourcePickerKind(kind)}
            onToggle={() => toggleSection(kind)}
            title={title}
          >
            <div className={cx('agent-config-resource-heading')}>
              <span className={cx('agent-config-resource-icon')} aria-hidden="true">
                {icon}
              </span>
              <Text type="secondary">
                可添加已确认的{AGENT_CONFIG_RESOURCE_KIND_LABELS[kind]}，并在当前配置中预览。
              </Text>
              <Tooltip title={`${AGENT_CONFIG_RESOURCE_KIND_LABELS[kind]}说明`}>
                <QuestionCircleOutlined
                  aria-label={`${AGENT_CONFIG_RESOURCE_KIND_LABELS[kind]}说明`}
                />
              </Tooltip>
            </div>
            <SelectedResources
              onRemove={(resourceId) => removeResource(kind, resourceId)}
              readOnly={readOnly}
              resources={config[kind]}
            />
          </ConfigSection>
        ))}

        <ConfigSection
          expanded={expandedSections.has('conversation')}
          onToggle={() => toggleSection('conversation')}
          title="对话体验"
        >
          <ConversationExperience
            onChange={updateConversation}
            readOnly={readOnly}
            settings={config.conversation}
          />
        </ConfigSection>
      </div>

      {readOnly ? (
        <footer className={cx('agent-config-footer', 'read-only')}>
          <Text className={cx('agent-config-footer-hint')} type="secondary">
            {readOnlyMessage}
          </Text>
        </footer>
      ) : dirty || status !== 'clean' ? (
        <footer className={cx('agent-config-footer')}>
          <Text className={cx('agent-config-footer-hint')} type="secondary">
            配置修改需要重新生成智能体代码，确认后才会影响左侧对话和试运行。
          </Text>
          <Space size={8}>
            <Button onClick={onReset}>撤销修改</Button>
            <Button
              disabled={!dirty || revisionInProgress}
              loading={status === 'generating'}
              onClick={onApply}
              type="primary"
            >
              应用配置并重新生成
            </Button>
          </Space>
        </footer>
      ) : null}

      {resourcePickerKind ? (
        <AgentResourcePickerModal
          kind={resourcePickerKind}
          onAdd={(resource) => addResource(resourcePickerKind, resource)}
          onClose={() => setResourcePickerKind(undefined)}
          open
          resources={resourceCatalogs[resourcePickerKind]}
          selectedIds={config[resourcePickerKind].map((resource) => resource.id)}
        />
      ) : null}
    </section>
  )
}
