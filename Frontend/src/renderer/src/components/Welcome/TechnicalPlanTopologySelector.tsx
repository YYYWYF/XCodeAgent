import { Radio, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import './TechnicalPlanTopologySelector.less'

const { Text } = Typography

type TopologyOption = {
  description: string
  disabled: boolean
  label: string
  value: 'agent_runtime_direct' | 'backend_direct' | 'gateway_composed'
}

const TOPOLOGY_OPTIONS: TopologyOption[] = [
  {
    description: 'Frontend 直接连接 Python Application Runtime；业务 API、Entity 与 Agent 同服务，不生成 Java Backend 或 Gateway。',
    disabled: false,
    label: 'Agent Runtime 直连',
    value: 'agent_runtime_direct'
  },
  {
    description: 'Frontend 直接连接 Backend，不生成 Agent Runtime 或 Gateway。',
    disabled: true,
    label: 'Backend 直连',
    value: 'backend_direct'
  },
  {
    description: 'Frontend 通过 Gateway 统一连接 Backend 与 Agent Runtime。',
    disabled: true,
    label: 'Gateway 组合',
    value: 'gateway_composed'
  }
]

type Props = {
  compact?: boolean
  disabled?: boolean
  onChange: (value: 'agent_runtime_direct') => void
  value?: 'agent_runtime_direct'
}

/** 展示 TechnicalPlan 的拓扑选择；当前仅开放已实现的 Agent Runtime 直连拓扑。 */
export default function TechnicalPlanTopologySelector({
  compact = false,
  disabled = false,
  onChange,
  value
}: Props): ReactElement {
  return (
    <section
      aria-labelledby="technical-plan-topology-title"
      className={cx('technical-plan-topology-selector', compact && 'is-compact')}
    >
      <header className={cx('technical-plan-topology-header')}>
        <div>
          <Text id="technical-plan-topology-title" strong>
            选择应用拓扑
          </Text>
          <Text type="secondary">当前仅 Agent Runtime 直连可用，其他拓扑将在实现后开放。</Text>
        </div>
        <Tag className={cx('technical-plan-topology-stage-tag')}>计划阶段</Tag>
      </header>

      <Radio.Group
        aria-label="应用拓扑"
        className={cx('technical-plan-topology-options')}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as 'agent_runtime_direct')}
        value={value}
      >
        {TOPOLOGY_OPTIONS.map((option) => (
          <Radio
            className={cx(
              'technical-plan-topology-option',
              option.disabled && 'is-unavailable'
            )}
            disabled={disabled || option.disabled}
            key={option.value}
            value={option.value}
          >
            <span className={cx('technical-plan-topology-option-content')}>
              <span className={cx('technical-plan-topology-option-heading')}>
                <strong>{option.label}</strong>
                <code>{option.value}</code>
                <Tag>{option.disabled ? '尚未实现' : '当前可用'}</Tag>
              </span>
              <span className={cx('technical-plan-topology-option-description')}>
                {option.description}
              </span>
            </span>
          </Radio>
        ))}
      </Radio.Group>
    </section>
  )
}
