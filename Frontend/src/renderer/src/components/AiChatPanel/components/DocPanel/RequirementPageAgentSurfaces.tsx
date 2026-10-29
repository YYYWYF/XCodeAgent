import { Switch } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { asRecord, recordItems, textValue, type JsonRecord } from './RequirementDocPanelData'
import './RequirementAgents.less'

export type ToggleAgentSurface = (
  agentId: string,
  pageId: string,
  enabled: boolean
) => void | Promise<void>

type Props = {
  productPlan: JsonRecord
  pageId: string
  pageName: string
  busy?: boolean
  onToggle?: ToggleAgentSurface
}

/** 在对应页面卡片展示智能体载体，只有浮窗绑定可通过原草稿动作启停。 */
export default function RequirementPageAgentSurfaces({
  productPlan,
  pageId,
  pageName,
  busy = false,
  onToggle
}: Props): ReactElement | null {
  const bindings = recordItems(productPlan.agents).flatMap((agent) =>
    recordItems(agent.pageActionBindings)
      .filter((binding) => binding.pageId === pageId)
      .map((binding) => ({
        agentId: textValue(agent.agentId),
        agentName: textValue(agent.name, '未命名智能体'),
        surface: asRecord(binding.surface)
      }))
  )
  if (!bindings.length) return null

  return (
    <section className={cx('requirement-doc-page-zone', 'requirement-page-agent-surfaces')}>
      <span className={cx('requirement-doc-page-zone-title')}>智能体入口</span>
      {bindings.map(({ agentId, agentName, surface }) => (
        <div className={cx('requirement-agent-surface-row')} key={agentId}>
          <div className={cx('requirement-agent-surface-copy')}>
            <strong>{agentName}</strong>
            <span>{surface.type === 'standalone_page' ? '独立对话页面' : '集成智能体浮窗'}</span>
          </div>
          {surface.type === 'floating_panel' ? (
            <Switch
              size="small"
              checked={surface.enabled === true}
              disabled={!onToggle || busy}
              loading={busy}
              aria-label={`${pageName}集成${agentName}浮窗`}
              onChange={(enabled) => void onToggle?.(agentId, pageId, enabled)}
            />
          ) : null}
        </div>
      ))}
    </section>
  )
}
