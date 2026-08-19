import { PlusOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Tag, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import type { AgentConfigResource } from './types'

const { Text } = Typography

export type ConfigSectionProps = {
  addLabel?: string
  count?: number
  expanded: boolean
  onAdd?: () => void
  onToggle: () => void
  title: string
  trailing?: ReactNode
  children?: ReactNode
}

/** 渲染配置区统一的折叠标题行和右侧操作。 */
export function ConfigSection({
  addLabel,
  children,
  count,
  expanded,
  onAdd,
  onToggle,
  title,
  trailing
}: ConfigSectionProps): ReactElement {
  return (
    <section className={cx('agent-config-section', expanded && 'expanded')}>
      <div className={cx('agent-config-section-row')}>
        <button
          aria-expanded={expanded}
          className={cx('agent-config-section-trigger')}
          onClick={onToggle}
          type="button"
        >
          <RightOutlined className={cx('agent-config-section-caret', expanded && 'expanded')} />
          <span className={cx('agent-config-section-title')}>{title}</span>
          {typeof count === 'number' ? (
            <span className={cx('agent-config-section-count')}>({count})</span>
          ) : null}
        </button>
        {trailing}
        {onAdd ? (
          <Button
            aria-label={`添加${addLabel || title}`}
            className={cx('agent-config-section-add')}
            icon={<PlusOutlined />}
            onClick={onAdd}
            title={`添加${addLabel || title}`}
            type="text"
          />
        ) : null}
      </div>
      {expanded && children ? (
        <div className={cx('agent-config-section-body')}>{children}</div>
      ) : null}
    </section>
  )
}

type SelectedResourcesProps = {
  readOnly?: boolean
  resources: AgentConfigResource[]
  onRemove: (resourceId: string) => void
}

/** 展示已加入当前配置的资源标签，并允许从配置中移除。 */
export function SelectedResources({
  readOnly = false,
  resources,
  onRemove
}: SelectedResourcesProps): ReactElement {
  if (resources.length === 0) {
    return (
      <Text className={cx('agent-config-empty-hint')} type="secondary">
        暂无已添加内容
      </Text>
    )
  }
  return (
    <div className={cx('agent-config-resource-tags')}>
      {resources.map((resource) => (
        <Tag
          closable={!readOnly}
          key={resource.id}
          onClose={() => onRemove(resource.id)}
        >
          {resource.name}
        </Tag>
      ))}
    </div>
  )
}
