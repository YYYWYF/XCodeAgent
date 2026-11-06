import { CheckCircleOutlined } from '@ant-design/icons'
import { Button, Input, Modal, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { WorkflowConfirmationArtifact } from '../../../../typings'
import { cx } from '../../../../utils'
import ProjectPlanSummary from '../../../Welcome/ProjectPlanSummary'
import ConfirmationArtifact from './ConfirmationArtifact'
import './PlanConfirmationCard.less'

const { Text } = Typography

/** 规划文档确认卡片：展示产物状态并提交当前阶段支持的确认或修改操作。 */
export function PlanConfirmationCard({
  artifact,
  confirmed = false,
  disabled,
  onAbandon,
  onConfirm,
  onRevise,
  plan,
  planType,
  requiresConfirmation,
  revisionOnly = false,
  title
}: {
  artifact?: WorkflowConfirmationArtifact
  confirmed?: boolean
  disabled: boolean
  onAbandon: () => void
  onConfirm: () => void
  onRevise: (request: string) => void
  plan?: Record<string, unknown>
  planType: 'product' | 'technical' | 'project'
  /** 技术规划弹框「页面概览」版块用：交互/布局来自 productPlan。 */
  productPlan?: Record<string, unknown>
  requiresConfirmation: boolean
  /** 停止回执只开放新的修改请求，不提供旧产物确认动作。 */
  revisionOnly?: boolean
  /** 技术规划弹框「页面概览」版块用：页面风格从 spec 自由文本提取。 */
  spec?: Record<string, unknown>
  title: string
}): ReactElement {
  const [viewing, setViewing] = useState(false)
  const [revising, setRevising] = useState(false)
  const [revisionRequest, setRevisionRequest] = useState('')
  const canView = planType !== 'technical' && Boolean(artifact || plan)
  const canRevise = planType === 'technical'
  const technicalReview = planType === 'technical' && requiresConfirmation && !revisionOnly
  // 从当前审阅版本读取实际范围，资料未加载时不显示猜测的零计数。
  const pageCount = Array.isArray(plan?.pages) ? plan.pages.length : undefined
  const entityCount = Array.isArray(plan?.entities) ? plan.entities.length : undefined
  const contracts = Array.isArray(plan?.api_contracts) ? plan.api_contracts : undefined
  const endpointCount = contracts?.reduce((count: number, contract) =>
    count + (Array.isArray(contract?.endpoints) ? contract.endpoints.length : 0), 0)
  const documentLabel =
    planType === 'product' ? '需求文档' : planType === 'technical' ? '技术规划' : '项目计划书'

  /** 将技术规划确认行切换为行内输入，并清空上一轮未提交内容。 */
  const startRevision = (): void => {
    setRevisionRequest('')
    setRevising(true)
  }

  /** 仅把非空意见提交给既有 revise 协议，后端负责版本失效、重新生成和一致性校验。 */
  const submitRevision = (): void => {
    const request = revisionRequest.trim()
    if (!request) return
    setRevising(false)
    onRevise(request)
  }

  /** 取消行内修改并丢弃本地未提交内容，不触发任何工作流动作。 */
  const cancelRevision = (): void => {
    setRevising(false)
    setRevisionRequest('')
  }

  return (
    <div className={cx('artifact-auth-bar', 'project-plan-confirmation-card')}>
      {!confirmed && !revising ? (
        <div className={cx('requirement-spec-draft-summary')}>
          <div>
            <Text strong>{revisionOnly ? '已有技术规划已保留' : technicalReview ? '请审阅这份技术规划，作为后续开发依据' : `${title}已生成，已展示在右侧`}</Text>
          </div>
          <div>
            <Text type="secondary">{revisionOnly ? '可以继续提出修改意见，修改后需要重新确认。' : technicalReview ? '请在右侧核对页面与数据范围、接口定义及页面操作绑定。确认后进入开发准备。' : '请在右侧查看内容，确认无误后保存，也可以先提出修改意见。'}</Text>
          </div>
        </div>
      ) : null}
      {technicalReview && !revising && (pageCount !== undefined || entityCount !== undefined || endpointCount !== undefined) ? (
        <ul className={cx('technical-plan-review-scope')} aria-label="本次技术规划范围">
          {pageCount !== undefined ? <li><strong>{pageCount}</strong>个页面</li> : null}
          {entityCount !== undefined ? <li><strong>{entityCount}</strong>个数据实体</li> : null}
          {endpointCount !== undefined ? <li><strong>{endpointCount}</strong>个接口</li> : null}
        </ul>
      ) : null}
      <div className={cx('artifact-auth-bar-footer', revising && 'technical-plan-revision-inline')}>
        {revising ? (
          <div className={cx('technical-plan-revision-editor')}>
            <Input
              aria-label="技术规划修改意见"
              autoFocus
              disabled={disabled}
              onChange={(event) => setRevisionRequest(event.target.value)}
              onKeyDown={(event) => {
                // Escape 只退出本地编辑态，不触发后端修订。
                if (event.key === 'Escape') cancelRevision()
              }}
              placeholder="输入技术规划修改意见"
              value={revisionRequest}
            />
            <Button
              disabled={disabled || !revisionRequest.trim()}
              onClick={submitRevision}
              type="primary"
            >
              提交
            </Button>
            <Button onClick={cancelRevision}>取消</Button>
          </div>
        ) : (
          <>
            <span className={cx('artifact-auth-status')}>
              <CheckCircleOutlined aria-hidden="true" />
              {title}{revisionOnly ? '可继续修改' : confirmed ? '已确认' : '已生成'}
            </span>
            <span className={cx('artifact-auth-actions')}>
              {canView ? (
                <Button
                  className={cx('requirement-spec-edit-btn')}
                  onClick={() => setViewing(true)}
                >
                  查看{documentLabel}
                </Button>
              ) : null}
              {canRevise ? (
                <Button
                  className={cx('requirement-spec-edit-btn')}
                  disabled={disabled || confirmed}
                  onClick={startRevision}
                >
                  {technicalReview ? '提出修改' : '修改'}
                </Button>
              ) : !confirmed ? (
                <Button disabled={disabled} onClick={onAbandon}>
                  放弃
                </Button>
              ) : null}
              {!confirmed && !revisionOnly ? (
                <Button
                  disabled={disabled || !requiresConfirmation}
                  onClick={onConfirm}
                  type="primary"
                >
                  {technicalReview ? '确认技术规划' : '确认保存'}
                </Button>
              ) : null}
            </span>
          </>
        )}
      </div>
      <Modal
        cancelText="关闭"
        centered
        className={cx('requirement-spec-edit-modal')}
        footer={null}
        onCancel={() => setViewing(false)}
        open={viewing}
        title={documentLabel}
        width={920}
        destroyOnClose
      >
        {planType === 'product' && artifact ? (
          <ConfirmationArtifact artifact={artifact} />
        ) : plan ? (
          <ProjectPlanSummary plan={plan} />
        ) : artifact ? (
          <ConfirmationArtifact artifact={artifact} />
        ) : null}
      </Modal>
    </div>
  )
}
