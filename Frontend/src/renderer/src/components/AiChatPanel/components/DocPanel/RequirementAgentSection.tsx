import { cx } from '../../../../utils'
import '../AgentDevelopmentDetail/AgentDevelopmentDetail.less'
import { Alert, Card, Descriptions, Space, Switch, Tag } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import { saveAgentSurfaceSelection } from '../../../../service/agentSurfaceSelection'
import { recordItems, textValue, asRecord, type JsonRecord } from './TechnicalPlanDocPanelData'

type Props = {
  productPlan: JsonRecord
  spec: JsonRecord
  workspaceRoot?: string
  onChanged?: () => void
}

/** 展示已规划的业务智能体及候选入口，只允许修改待联合确认草稿中的浮窗开关。 */
export default function RequirementAgentSection({
  productPlan,
  spec,
  workspaceRoot,
  onChanged
}: Props): ReactElement {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const agents = recordItems(productPlan.agents)
  const requirements = recordItems(spec.agent_requirements)
  const editable =
    productPlan.confirmation_status === 'pending_user_confirmation' && Boolean(workspaceRoot)

  /** 保存候选浮窗启停后刷新正式草稿，失败时保持原展示值。 */
  const toggle = async (agentId: string, pageId: string, enabled: boolean): Promise<void> => {
    if (!workspaceRoot || busy || !editable) return
    setBusy(true)
    setError('')
    try {
      await saveAgentSurfaceSelection(workspaceRoot, agentId, pageId, enabled, productPlan)
      onChanged?.()
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      className={cx('agent-review-section')}
      role="tabpanel"
      id="requirement-doc-panel-agents"
      aria-labelledby="requirement-doc-tab-agents"
    >
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {(agents.length ? agents : requirements).map((agent) => (
        <Card key={textValue(agent.agentId)} title={textValue(agent.name)}>
          <Descriptions column={1}>
            <Descriptions.Item label="职责">{textValue(agent.purpose)}</Descriptions.Item>
            <Descriptions.Item label="业务能力">
              {Array.isArray(agent.capabilities)
                ? agent.capabilities
                    .map((item) =>
                      typeof item === 'string' ? item : textValue(asRecord(item).name)
                    )
                    .join('；')
                : ''}
            </Descriptions.Item>
            <Descriptions.Item label="业务边界">
              {Array.isArray(agent.boundaries) ? agent.boundaries.join('；') : ''}
            </Descriptions.Item>
          </Descriptions>
          {recordItems(agent.pageActionBindings).map((binding) => {
            const surface = asRecord(binding.surface)
            return (
              <Space key={textValue(binding.pageId)} style={{ display: 'flex', marginBottom: 8 }}>
                <Tag color="purple">{textValue(binding.pageId)}</Tag>
                <span>{surface.type === 'standalone_page' ? '独立对话页面' : '智能体浮窗'}</span>
                {surface.type === 'floating_panel' ? (
                  <Switch
                    checked={surface.enabled === true}
                    disabled={!editable || busy}
                    aria-label={`${textValue(binding.pageId)}集成智能体浮窗`}
                    onChange={(enabled) =>
                      void toggle(textValue(agent.agentId), textValue(binding.pageId), enabled)
                    }
                  />
                ) : null}
              </Space>
            )
          })}
        </Card>
      ))}
    </section>
  )
}
