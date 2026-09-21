import { ApiOutlined, DatabaseOutlined, FileTextOutlined } from '@ant-design/icons'
import { Button, Tag, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import type { DevelopmentArtifactProgress } from '../../../../typings'
import { cx } from '../../../../utils'
import { developmentStatusText } from '../../../../developmentArtifacts'
import './DevelopmentTargetDetail.less'

const { Text } = Typography

type Props = {
  description?: string
  disabled?: boolean
  extra?: ReactNode
  kind: 'page' | 'endpoint' | 'entity'
  progress?: DevelopmentArtifactProgress
  subtitle?: string
  title: string
  onStart: () => void
}

/** 说明开发阶段选中产物仍需单独执行，避免被误当成产品设计未完成。 */
function developmentReadyHint(kind: Props['kind']): string {
  if (kind === 'endpoint') {
    return '产品和技术规划已完成。开发此接口时会先检查字段映射；尚未配置映射时会打开映射门禁，而不是退回产品设计。'
  }
  if (kind === 'entity') {
    return '产品和技术规划已完成。实体需要单独绑定数据源后，才计入初次开发完成。'
  }
  return '产品和技术规划已完成。每个页面仍需作为独立开发目标执行一次，不会自动随其他产物完成。'
}

/** 为开发产物详情提供开始开发按钮文案。 */
function startActionLabel(
  kind: Props['kind'],
  progress?: DevelopmentArtifactProgress
): string {
  if (progress?.initialDevelopmentStatus === 'completed') return '已初次完成'
  if (progress?.initialDevelopmentStatus === 'in_progress') return '继续开发'
  return kind === 'entity' ? '开始绑定数据源' : '开始开发'
}

/** 渲染开发阶段页面、接口或实体的状态详情，并提供正式开发入口。 */
export default function DevelopmentTargetDetail({
  description,
  disabled,
  extra,
  kind,
  progress,
  subtitle,
  title,
  onStart
}: Props): ReactElement {
  const kindLabel = kind === 'page' ? '页面' : kind === 'entity' ? '实体' : '接口'
  const Icon = kind === 'page' ? FileTextOutlined : kind === 'entity' ? DatabaseOutlined : ApiOutlined
  const completed = progress?.initialDevelopmentStatus === 'completed'
  return (
    <div className={cx('development-target-detail')}>
      <header className={cx('development-target-hero')}>
        <span className={cx('development-target-avatar')} aria-hidden="true">
          <Icon />
        </span>
        <div>
          <Text type="secondary">{kindLabel}</Text>
          <h2>{title}</h2>
          {subtitle ? <code>{subtitle}</code> : null}
        </div>
        <div className={cx('development-target-hero-actions')}>
          <Tag>{developmentStatusText(progress)}</Tag>
          <Button disabled={disabled || completed} onClick={onStart} type="primary">
            {startActionLabel(kind, progress)}
          </Button>
        </div>
      </header>
      <section className={cx('development-target-section')}>
        <h3>开发说明</h3>
        {description ? <p>{description}</p> : null}
        <p>{developmentReadyHint(kind)}</p>
      </section>
      {extra}
    </div>
  )
}
