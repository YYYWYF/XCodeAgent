import { Button, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningPageOption } from '../../typings'
import { cx } from '../../utils'
import './DetailBlockerCard.less'

const { Text } = Typography

type Props = {
  disabled?: boolean
  onStart?: (page: DevelopmentPlanningPageOption) => void
  selectedPage: DevelopmentPlanningPageOption
}

/** 开发阶段研发 Agent 流内挡板卡：选中待设计页面时在对话区展示。
 *  内容对齐原"尚未进行详细设计"弹窗：页面信息 + "开始详细设计"按钮。
 *  作为对话历史消息保留，点开始后不回退消失。 */
export default function DetailBlockerCard({
  disabled,
  onStart,
  selectedPage
}: Props): ReactElement {
  return (
    <div className={cx('detail-blocker-card')}>
      <div className={cx('detail-blocker-card-header')}>
        <div className={cx('detail-blocker-card-title')}>
          <span className={cx('detail-blocker-card-signal')} aria-hidden="true" />
          <Text className={cx('detail-blocker-card-name')} strong>
            页面详细设计
          </Text>
        </div>
        <Tag className={cx('detail-blocker-card-status')}>待设计</Tag>
      </div>

      <div className={cx('detail-blocker-card-message')}>
        <Text className={cx('detail-blocker-card-message-text')}>
          「{selectedPage.label || selectedPage.pageId}」尚未进行详细设计。为避免自由对话跳过页面需求，
          我将先综合应用需求与项目计划，为该页面生成页面需求文档，确认或补充后即可进入构建。
        </Text>
      </div>

      <div className={cx('detail-blocker-card-target')}>
        <div>
          <Text strong>{selectedPage.label || selectedPage.pageId}</Text>
          <Text code>{selectedPage.path || '/'}</Text>
        </div>
        <Text type="secondary">{selectedPage.purpose || ''}</Text>
      </div>

      <div className={cx('detail-blocker-card-actions')}>
        <Button
          className={cx('detail-blocker-card-action')}
          disabled={Boolean(disabled)}
          loading={Boolean(disabled)}
          onClick={() => onStart?.(selectedPage)}
          type="primary"
        >
          开始详细设计
        </Button>
      </div>
    </div>
  )
}
