import { FileTextOutlined } from '@ant-design/icons'
import { Alert, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import RichLoading from '../DesignProgress/RichLoading'
import RequirementDocPanel from './RequirementDocPanel'
import TechnicalPlanDocPanel from './TechnicalPlanDocPanel'
import './index.less'

const { Text } = Typography

/** 将 TechnicalPlan 的确认状态转换为顶部工具栏中的可读标签。 */
function technicalPlanStatusLabel(plan?: Record<string, unknown>): string {
  const status = plan?.confirmation_status
  return status === 'confirmed'
    ? '已确认'
    : status === 'pending_user_confirmation'
      ? '待确认'
      : '草稿'
}

/** 将 RequirementSpec 的确认状态转换为顶部工具栏中的可读标签。 */
function requirementStatusLabel(spec?: Record<string, unknown>): string {
  const status = spec?.confirmation_status
  return status === 'confirmed'
    ? '已确认'
    : status === 'pending_user_confirmation'
      ? '待确认'
      : '草稿'
}

type Props = {
  content?: string
  title?: string
  generating?: boolean
  error?: string
  docName?: string
  productPlan?: Record<string, unknown>
  requirementSpec?: Record<string, unknown>
  technicalPlan?: Record<string, unknown>
  structuredDocument?: 'requirement-doc' | 'technical-plan'
  structuredDocumentLoading?: boolean
}

/** 右侧产物面板：需求文档与技术规划使用结构化审核视图，其余文档只读展示 Markdown。 */
export default function DocPanel({
  content,
  title,
  generating,
  error,
  docName,
  productPlan,
  requirementSpec,
  technicalPlan,
  structuredDocument,
  structuredDocumentLoading
}: Props): ReactElement {
  const isTechnicalPlan = structuredDocument === 'technical-plan'
  const isRequirementDoc = structuredDocument === 'requirement-doc'
  const requirementReady =
    isRequirementDoc && Boolean(requirementSpec && Object.keys(requirementSpec).length)
  const ready = Boolean(content || technicalPlan || isTechnicalPlan || requirementReady || error)

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
        ) : isRequirementDoc ? (
          <div className={cx('doc-panel-path', 'doc-panel-technical-path')}>
            <FileTextOutlined aria-hidden="true" />
            <span>
              <strong>需求文档</strong>
              <small>{title?.split('/').pop() || 'requirement-spec.json'}</small>
            </span>
            {requirementSpec ? (
              <Tag className={cx('doc-panel-technical-status')}>
                {requirementStatusLabel(requirementSpec)}
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
        ) : error ? (
          <div className={cx('doc-panel-empty', 'doc-panel-error')}>
            <Alert message="文档读取失败" description={error} showIcon type="error" />
          </div>
        ) : ready ? (
          <div
            className={cx(
              'doc-panel-viewer',
              isTechnicalPlan || requirementReady ? 'doc-panel-structured' : 'doc-panel-markdown'
            )}
          >
            {structuredDocumentLoading ? (
              <div className={cx('doc-panel-generating')}>
                <RichLoading bare title="正在读取结构化文档…" />
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
            ) : requirementReady ? (
              <RequirementDocPanel productPlan={productPlan || {}} spec={requirementSpec || {}} />
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
