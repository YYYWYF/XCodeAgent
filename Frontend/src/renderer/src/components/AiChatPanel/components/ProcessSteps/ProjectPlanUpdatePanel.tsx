import {
  ApiOutlined,
  CheckCircleOutlined,
  FileMarkdownOutlined,
  LockOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowProjectPlanUpdate } from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import './ProjectPlanUpdatePanel.less'

const { Text } = Typography

type Props = {
  update: WorkflowProjectPlanUpdate
}

/** 展示页面细节确认后生成的只读项目计划更新章节。 */
export default function ProjectPlanUpdatePanel({ update }: Props): ReactElement {
  const targetLabel = update.targetType === 'endpoint' ? '接口目标' : '页面目标'

  return (
    <section aria-label="项目计划书本次更新" className={cx('project-plan-update')}>
      <div className={cx('project-plan-update-header')}>
        <div className={cx('project-plan-update-identity')}>
          <span className={cx('project-plan-update-mark')} aria-hidden="true">
            <FileMarkdownOutlined />
          </span>
          <span className={cx('project-plan-update-heading')}>
            <Text className={cx('project-plan-update-eyebrow')}>PROJECT PLAN UPDATE</Text>
            <Text className={cx('project-plan-update-title')} strong>
              项目计划书本次更新
            </Text>
            <Text className={cx('project-plan-update-document')} type="secondary">
              {update.documentName}
            </Text>
          </span>
        </div>
        <div className={cx('project-plan-update-badges')}>
          <span>
            <LockOutlined /> 只读
          </span>
          <span className={cx('confirmed')}>
            <CheckCircleOutlined /> 已确认
          </span>
        </div>
      </div>

      <div className={cx('project-plan-update-meta')}>
        <span className={cx('project-plan-update-target')}>
          <small>{targetLabel}</small>
          <strong>{update.targetId}</strong>
        </span>
        <span className={cx('project-plan-update-metric')}>
          <strong>{update.summary.pageCount}</strong>
          <small>PAGE</small>
        </span>
        <span className={cx('project-plan-update-metric')}>
          <strong>{update.summary.endpointCount}</strong>
          <small>API</small>
        </span>
      </div>

      <div
        aria-label="项目计划书更新正文"
        className={cx('project-plan-update-content')}
        tabIndex={0}
      >
        {update.sections.map((section, index) => (
          <article className={cx('project-plan-update-section')} key={section.id}>
            <div className={cx('project-plan-update-section-header')}>
              <span className={cx('project-plan-update-section-index')}>
                {String(index + 1).padStart(2, '0')}
              </span>
              <span className={cx('project-plan-update-section-kind')}>
                {section.kind === 'endpoint' ? <ApiOutlined /> : <FileMarkdownOutlined />}
                {section.kind === 'endpoint' ? 'API' : 'PAGE'}
              </span>
              <span className={cx('project-plan-update-section-name')}>
                <Text strong>{section.title}</Text>
                {section.subtitle && <Text type="secondary">{section.subtitle}</Text>}
              </span>
            </div>
            <MarkdownContent
              className={cx('project-plan-update-markdown')}
              content={section.content}
            />
          </article>
        ))}
      </div>
    </section>
  )
}
