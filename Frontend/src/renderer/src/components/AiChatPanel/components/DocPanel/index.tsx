import { FileTextOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import RichLoading from '../DesignProgress/RichLoading'
import TechnicalPlanDocPanel from './TechnicalPlanDocPanel'
import './index.less'

const { Text } = Typography

/** 将 TechnicalPlan 的确认状态转换为顶部工具栏中的可读标签。 */
function technicalPlanStatusLabel(plan?: Record<string, unknown>): string {
  const status = plan?.confirmation_status
  return status === 'confirmed' ? '已确认' : status === 'pending_user_confirmation' ? '待确认' : '草稿'
}

type Props = {
  content?: string
  title?: string
  generating?: boolean
  docName?: string
  productPlan?: Record<string, unknown>
  technicalPlan?: Record<string, unknown>
  structuredDocument?: 'technical-plan'
  structuredDocumentLoading?: boolean
}

/** 右侧产物面板：普通文档只读展示 Markdown，TechnicalPlan 使用结构化审核视图。 */
export default function DocPanel({
  content,
  title,
  generating,
  docName,
  productPlan,
  technicalPlan,
  structuredDocument,
  structuredDocumentLoading
}: Props): ReactElement {
  const isTechnicalPlan = structuredDocument === 'technical-plan'
  const ready = Boolean(content || technicalPlan || isTechnicalPlan)

  return (
    <div className={cx('doc-panel')}>
      <header className={cx('doc-panel-toolbar')}>
        {isTechnicalPlan ? (
          <div className={cx('doc-panel-path', 'doc-panel-technical-path')}>
            <FileTextOutlined aria-hidden="true" />
            <span>
              <strong>技术规划</strong>
              <small>{title?.split('/').pop() || 'technical-plan.json'}</small>
            </span>
            {technicalPlan ? (
              <Tag className={cx('doc-panel-technical-status')}>
                {technicalPlanStatusLabel(technicalPlan)}
              </Tag>
            ) : null}
          </div>
        ) : (
          <div className={cx('doc-panel-path')}>{title || '文档'}</div>
        )}
      </header>
      <div className={cx('doc-panel-stage', ready && 'editor')}>
        {generating ? (
          <div className={cx('doc-panel-generating')}>
            <RichLoading bare title={`正在生成${docName || '文档'}…`} />
          </div>
        ) : ready ? (
          <div
            className={cx(
              'doc-panel-viewer',
              isTechnicalPlan ? 'doc-panel-structured' : 'doc-panel-markdown'
            )}
          >
            {structuredDocumentLoading ? (
              <div className={cx('doc-panel-generating')}>
                <RichLoading bare title="正在读取技术规划…" />
              </div>
            ) : isTechnicalPlan ? (
              technicalPlan && productPlan ? (
                <TechnicalPlanDocPanel plan={technicalPlan} productPlan={productPlan} />
              ) : (
                <div className={cx('doc-panel-empty')}>
                  <Text strong>技术规划可视化暂不可用</Text>
                  <Text type="secondary">未读取到 ProductPlan 或 TechnicalPlan 结构化数据</Text>
                </div>
              )
            ) : (
              <MarkdownContent content={content ?? ''} />
            )}
          </div>
        ) : (
          <div className={cx('doc-panel-empty')}>
            <span className={cx('doc-panel-orb')}>
              <FileTextOutlined />
            </span>
            <Text strong>{docName ? `${docName}待生成` : '文档将在此生成'}</Text>
            <Text type="secondary">完成当前阶段确认后，文档会生成在这里</Text>
          </div>
        )}
      </div>
    </div>
  )
}
